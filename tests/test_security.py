from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.envbuild import ALLOWED_KEYS
from app.precheck import load_token
from app.server import create_app
from app.transcript import SYSTEM_PROMPT
from tests.conftest import ORIGIN, World, csrf_of

SENTINEL = "UNIQUE_PROMPT_XYZ --dangerously-skip-permissions"


def _chat(world: World, csrf: str, content: str, messages: list[dict] | None = None):
    body = {"messages": messages or [{"role": "user", "content": content}]}
    return world.client.post("/api/chat", headers=world.headers(csrf), json=body)


def test_prompt_not_in_argv_and_env_is_clean(world: World):
    csrf = csrf_of(world)
    response = _chat(world, csrf, SENTINEL)
    assert response.status_code == 200, response.text
    assert "你好" in response.text
    report = world.report()
    assert SENTINEL not in report["argv"]
    assert SENTINEL not in " ".join(report["argv"])
    assert "--bare" not in report["argv"]
    assert "--tools" in report["argv"]
    assert report["argv"][report["argv"].index("--tools") + 1] == ""
    assert "*" in report["argv"]
    assert "mcp__*" in report["argv"]
    assert "--restricted" in report["argv"]
    assert "--safe-mode" in report["argv"]
    assert "--no-session-persistence" in report["argv"]
    assert SYSTEM_PROMPT in report["argv"]
    assert SENTINEL in report["stdin"]
    assert json.loads(report["stdin"])["message"]["content"].count(SENTINEL) == 1
    assert set(report["env"]) <= ALLOWED_KEYS
    assert "ANTHROPIC_API_KEY" not in report["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in report["env"]
    assert "CLAUDE_CODE_USE_BEDROCK" not in report["env"]
    assert "CLAUDE_CODE_USE_VERTEX" not in report["env"]
    assert "CLAUDE_CODE_USE_FOUNDRY" not in report["env"]
    assert "CLAUDE_CODE_SIMPLE" not in report["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in report["env"]
    assert "sentinel-bin" not in report["env"]["PATH"]
    assert report["env"]["HOME"] == str(world.settings.home)
    assert report["mcp"] == '{"mcpServers":{}}\n'
    assert report["cwd"] != str(world.settings.home)
    assert not Path(report["cwd"]).exists()
    assert "sk-ant-" not in response.text


def test_auth_origin_and_csrf(world: World):
    denied = world.client.post(
        "/api/chat",
        headers={"Origin": ORIGIN},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert denied.status_code == 401

    wrong = world.client.post(
        "/api/chat",
        headers={"Authorization": "Bearer not-the-token", "Origin": ORIGIN},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert wrong.status_code == 401

    csrf = csrf_of(world)
    foreign = world.client.post(
        "/api/chat",
        headers={**world.headers(csrf), "Origin": "http://evil.example:8765"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"] == "origin_rejected"

    cross = world.client.post(
        "/api/chat",
        headers={**world.headers(csrf), "Sec-Fetch-Site": "cross-site"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert cross.status_code == 403

    world.client.cookies.set("local_chat_csrf", "not-the-cookie")
    bad_cookie = world.client.post(
        "/api/chat",
        headers=world.headers(csrf),
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert bad_cookie.status_code == 403
    assert bad_cookie.json()["error"] == "csrf_rejected"


def test_tools_mcp_and_errors_do_not_leak(world: World):
    csrf = csrf_of(world)
    world.write_mode({"mode": "bad_init", "init_patch": {"tools": ["Bash"]}})
    tools = _chat(world, csrf, "hello")
    assert tools.status_code == 502
    assert tools.json()["error"] == "tools_not_empty"
    assert "LEAKED_TOOL_TEXT" not in tools.text
    assert "Bash" not in tools.text

    world.write_mode(
        {"mode": "bad_init", "init_patch": {"mcp_servers": [{"name": "box", "status": "connected"}]}}
    )
    mcp = _chat(world, csrf, "hello")
    assert mcp.status_code == 502
    assert mcp.json()["error"] == "mcp_not_empty"

    world.write_mode({"mode": "bad_init", "omit": ["tools"]})
    missing = _chat(world, csrf, "hello")
    assert missing.status_code == 502
    assert missing.json()["error"] == "tools_unconfirmed"

    world.write_mode({"mode": "bad_init", "init_patch": {"apiKeySource": "ANTHROPIC_API_KEY"}})
    billing = _chat(world, csrf, "hello")
    assert billing.status_code == 502
    assert billing.json()["error"] == "billing_source"
    assert "test-api-key-value" not in billing.text

    world.write_mode({"mode": "hooks"})
    hooks = _chat(world, csrf, "hello")
    assert hooks.status_code == 502
    assert hooks.json()["error"] == "hooks_detected"

    world.write_mode({"mode": "tool_later"})
    later = _chat(world, csrf, "hello")
    assert later.status_code == 200
    assert "tool_use_detected" in later.text
    assert "LEAKED_TOOL_TEXT" not in later.text

    world.write_mode({"mode": "error_result"})
    failed = _chat(world, csrf, "hello")
    assert "cli_failed" in failed.text
    assert "SECRETLEAK" not in failed.text

    world.write_mode({"mode": "crash"})
    crashed = _chat(world, csrf, "hello")
    assert crashed.status_code == 502
    assert "SECRETLEAK" not in crashed.text


def test_timeout_kills_process(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from dataclasses import replace

    settings = replace(world.settings, timeout_sec=0.4)
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        boxed = World(
            client=client,
            token=world.token,
            mock=world.mock,
            tmp=world.tmp,
            settings=settings,
        )
        csrf = csrf_of(boxed)
        boxed.write_mode({"mode": "sleep", "seconds": 30})
        started = time.monotonic()
        response = _chat(boxed, csrf, "hello")
        elapsed = time.monotonic() - started
        assert response.status_code == 504
        assert response.json()["error"] == "timeout"
        assert elapsed < 5
        pid = boxed.report()["pid"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("mock process was not reaped")


def test_single_flight(world: World, tmp_path: Path):
    from dataclasses import replace

    port = 18766
    settings = replace(world.settings, port=port, timeout_sec=5)
    app = create_app(settings)
    origin = f"http://127.0.0.1:{port}"
    started = tmp_path / "started"
    release = tmp_path / "release"
    world.write_mode({"mode": "hold", "started": str(started), "release": str(release)})
    config = uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            try:
                probe = TestClient(app, base_url=origin)
                break
            except Exception:
                time.sleep(0.05)
        import urllib.request

        ready = False
        for _ in range(50):
            try:
                urllib.request.urlopen(origin + "/health", timeout=0.2)
                ready = True
                break
            except Exception:
                time.sleep(0.05)
        assert ready
        token = world.token

        def call():
            import httpx

            with httpx.Client(base_url=origin, timeout=5) as http:
                session = http.get("/api/session", headers={"Authorization": f"Bearer {token}", "Origin": origin})
                csrf = session.json()["csrf"]
                return http.post(
                    "/api/chat",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Origin": origin,
                        "X-CSRF-Token": csrf,
                    },
                    json={"messages": [{"role": "user", "content": "hold"}]},
                )

        first = threading.Thread(target=lambda: results.append(call()))
        results: list = []
        first.start()
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists()
        import httpx

        with httpx.Client(base_url=origin, timeout=5) as http:
            session = http.get("/api/session", headers={"Authorization": f"Bearer {token}", "Origin": origin})
            csrf = session.json()["csrf"]
            second = http.post(
                "/api/chat",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                },
                json={"messages": [{"role": "user", "content": "other"}]},
            )
        assert second.status_code == 429
        release.write_text("go", encoding="utf-8")
        first.join(timeout=5)
        assert results and results[0].status_code == 200
    finally:
        release.write_text("go", encoding="utf-8")
        server.should_exit = True
        thread.join(timeout=5)


def test_history_and_length(world: World):
    csrf = csrf_of(world)
    response = _chat(
        world,
        csrf,
        "现在",
        [
            {"role": "user", "content": "第一句"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "现在"},
        ],
    )
    assert response.status_code == 200
    stdin = world.report()["stdin"]
    assert stdin.index("第一句") < stdin.index("第一答") < stdin.index("现在")
    assert "[用户]" in stdin and "[助手]" in stdin
    too_long = _chat(world, csrf, "x" * 8001)
    assert too_long.status_code == 422
    assert too_long.json()["error"] == "too_long"


def test_page_and_logs(world: World, caplog: pytest.LogCaptureFixture):
    caplog.set_level("INFO", logger="localchat")
    page = world.client.get("/")
    assert page.status_code == 200
    assert "本机对话" in page.text
    assert "出门以后" in page.text
    assert "innerHTML" not in Path("web/app.js").read_text(encoding="utf-8")
    csrf = csrf_of(world)
    _chat(world, csrf, SENTINEL)
    text = caplog.text
    assert SENTINEL not in text
    assert world.token not in text
    assert "test-api-key-value" not in text


def test_token_file_mode(tmp_path: Path):
    path = tmp_path / "auth.token"
    path.write_text("a" * 40, encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(Exception):
        load_token(path)
    path.chmod(0o600)
    assert load_token(path) == "a" * 40
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(Exception):
        load_token(link)
