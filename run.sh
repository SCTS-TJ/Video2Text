#!/bin/bash
# Video2Text 启动脚本 (阶段一: 采集 + Web 预览)
# 用法:
#   bash run.sh          # 启动 (默认, 脱离终端, 关窗口不断)
#   bash run.sh start    # 启动
#   bash run.sh stop     # 停止
#   bash run.sh restart  # 重启
set -e
cd "$(dirname "$0")"
. .venv/bin/activate

PORT=8137
LOG="$PWD/.uvicorn.log"
PIDFILE="$PWD/.v2t.pid"

export HTTP_PROXY=http://192.168.121.44:7890
export HTTPS_PROXY=http://192.168.121.44:7890

is_running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

stop() {
  if is_running; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && echo "[停止] PID $(cat "$PIDFILE")"
  fi
  pkill -f "uvicorn app:app" 2>/dev/null && echo "[停止] 清理残留进程" || true
  rm -f "$PIDFILE"
}

start() {
  # 先清残留, 避免旧 127.0.0.1 进程挡局域网
  pkill -f "uvicorn app:app" 2>/dev/null || true
  sleep 1
  # nohup 忽略 SIGHUP + 后台 + disown: 完全脱离终端, 关窗口/断 SSH 服务不断
  # (macOS 无 setsid, 用 nohup+disown 等效)
  nohup .venv/bin/uvicorn app:app --host 0.0.0.0 --port "$PORT" \
    > "$LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  echo "$pid" > "$PIDFILE"
  sleep 2
  if is_running; then
    echo "==> 已启动 (PID $(cat "$PIDFILE"))"
    echo "    本机:   http://127.0.0.1:${PORT}"
    echo "    局域网: http://$(ipconfig getifaddr en0 2>/dev/null || echo '本机IP'):${PORT}"
    echo "    日志:   $LOG"
  else
    echo "[失败] 启动异常, 查看日志:"; tail -10 "$LOG"
    exit 1
  fi
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  *) echo "用法: bash run.sh [start|stop|restart]"; exit 1 ;;
esac
