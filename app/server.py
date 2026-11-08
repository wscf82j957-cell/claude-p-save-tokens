"""本机页面和 /api/chat。默认只配合 127.0.0.1。"""

from __future__ import annotations

import hmac
import logging
import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.failures import ChatFailure
from app.precheck import PrecheckError, load_token, resolve_claude
from app.runner import Runner, is_hook, next_event, reject_assistant_tools, text_from_delta, usage_numbers
from app.settings import Settings
from app.transcript import parse_messages, render_transcript

WEB = Path(__file__).resolve().parent.parent / "web"
logger = logging.getLogger("localchat")
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)


class _Redact(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if (self.secret and self.secret in message) or "Bearer " in message or "sk-ant-" in message:
            record.msg = "已省略含口令或凭据的日志"
            record.args = ()
        return True


def local_ipv4s() -> list[str]:
    found: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
    except OSError:
        address = ""
    finally:
        sock.close()
    if address and not address.startswith("127."):
        found.add(address)
    return sorted(found)


def allowed_origins(settings: Settings) -> set[str]:
    origins = {f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"}
    if settings.lan:
        origins.update(f"http://{ip}:{settings.port}" for ip in local_ipv4s())
        origins.update(settings.extra_origins)
    return origins


def bind_allowed(server_host: str | None, lan: bool) -> bool:
    if not server_host or server_host in {"127.0.0.1", "localhost", "::1"}:
        return True
    return lan


def _equal(left: str, right: str) -> bool:
    left_b = left.encode()
    right_b = right.encode()
    if len(left_b) != len(right_b):
        return False
    return hmac.compare_digest(left_b, right_b)


def _json_error(failure: ChatFailure) -> JSONResponse:
    return JSONResponse(
        {"error": failure.code, "message": failure.message},
        status_code=failure.http_status,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            token = load_token(settings.token_file)
            env = child_env_for(settings)
            app.state.claude = str(resolve_claude(settings.home, settings.claude_bin, dict(__import__("os").environ), env))
            app.state.token = token
        except PrecheckError as exc:
            logger.error("启动检查没有通过：%s", exc.__class__.__name__)
            raise
        app.state.csrf = secrets.token_urlsafe(32)
        app.state.runner = Runner(home=settings.home, work_root=settings.work_root, timeout_sec=settings.timeout_sec)
        app.state.busy = False
        logger.filters = [_Redact(token)]
        logger.info("ready host=%s port=%s lan=%s", settings.host, settings.port, settings.lan)
        yield
        await app.state.runner.shutdown()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def guard(request: Request, call_next):
        server = request.scope.get("server")
        host = server[0] if server else None
        if not bind_allowed(host, settings.lan):
            return JSONResponse(
                {"error": "bind_rejected", "message": "没有显式开启同一网络模式，拒绝在更大范围上提供服务。"},
                status_code=403,
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(WEB / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def style() -> FileResponse:
        return FileResponse(WEB / "style.css", media_type="text/css")

    @app.get("/api/session")
    async def session(request: Request) -> JSONResponse:
        failure = _auth(request, need_csrf=False)
        if failure is not None:
            return _json_error(failure)
        response = JSONResponse({"ok": True, "csrf": request.app.state.csrf})
        response.set_cookie(
            "local_chat_csrf",
            request.app.state.csrf,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
            max_age=12 * 60 * 60,
        )
        return response

    @app.post("/api/chat")
    async def chat(request: Request):
        failure = _auth(request, need_csrf=True)
        if failure is not None:
            logger.info("chat rejected code=%s", failure.code)
            return _json_error(failure)
        if request.app.state.busy:
            return _json_error(ChatFailure("busy", 429, "上一条还没结束。"))
        request.app.state.busy = True
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > 200_000:
            request.app.state.busy = False
            return _json_error(ChatFailure("too_long", 413, "内容太长。"))
        try:
            payload = await request.json()
            messages = parse_messages(payload)
            transcript = render_transcript(messages)
            proc, work, deadline = await request.app.state.runner.open_turn(request.app.state.claude, transcript)
        except ChatFailure as exc:
            request.app.state.busy = False
            logger.info("chat rejected code=%s", exc.code)
            return _json_error(exc)
        except Exception:
            request.app.state.busy = False
            return _json_error(ChatFailure("bad_request", 422, "请求格式不对。"))

        async def generate():
            sent = 0
            saw_text = False
            try:
                while True:
                    event = await next_event(proc, deadline)
                    if event is None:
                        break
                    if is_hook(event):
                        yield _sse({"error": "hooks_detected", "message": "检测到钩子，已停止。"})
                        return
                    try:
                        reject_assistant_tools(event)
                        piece = text_from_delta(event)
                    except ChatFailure as exc:
                        yield _sse({"error": exc.code, "message": exc.message})
                        return
                    if piece is not None:
                        if sent + len(piece) > MAX_OUT:
                            yield _sse({"error": "output_limit", "message": "回复过长，已停止。"})
                            return
                        sent += len(piece)
                        saw_text = True
                        yield _sse({"delta": piece})
                        continue
                    if event.get("type") == "result":
                        if event.get("is_error") or str(event.get("subtype", "")).startswith("error"):
                            yield _sse({"error": "cli_failed", "message": "调用没有完成，已停止。"})
                            return
                        usage = usage_numbers(event)
                        if usage:
                            yield _sse({"usage": usage})
                        if not saw_text and isinstance(event.get("result"), str) and event.get("result"):
                            text = event["result"]
                            if len(text) > MAX_OUT:
                                yield _sse({"error": "output_limit", "message": "回复过长，已停止。"})
                                return
                            yield _sse({"delta": text})
                        break
                yield _sse({"done": True})
            except ChatFailure as exc:
                yield _sse({"error": exc.code, "message": exc.message})
            finally:
                await request.app.state.runner.finish(proc, work)
                request.app.state.busy = False

        logger.info("chat stream")
        return StreamingResponse(generate(), media_type="text/event-stream")

    def _auth(request: Request, *, need_csrf: bool) -> ChatFailure | None:
        try:
            token = load_token(settings.token_file)
        except PrecheckError:
            return ChatFailure("unauthorized", 401, "口令不可用。")
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not _equal(header[7:], token):
            return ChatFailure("unauthorized", 401, "口令不对。")
        origin = request.headers.get("origin")
        if origin not in allowed_origins(settings):
            return ChatFailure("origin_rejected", 403, "页面来源不被允许。")
        site = request.headers.get("sec-fetch-site")
        if site not in {None, "", "same-origin", "none"}:
            return ChatFailure("origin_rejected", 403, "页面来源不被允许。")
        if need_csrf:
            csrf_header = request.headers.get("x-csrf-token", "")
            csrf_cookie = request.cookies.get("local_chat_csrf", "")
            secret = request.app.state.csrf
            if not (_equal(csrf_header, secret) and _equal(csrf_cookie, secret)):
                return ChatFailure("csrf_rejected", 403, "页面校验没有通过，请重新连接。")
        return None

    return app


def child_env_for(settings: Settings) -> dict[str, str]:
    from app.envbuild import child_env

    binary = settings.claude_bin or "claude"
    return child_env(dict(__import__("os").environ), home=settings.home, claude_bin=binary)


def _sse(payload: dict) -> str:
    import json

    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


MAX_OUT = 64000


def _build_default_app() -> FastAPI:
    try:
        return create_app()
    except PrecheckError as exc:
        # 导入时只保存错误，真正监听前由启动脚本再检查一次。
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        app.state.startup_error = str(exc)

        @app.middleware("http")
        async def refuse(request: Request, call_next):
            return JSONResponse({"error": "precheck_failed", "message": "启动检查没有通过。"}, status_code=503)

        return app


app = _build_default_app()
