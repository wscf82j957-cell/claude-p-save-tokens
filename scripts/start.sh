#!/usr/bin/env bash
# 前台用户自己运行。不会注册开机启动，不会禁止睡眠，不会改系统设置。
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

if [[ "$(id -u)" -eq 0 ]]; then
  echo "请用普通用户运行，不要用 root。" >&2
  exit 1
fi

LAN=0
for arg in "$@"; do
  case "$arg" in
    --lan) LAN=1 ;;
    *)
      echo "只接受 --lan。未知参数：$arg" >&2
      exit 2
      ;;
  esac
done

if [[ ! -x .venv/bin/python ]]; then
  echo "请先运行 bash scripts/install.sh" >&2
  exit 1
fi
if [[ ! -f runtime/auth.token ]]; then
  echo "缺少 runtime/auth.token。请先安装。" >&2
  exit 1
fi
chmod 600 runtime/auth.token

if [[ "$LAN" -eq 1 ]]; then
  export LAN_MODE=1
  export BIND_HOST=0.0.0.0
else
  export LAN_MODE=0
  export BIND_HOST=127.0.0.1
fi
export PORT="${PORT:-8765}"

if ! .venv/bin/python -m app.precheck; then
  exit 1
fi

mkdir -p runtime
PIDFILE=runtime/server.pid
LOG=runtime/server.log
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "服务已在运行。先执行 bash scripts/stop.sh。" >&2
  exit 1
fi

nohup .venv/bin/python -m app >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"
chmod 600 "$PIDFILE"

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
  if .venv/bin/python -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=0.5)' "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.2
done

if [[ "$ready" -ne 1 ]]; then
  echo "没有启动成功。日志最后几行：" >&2
  tail -n 40 "$LOG" >&2 || true
  if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  exit 1
fi

if [[ -t 1 ]]; then
  printf '本机口令（不要转发）：%s\n' "$(cat runtime/auth.token)"
fi

if [[ "$LAN" -eq 1 ]]; then
  echo "同一 Wi-Fi 的手机可以用下面的地址。电脑必须开着、醒着、能上网。"
  PORT="$PORT" .venv/bin/python -c 'import os
from app.server import local_ipv4s
ips = local_ipv4s()
port = os.environ["PORT"]
if not ips:
    print("没有发现局域网地址。可以设置 LAN_ORIGINS=http://这台电脑的地址:端口")
else:
    for ip in ips:
        print(f"http://{ip}:{port}/")'
  echo "出门后这些地址不能用。本程序不做穿透、VPN 或公网部署。"
else
  echo "先在这台电脑的浏览器打开：http://127.0.0.1:${PORT}/"
  echo "手机现在连不上。同一 Wi-Fi 要先 bash scripts/stop.sh，再 bash scripts/start.sh --lan。"
  echo "人在外面时，本机地址和同一 Wi-Fi 地址都不够，需要另外配置安全路径。这里不提供。"
fi
echo "停止：bash scripts/stop.sh。重启电脑后不会自己启动。"
