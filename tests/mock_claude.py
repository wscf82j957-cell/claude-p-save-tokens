#!/usr/bin/env python3
"""测试用的假 claude。只按旁边的 mock_mode.json 行事，不联网。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODE_PATH = HERE / "mock_mode.json"
REPORT_PATH = HERE / "mock_report.json"

HELP = """
--print
--input-format
--output-format
--include-partial-messages
--tools
--disallowed-tools
--restricted
--safe-mode
--strict-mcp-config
--mcp-config
--permission-mode
--permission-prompts
--max-turns
--no-session-persistence
--disable-slash-commands
--system-prompt
"""


def load_mode() -> dict:
    if not MODE_PATH.is_file():
        return {"mode": "ok", "version": "2.1.259"}
    return json.loads(MODE_PATH.read_text(encoding="utf-8"))


def base_init() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "tools": [],
        "mcp_servers": [],
        "plugins": [],
        "skills": [],
        "agents": [],
        "apiKeySource": "none",
        "permissionMode": "dontAsk",
        "session_id": "mock-session",
        "cwd": os.getcwd(),
        "model": "mock",
    }


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def write_report(stdin_text: str) -> None:
    mcp = ""
    if "--mcp-config" in sys.argv:
        mcp_path = sys.argv[sys.argv.index("--mcp-config") + 1]
        mcp = Path(mcp_path).read_text(encoding="utf-8")
    report = {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "env": dict(os.environ),
        "stdin": stdin_text,
        "pid": os.getpid(),
        "mcp": mcp,
    }
    REPORT_PATH.write_text(json.dumps(report), encoding="utf-8")


def emit_ok(text: str) -> None:
    emit(base_init())
    emit(
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "usage": {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 0},
        }
    )


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        sys.stdout.write(HELP)
        return 0
    mode = load_mode()
    if "--version" in args:
        sys.stdout.write(str(mode.get("version", "2.1.259")) + "\n")
        return 0
    stdin_text = sys.stdin.read()
    write_report(stdin_text)
    kind = mode.get("mode", "ok")
    if kind == "sleep":
        time.sleep(float(mode.get("seconds", 30)))
        return 0
    if kind == "crash":
        sys.stderr.write("sk-ant-SECRETLEAK\n")
        return 2
    if kind == "hooks":
        emit({"type": "system", "subtype": "hook_started"})
        return 0
    if kind == "hold":
        emit(base_init())
        Path(mode["started"]).write_text(str(os.getpid()), encoding="utf-8")
        release = Path(mode["release"])
        while not release.exists():
            time.sleep(0.05)
        emit_ok("等到了")
        return 0
    init = base_init()
    init.update(mode.get("init_patch") or {})
    for key in mode.get("omit") or []:
        init.pop(key, None)
    if kind == "bad_init":
        emit(init)
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "LEAKED_TOOL_TEXT"},
                },
            }
        )
        return 0
    if kind == "tool_later":
        emit(base_init())
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "content_block": {"type": "tool_use", "name": "Bash"},
                },
            }
        )
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "LEAKED_TOOL_TEXT"},
                },
            }
        )
        return 0
    if kind == "error_result":
        emit(base_init())
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "errors": ["sk-ant-SECRETLEAK"],
            }
        )
        return 0
    emit_ok(str(mode.get("text", "你好")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
