from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.envbuild import child_env
from app.precheck import PrecheckError, candidate_paths, inspect_binary, resolve_claude
from app.settings import Settings


def test_search_order_covers_both_mac_layouts(tmp_path: Path):
    home = tmp_path / "home"
    arm = tmp_path / "opt" / "homebrew" / "bin" / "claude"
    intel = tmp_path / "usr" / "local" / "bin" / "claude"
    paths = candidate_paths(
        home=home,
        path_env=str(tmp_path / "pathbin"),
        env_bin=None,
        fallbacks=(arm, intel),
    )
    assert paths[0] == tmp_path / "pathbin" / "claude"
    assert paths[1] == home / ".local" / "bin" / "claude"
    assert paths[2] == arm
    assert paths[3] == intel
    only = candidate_paths(home=home, path_env="", env_bin="/tmp/chosen", fallbacks=(arm,))
    assert only == [Path("/tmp/chosen")]


def test_symlink_launcher_passes_and_old_version_fails(tmp_path: Path):
    source = Path(__file__).resolve().parent / "mock_claude.py"
    real = tmp_path / "real"
    real.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    real.chmod(0o755)
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    link = link_dir / "claude"
    link.symlink_to(real)
    # mock 用 __file__.resolve()，会跟到符号链接指向的文件旁边。
    mode = tmp_path / "mock_mode.json"
    mode.write_text('{"version":"2.1.259"}', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = child_env({"LANG": "en_US.UTF-8"}, home=home, claude_bin=str(link))
    found = resolve_claude(home, None, {"PATH": str(link_dir)}, env)
    assert found == link

    mode.write_text('{"version":"2.1.200"}', encoding="utf-8")
    with pytest.raises(PrecheckError, match="2.1.259"):
        inspect_binary(link, env)


def test_missing_flag_is_rejected(tmp_path: Path):
    binary = tmp_path / "claude"
    binary.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "--version) echo 2.1.300 ;;\n"
        "*) echo no-flags ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    binary.chmod(stat.S_IRWXU)
    home = tmp_path / "home"
    home.mkdir()
    env = child_env({}, home=home, claude_bin=str(binary))
    with pytest.raises(PrecheckError, match="--restricted"):
        inspect_binary(binary, env)


def test_bind_defaults_and_lan_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("BIND_HOST", raising=False)
    monkeypatch.delenv("LAN_MODE", raising=False)
    monkeypatch.delenv("LAN_ORIGINS", raising=False)
    settings = Settings.from_env()
    assert settings.host == "127.0.0.1"
    assert settings.lan is False

    monkeypatch.setenv("BIND_HOST", "0.0.0.0")
    with pytest.raises(PrecheckError, match="127.0.0.1"):
        Settings.from_env()

    monkeypatch.setenv("LAN_MODE", "1")
    monkeypatch.setenv("LAN_ORIGINS", "http://192.0.2.10:8765")
    opened = Settings.from_env()
    assert opened.lan is True
    assert opened.host == "0.0.0.0"
    assert opened.extra_origins == ("http://192.0.2.10:8765",)


def test_scripts_do_not_change_the_system():
    for name in ("install.sh", "start.sh", "stop.sh"):
        text = Path("scripts", name).read_text(encoding="utf-8")
        for forbidden in ("caffeinate", "pmset", "launchctl", "sudo", "sysadminctl", "dscl"):
            assert forbidden not in text
        assert "set -euo pipefail" in text
