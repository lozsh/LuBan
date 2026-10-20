#!/usr/bin/env bash
# LubanAgent 一键启动：后端 API + 前端工作台
# 服务用 nohup 脱离会话进程组，不归 Claude background task 跟踪，不会被自动回收。
# 用法：scripts/dev.sh [start|stop|status|restart|logs api|ui]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT_API=8000
PORT_UI=5173
VENV_PY="$ROOT/.venv/bin/python"
VENV_BIN="$ROOT/.venv/bin"
UI_DIR="$ROOT/lubanagent/ui"
LOG_API=/tmp/luban-uvicorn.log
LOG_UI=/tmp/luban-ui.log
PID_API=/tmp/luban-uvicorn.pid
PID_UI=/tmp/luban-ui.pid

alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
pidof_file() {
  [ -f "$1" ] || return 1
  local p; p=$(cat "$1" 2>/dev/null || true)
  [ -n "$p" ] || return 1
  alive "$p" && echo "$p" || return 1
}

start_api() {
  local pid; pid=$(pidof_file "$PID_API" 2>/dev/null || true)
  if [ -n "$pid" ]; then echo "✓ 后端已在跑 (pid $pid)"; return; fi
  "$VENV_PY" -c "import fastapi,uvicorn,langchain,pypdf,docx" 2>/dev/null || {
    echo "→ 安装依赖..."; uv pip install --python "$VENV_PY" -e ".[dev]" >/dev/null
  }
  [ -f "$ROOT/.env" ] || cp "$ROOT/app/config/.env.example" "$ROOT/.env"
  "$VENV_BIN/luban" migrate >/dev/null 2>&1 || true
  # --fill-missing：自动启动只补缺失 Agent，磁盘旧 YAML 不打回工作台修改（H08）
  "$VENV_BIN/luban" init --fill-missing >/dev/null 2>&1 || true
  nohup "$VENV_BIN/uvicorn" app.main:create_app --factory \
    --host 127.0.0.1 --port "$PORT_API" > "$LOG_API" 2>&1 &
  local npid=$!; echo "$npid" > "$PID_API"; disown 2>/dev/null || true
  echo "→ 后端启动 (pid $npid) → http://localhost:$PORT_API"
}

start_ui() {
  local pid; pid=$(pidof_file "$PID_UI" 2>/dev/null || true)
  if [ -n "$pid" ]; then echo "✓ 前端已在跑 (pid $pid)"; return; fi
  [ -f "$UI_DIR/.env" ] || printf 'VITE_API_BASE=\nVITE_USE_MOCK=false\n' > "$UI_DIR/.env"
  [ -d "$UI_DIR/node_modules" ] || (cd "$UI_DIR" && npm install)
  nohup bash -c "cd '$UI_DIR' && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port $PORT_UI --strictPort" > "$LOG_UI" 2>&1 &
  local npid=$!; echo "$npid" > "$PID_UI"; disown 2>/dev/null || true
  echo "→ 前端启动 (pid $(cat "$PID_UI")) → http://localhost:$PORT_UI"
}

wait_port() {
  local port="$1" i
  for i in $(seq 1 60); do
    curl -sf -o /dev/null "http://localhost:$port/" 2>/dev/null && return 0
    curl -sf -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null && return 0
    sleep 0.5
  done
  return 1
}

start() {
  start_api
  start_ui
  echo "→ 等待端口就绪..."
  wait_port "$PORT_API" || { echo "✗ 后端未就绪，日志："; tail -20 "$LOG_API"; return 1; }
  wait_port "$PORT_UI" || { echo "✗ 前端未就绪，日志："; tail -20 "$LOG_UI"; return 1; }
  echo
  echo "✓ 全部就绪"
  echo "  工作台  http://localhost:$PORT_UI/"
  echo "  API     http://localhost:$PORT_API/v1/agents"
  echo "  health  http://localhost:$PORT_API/health"
  curl -s "http://localhost:$PORT_API/health"; echo
}

kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}
stop_one() {
  local pidfile="$1" name="$2" pid
  pid=$(pidof_file "$pidfile" 2>/dev/null || true)
  if [ -n "$pid" ]; then kill_tree "$pid"; echo "✓ $name 已停 (pid $pid)"; else echo "· $name 未在跑"; fi
  rm -f "$pidfile"
}
stop() { stop_one "$PID_UI" "前端"; stop_one "$PID_API" "后端"; }

status() {
  local a u
  a=$(pidof_file "$PID_API" 2>/dev/null || true)
  u=$(pidof_file "$PID_UI" 2>/dev/null || true)
  [ -n "$a" ] && echo "后端 :$PORT_API 跑中 (pid $a)" || echo "后端 未跑"
  [ -n "$u" ] && echo "前端 :$PORT_UI 跑中 (pid $u)" || echo "前端 未跑"
}

logs() {
  local t="${2:-api}"
  case "$t" in
    api|uvicorn) tail -f "$LOG_API" ;;
    ui|vite) tail -f "$LOG_UI" ;;
    *) echo "用法: $0 logs api|ui" ;;
  esac
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs "$@" ;;
  *) echo "用法: $0 [start|stop|status|restart|logs api|ui]"; exit 1 ;;
esac
