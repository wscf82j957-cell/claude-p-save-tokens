from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest


def _listen_addresses(port: int) -> list[str]:
    found: list[str] = []
    hex_port = f"{port:04X}"
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        path = Path(name)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split()
            local, state = parts[1], parts[3]
            ip_hex, port_hex = local.split(":")
            if port_hex.upper() != hex_port or state != "0A":
                continue
            if len(ip_hex) == 8:
                raw = bytes.fromhex(ip_hex)
                found.append(".".join(str(byte) for byte in raw[::-1]))
    return found


@pytest.mark.skipif(os.name != "posix", reason="脚本用 bash")
def test_start_stop_loopback_on_this_linux(tmp_path: Path):
    """在当前 Linux 上用假 claude 走一遍启动和停止。没有在 macOS 上跑过。"""

    if not Path(".venv/bin/python").exists():
        pytest.skip("还没有虚拟环境")
    mock = tmp_path / "claude"
    shutil.copy(Path("tests/mock_claude.py"), mock)
    mock.chmod(0o755)
    (tmp_path / "mock_mode.json").write_text('{"mode":"ok","version":"2.1.259"}', encoding="utf-8")
    runtime = Path("runtime")
    runtime.mkdir(exist_ok=True)
    token_path = runtime / "auth.token"
    created = not token_path.exists()
    if created:
        token_path.write_text("t" * 40, encoding="utf-8")
        token_path.chmod(0o600)
    env = os.environ.copy()
    env["CLAUDE_BIN"] = str(mock)
    env["PORT"] = "18765"
    env.pop("LAN_MODE", None)
    env.pop("BIND_HOST", None)
    try:
        started = subprocess.run(
            ["bash", "scripts/start.sh"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        assert "t" * 40 not in started.stdout
        assert "127.0.0.1:18765" in started.stdout
        assert "外面" in started.stdout
        page = urllib.request.urlopen("http://127.0.0.1:18765/", timeout=2).read()
        assert "本机对话".encode() in page
        listeners = _listen_addresses(18765)
        assert "127.0.0.1" in listeners
        assert "0.0.0.0" not in listeners
    finally:
        subprocess.run(["bash", "scripts/stop.sh"], check=False)
        if created:
            token_path.unlink(missing_ok=True)
