#!/usr/bin/env bash
# 只创建本目录里的虚拟环境和口令文件。
# 不登录、不读取 Claude 凭据、不建系统账户、不改睡眠、不设置开机启动。
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

if [[ "$(id -u)" -eq 0 ]]; then
  echo "请用普通用户运行，不要用 root。" >&2
  exit 1
fi

choose_python() {
  local candidate version major minor
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
      major="${version%%.*}"
      minor="${version#*.}"
      if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(choose_python)"; then
  echo "需要 Python 3.11 或更新。这台电脑上没找到。" >&2
  exit 1
fi

"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

mkdir -p runtime
if [[ ! -f runtime/auth.token ]]; then
  .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > runtime/auth.token
fi
chmod 600 runtime/auth.token

echo "安装完成。服务还没有启动，系统设置也没有改。"
echo "口令文件：runtime/auth.token（权限 600）。"
echo "在这台电脑上查看它，再亲手填进页面。不要提交，不要发到别处。"
echo "下一步：bash scripts/start.sh"
