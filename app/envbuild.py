"""交给 claude 的环境只保留登录和运行需要的项。

官方说明：`-p` 只要看见 `ANTHROPIC_API_KEY` 就会改用 API 计费；
`CLAUDE_CODE_USE_BEDROCK`、`CLAUDE_CODE_USE_VERTEX`、`CLAUDE_CODE_USE_FOUNDRY`
会改走云厂商。`CLAUDE_CODE_SIMPLE=1` 等于 `--bare`，而 bare 不读取订阅登录。
这里用白名单，不把父进程环境原样传下去。
"""

from __future__ import annotations

import os
from pathlib import Path

ALLOWED_KEYS = frozenset({"HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "PATH", "TERM"})


def build_path(claude_bin: str) -> str:
    parts: list[str] = []
    bindir = str(Path(claude_bin).resolve().parent)
    for directory in (bindir, "/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin", "/usr/local/bin"):
        if directory not in parts and os.path.isdir(directory):
            parts.append(directory)
    return ":".join(parts)


def utf8_locale(parent: dict[str, str]) -> str:
    for key in ("LC_ALL", "LANG"):
        value = parent.get(key, "")
        upper = value.upper()
        if "UTF-8" in upper or "UTF8" in upper:
            return value
    return "en_US.UTF-8"


def child_env(parent: dict[str, str], *, home: Path, claude_bin: str) -> dict[str, str]:
    locale = utf8_locale(parent)
    tmp = parent.get("TMPDIR", "")
    if not tmp.startswith("/") or not os.path.isdir(tmp):
        tmp = "/tmp"
    env = {
        "HOME": str(home),
        "LANG": locale,
        "LC_ALL": locale,
        "PATH": build_path(claude_bin),
        "TERM": "dumb",
        "TMPDIR": tmp,
    }
    for key in ("USER", "LOGNAME"):
        value = parent.get(key, "")
        if value and "\n" not in value and "\r" not in value:
            env[key] = value
    return {key: value for key, value in env.items() if key in ALLOWED_KEYS}
