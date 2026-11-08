"""启动一次 claude -p。用户内容只写进标准输入。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

from app.envbuild import child_env
from app.failures import ChatFailure
from app.transcript import SYSTEM_PROMPT

MAX_OUTPUT_CHARS = 64000
MAX_LINE_CHARS = 1_000_000
MCP_CONFIG = '{"mcpServers":{}}\n'

# 官方 CLI / Agent SDK 文档里的参数。不用 --bare，也不设置 CLAUDE_CODE_SIMPLE。
# --tools 的空字符串才会关掉内置工具；none 不是这个开关。
# --disallowed-tools 同时去掉 MCP。--restricted 不加载用户/项目设置。
# --safe-mode 关掉钩子、技能、插件和 CLAUDE.md，同时仍会读取订阅登录。


def build_argv(claude_bin: str, mcp_path: str) -> list[str]:
    return [
        claude_bin,
        "--print",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools",
        "",
        "--disallowed-tools",
        "*",
        "--disallowed-tools",
        "mcp__*",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--mcp-config",
        mcp_path,
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--max-turns",
        "1",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--system-prompt",
        SYSTEM_PROMPT,
    ]


def stdin_payload(transcript: str) -> bytes:
    message = {
        "type": "user",
        "message": {"role": "user", "content": transcript},
        "parent_tool_use_id": None,
    }
    return json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"


class Runner:
    def __init__(self, *, home: Path, work_root: Path, timeout_sec: float) -> None:
        self.home = home
        self.work_root = work_root
        self.timeout_sec = timeout_sec
        self._proc: asyncio.subprocess.Process | None = None

    async def shutdown(self) -> None:
        proc = self._proc
        if proc is not None:
            await kill_process(proc)
            self._proc = None

    async def open_turn(self, claude_bin: str, transcript: str) -> tuple[asyncio.subprocess.Process, Path, float]:
        self.work_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.work_root, 0o700)
        work = Path(tempfile.mkdtemp(prefix="turn-", dir=self.work_root))
        os.chmod(work, 0o700)
        mcp_path = work / "mcp.json"
        mcp_path.write_text(MCP_CONFIG, encoding="utf-8")
        os.chmod(mcp_path, 0o600)
        env = child_env(dict(os.environ), home=self.home, claude_bin=claude_bin)
        argv = build_argv(claude_bin, str(mcp_path))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=work,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            shutil.rmtree(work, ignore_errors=True)
            raise ChatFailure("cli_failed", 502, "没有启动 claude。") from exc
        self._proc = proc
        assert proc.stdin is not None and proc.stderr is not None
        proc.stdin.write(stdin_payload(transcript))
        await proc.stdin.drain()
        proc.stdin.close()
        await proc.stdin.wait_closed()
        asyncio.create_task(_drain(proc.stderr))
        deadline = time.monotonic() + self.timeout_sec
        try:
            await self._wait_for_clean_init(proc, deadline)
        except ChatFailure:
            await self.finish(proc, work)
            raise
        return proc, work, deadline

    async def finish(self, proc: asyncio.subprocess.Process, work: Path) -> None:
        await kill_process(proc)
        if self._proc is proc:
            self._proc = None
        shutil.rmtree(work, ignore_errors=True)

    async def _wait_for_clean_init(self, proc: asyncio.subprocess.Process, deadline: float) -> None:
        while True:
            event = await next_event(proc, deadline)
            if event is None:
                raise ChatFailure("cli_failed", 502, "claude 没有完成启动确认。")
            if is_hook(event):
                raise ChatFailure("hooks_detected", 502, "检测到钩子，已停止。")
            if event.get("type") == "system" and event.get("subtype") == "init":
                check_init(event)
                return
            raise ChatFailure("cli_failed", 502, "启动确认没有通过，已停止。")


def check_init(event: dict[str, Any]) -> None:
    tools = event.get("tools")
    if not isinstance(tools, list):
        raise ChatFailure("tools_unconfirmed", 502, "没有确认工具列表是空的，已停止。")
    if tools:
        raise ChatFailure("tools_not_empty", 502, "工具没有全部关掉，已停止。")
    servers = event.get("mcp_servers")
    if not isinstance(servers, list):
        raise ChatFailure("mcp_unconfirmed", 502, "没有确认 MCP 是空的，已停止。")
    if servers:
        raise ChatFailure("mcp_not_empty", 502, "仍加载了 MCP，已停止。")
    plugins = event.get("plugins", [])
    if not isinstance(plugins, list) or plugins:
        raise ChatFailure("plugins_loaded", 502, "仍加载了插件，已停止。")
    skills = event.get("skills", [])
    agents = event.get("agents", [])
    if not isinstance(skills, list) or skills or not isinstance(agents, list) or agents:
        raise ChatFailure("customizations_loaded", 502, "仍加载了技能或代理，已停止。")
    source = event.get("apiKeySource")
    if source is not None and source != "none":
        raise ChatFailure("billing_source", 502, "当前不是订阅登录，已停止，避免改走 API 或云厂商计费。")
    mode = event.get("permissionMode")
    if mode is not None and mode not in {"dontAsk", "default", "plan"}:
        raise ChatFailure("permission_mode", 502, "权限模式不符合预期，已停止。")


def is_hook(event: dict[str, Any]) -> bool:
    subtype = event.get("subtype")
    if event.get("type") == "system" and isinstance(subtype, str) and subtype.startswith("hook"):
        return True
    return event.get("type") in {"hook_started", "hook_progress", "hook_response"}


async def next_event(proc: asyncio.subprocess.Process, deadline: float) -> dict[str, Any] | None:
    assert proc.stdout is not None
    left = deadline - time.monotonic()
    if left <= 0:
        raise ChatFailure("timeout", 504, "超时，已停止。")
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), left)
    except asyncio.TimeoutError as exc:
        raise ChatFailure("timeout", 504, "超时，已停止。") from exc
    if not line:
        return None
    if len(line) > MAX_LINE_CHARS:
        raise ChatFailure("output_limit", 502, "上游输出过长，已停止。")
    try:
        event = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChatFailure("cli_failed", 502, "上游输出无法识别，已停止。") from exc
    if not isinstance(event, dict):
        raise ChatFailure("cli_failed", 502, "上游输出无法识别，已停止。")
    return event


def text_from_delta(event: dict[str, Any]) -> str | None:
    if event.get("type") != "stream_event":
        return None
    inner = event.get("event")
    if not isinstance(inner, dict):
        return None
    if inner.get("type") == "content_block_start":
        block = inner.get("content_block")
        if isinstance(block, dict) and block.get("type") == "tool_use":
            raise ChatFailure("tool_use_detected", 502, "出现了工具调用，已停止。")
        return None
    if inner.get("type") != "content_block_delta":
        return None
    delta = inner.get("delta")
    if not isinstance(delta, dict):
        return None
    kind = delta.get("type")
    if kind == "input_json_delta":
        raise ChatFailure("tool_use_detected", 502, "出现了工具调用，已停止。")
    if kind != "text_delta":
        return None
    text = delta.get("text")
    if not isinstance(text, str) or text == "":
        return None
    return text


def usage_numbers(event: dict[str, Any]) -> dict[str, int] | None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    picked: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            picked[key] = value
    return picked or None


def reject_assistant_tools(event: dict[str, Any]) -> None:
    if event.get("type") != "assistant":
        return
    message = event.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            raise ChatFailure("tool_use_detected", 502, "出现了工具调用，已停止。")


async def kill_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except asyncio.TimeoutError:
        return


async def _drain(stream: asyncio.StreamReader) -> None:
    try:
        while await stream.read(4096):
            pass
    except Exception:
        return
