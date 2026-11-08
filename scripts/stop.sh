#!/usr/bin/env bash
# 只停止本目录记录的服务进程。不改系统设置。
set -euo pipefail
cd "$(dirname "$0")/.."

PIDFILE=runtime/server.pid
if [[ ! -f "$PIDFILE" ]]; then
  echo "没有在运行。"
  exit 0
fi

pid="$(cat "$PIDFILE")"
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PIDFILE"
  echo "进程已经不在。"
  exit 0
fi

args="$(ps -p "$pid" -o args=)"
case "$args" in
  *\ -m\ app*|*/app/__main__.py*) ;;
  *)
    echo "拒绝停止：这个 pid 不是本服务。" >&2
    exit 1
    ;;
esac

kill "$pid" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "已停止。"
    exit 0
  fi
  sleep 0.2
done

kill -9 "$pid" 2>/dev/null || true
rm -f "$PIDFILE"
echo "已强制停止。"
