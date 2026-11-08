"""核对已安装的 claude 是否带有本次要用的官方参数。"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

MIN_VERSION = (2, 1, 259)

# 这些字符串必须出现在 `claude --help` 里。版本下限来自官方文档：
# --restricted 需要 2.1.248，--permission-prompts 需要 2.1.259。
REQUIRED_HELP = (
    "--print",
    "--input-format",
    "--output-format",
    "--include-partial-messages",
    "--tools",
    "--restricted",
    "--safe-mode",
    "--strict-mcp-config",
    "--mcp-config",
    "--permission-mode",
    "--permission-prompts",
    "--max-turns",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--system-prompt",
)

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class PrecheckError(Exception):
    """启动前就能确定的配置或客户端问题。"""


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def help_gaps(help_text: str) -> list[str]:
    missing = [flag for flag in REQUIRED_HELP if flag not in help_text]
    if "--disallowed-tools" not in help_text and "--disallowedTools" not in help_text:
        missing.append("--disallowed-tools")
    return missing


def candidate_paths(
    *,
    home: Path,
    path_env: str,
    env_bin: str | None,
    fallbacks: tuple[Path, ...] | None = None,
) -> list[Path]:
    """查找顺序：CLAUDE_BIN、PATH、官方原生目录、Apple Silicon Homebrew、Intel Homebrew。

    设置了 CLAUDE_BIN 时只使用这一处，避免悄悄换到另一个程序。
    """

    if env_bin:
        return [Path(env_bin)]
    if fallbacks is None:
        fallbacks = (Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude"))
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key not in seen:
            seen.add(key)
            found.append(path)

    for directory in path_env.split(":"):
        if directory:
            add(Path(directory) / "claude")
    add(home / ".local" / "bin" / "claude")
    for path in fallbacks:
        add(path)
    return found


def executable_candidates(candidates: list[Path]) -> list[Path]:
    ready: list[Path] = []
    for path in candidates:
        # 官方安装器把 ~/.local/bin/claude 做成指向 versions/ 的符号链接，所以这里允许链接。
        if path.is_file() and os.access(path, os.X_OK):
            ready.append(path)
    return ready


def inspect_binary(path: Path, env: dict[str, str]) -> None:
    try:
        help_proc = subprocess.run(
            [str(path), "--help"],
            capture_output=True,
            timeout=15,
            env=env,
            check=False,
        )
        version_proc = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            timeout=15,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrecheckError(f"无法执行 {path} 的版本检查。") from exc
    help_text = (help_proc.stdout + help_proc.stderr).decode("utf-8", "replace")
    gaps = help_gaps(help_text)
    if gaps:
        raise PrecheckError(f"{path} 的帮助里没有这些参数：{', '.join(gaps)}")
    version_text = (version_proc.stdout + version_proc.stderr).decode("utf-8", "replace")
    version = parse_version(version_text)
    if version is None:
        raise PrecheckError(f"读不到 {path} 的版本号。")
    if version < MIN_VERSION:
        current = ".".join(str(part) for part in version)
        needed = ".".join(str(part) for part in MIN_VERSION)
        raise PrecheckError(f"需要 Claude Code {needed} 或更新，当前是 {current}。")


def load_token(path: Path) -> str:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise PrecheckError("找不到口令文件 runtime/auth.token。请先运行 scripts/install.sh。") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PrecheckError("口令文件必须是普通文件，不能是链接。")
    if info.st_mode & 0o077:
        raise PrecheckError("口令文件权限过宽。请执行 chmod 600 runtime/auth.token。")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32 or any(char.isspace() for char in token):
        raise PrecheckError("口令文件内容不可用。")
    return token


def resolve_claude(home: Path, env_bin: str | None, parent_env: dict[str, str], child_env: dict[str, str]) -> Path:
    candidates = candidate_paths(home=home, path_env=parent_env.get("PATH", ""), env_bin=env_bin)
    executables = executable_candidates(candidates)
    if not executables:
        looked = "、".join(str(path) for path in candidates)
        raise PrecheckError(f"没有找到可执行的 claude。已查找：{looked}")
    errors: list[str] = []
    for path in executables:
        try:
            inspect_binary(path, child_env)
        except PrecheckError as exc:
            errors.append(str(exc))
            if env_bin:
                break
            continue
        return path
    raise PrecheckError("没有通过版本和参数检查的 claude。\n" + "\n".join(errors))


def main() -> int:
    import os
    import sys

    from app.envbuild import child_env
    from app.settings import Settings

    try:
        settings = Settings.from_env()
        load_token(settings.token_file)
        parent = dict(os.environ)
        env = child_env(parent, home=settings.home, claude_bin=settings.claude_bin or "claude")
        path = resolve_claude(settings.home, settings.claude_bin, parent, env)
    except PrecheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"claude: {path}")
    if settings.lan:
        print("同一网络模式会监听 0.0.0.0，仍要口令、来源和 CSRF。")
    else:
        print("只监听 127.0.0.1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
