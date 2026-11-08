"""运行配置。绑定地址只从环境读取，不从网页请求读取。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.precheck import PrecheckError


_ORIGIN = re.compile(r"http://[A-Za-z0-9.\-]+:(\d+)$")


@dataclass(frozen=True)
class Settings:
    claude_bin: str | None
    host: str
    port: int
    lan: bool
    token_file: Path
    work_root: Path
    timeout_sec: float
    extra_origins: tuple[str, ...]
    home: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        lan = env.get("LAN_MODE", "0").strip() == "1"
        host = env.get("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
        if lan and host != "0.0.0.0":
            raise PrecheckError("同一网络模式必须同时把 BIND_HOST 设为 0.0.0.0。")
        if not lan and host not in {"127.0.0.1", "localhost"}:
            raise PrecheckError(
                "默认只监听 127.0.0.1。要让手机走同一 Wi-Fi，请用 scripts/start.sh --lan。"
            )
        try:
            port = int(env.get("PORT", "8765"))
        except ValueError as exc:
            raise PrecheckError("PORT 必须是数字。") from exc
        if port < 1 or port > 65535:
            raise PrecheckError("PORT 超出范围。")
        try:
            timeout = float(env.get("REQUEST_TIMEOUT", "120"))
        except ValueError as exc:
            raise PrecheckError("REQUEST_TIMEOUT 必须是数字。") from exc
        timeout = min(180.0, max(5.0, timeout))
        home = Path(env.get("HOME", "")).expanduser()
        if not home.is_dir():
            raise PrecheckError("HOME 不可用，订阅登录需要当前用户的主目录。")
        extra = _parse_origins(env.get("LAN_ORIGINS", ""), port, lan)
        root = Path(env.get("WORK_ROOT", "runtime/work"))
        token = Path(env.get("AUTH_TOKEN_FILE", "runtime/auth.token"))
        return cls(
            claude_bin=env.get("CLAUDE_BIN") or None,
            host=host,
            port=port,
            lan=lan,
            token_file=token,
            work_root=root,
            timeout_sec=timeout,
            extra_origins=extra,
            home=home,
        )


def _parse_origins(raw: str, port: int, lan: bool) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    if not lan:
        raise PrecheckError("LAN_ORIGINS 只在 --lan 时生效。")
    found: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        match = _ORIGIN.fullmatch(item)
        if match is None or int(match.group(1)) != port:
            raise PrecheckError(f"LAN_ORIGINS 里有不能用的地址：{item}")
        found.append(item)
    return tuple(found)
