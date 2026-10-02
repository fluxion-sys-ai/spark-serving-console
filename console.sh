#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SESSION=spark-serving-console
export SPARK_BUNDLE=${SPARK_BUNDLE:-"$HOME/ai/spark-dual-models"}
export SPARK_CONSOLE_DATA=${SPARK_CONSOLE_DATA:-"$ROOT/var"}
export PYTHONDONTWRITEBYTECODE=1 MAX_JOBS=1

mkdir -p "$SPARK_CONSOLE_DATA"
chmod 700 "$SPARK_CONSOLE_DATA"
if [[ -n "${SPARK_CONSOLE_PORT:-}" ]]; then
  PORT=$SPARK_CONSOLE_PORT
elif [[ -s "$SPARK_CONSOLE_DATA/port" ]]; then
  PORT=$(<"$SPARK_CONSOLE_DATA/port")
else
  PORT=18080
fi
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT > 0 && PORT < 65536 )) || { echo 'SPARK_CONSOLE_PORT must be a valid TCP port.' >&2; exit 2; }

health() {
  "$ROOT/.venv/bin/python" - "$PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
with urllib.request.urlopen('http://127.0.0.1:' + sys.argv[1] + '/health', timeout=2) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

show_url() {
  local token_file="$SPARK_CONSOLE_DATA/access-token"
  if [[ -s "$token_file" ]]; then
    echo "Authenticated URL: http://127.0.0.1:$PORT/#access_token=$(<"$token_file")"
  else
    echo "Console URL: http://127.0.0.1:$PORT (access token is not ready yet)"
  fi
}

case "${1:-status}" in
  start)
    [[ -x "$ROOT/.venv/bin/python" ]] || { echo 'Run ./setup.sh first.' >&2; exit 1; }
    [[ -f "$SPARK_BUNDLE/start.sh" ]] || { echo "Missing model launcher: $SPARK_BUNDLE" >&2; exit 1; }
    if tmux has-session -t "=$SESSION" 2>/dev/null; then
      if health; then
        echo "Console is already running in tmux: $SESSION"
        show_url
        exit 0
      fi
      echo "The tmux session exists but its health check failed. Inspect $SPARK_CONSOLE_DATA/console.log" >&2
      exit 1
    fi
    printf '%s\n' "$PORT" >"$SPARK_CONSOLE_DATA/port"
    chmod 600 "$SPARK_CONSOLE_DATA/port"
    printf -v CMD '%q ' env "SPARK_BUNDLE=$SPARK_BUNDLE" "SPARK_CONSOLE_DATA=$SPARK_CONSOLE_DATA" PYTHONDONTWRITEBYTECODE=1 MAX_JOBS=1 \
      "$ROOT/.venv/bin/python" -m uvicorn spark_console.app:create_app --factory --host 127.0.0.1 --port "$PORT" --no-access-log
    printf -v LOG '%q' "$SPARK_CONSOLE_DATA/console.log"
    tmux new-session -d -s "$SESSION" -c "$ROOT" "exec $CMD >>$LOG 2>&1"
    for _ in $(seq 1 45); do
      if health; then
        echo "Console ready in tmux: $SESSION"
        show_url
        echo "Logs: $SPARK_CONSOLE_DATA/console.log"
        exit 0
      fi
      if ! tmux has-session -t "=$SESSION" 2>/dev/null; then
        echo 'Console exited before becoming healthy. Recent log:' >&2
        tail -n 40 "$SPARK_CONSOLE_DATA/console.log" >&2 || true
        exit 1
      fi
      sleep 1
    done
    echo "Console did not become healthy within 45 seconds. Inspect $SPARK_CONSOLE_DATA/console.log" >&2
    exit 1
    ;;
  stop)
    if tmux has-session -t "=$SESSION" 2>/dev/null; then tmux kill-session -t "=$SESSION"; fi
    echo 'Web console stopped. Model services remain running.'
    ;;
  status)
    [[ -x "$ROOT/.venv/bin/python" ]] || { echo 'Console is not set up. Run ./setup.sh first.'; exit 1; }
    if tmux has-session -t "=$SESSION" 2>/dev/null; then
      if health; then
        echo "Console is healthy in tmux: $SESSION"
        show_url
      else
        echo "Console tmux session is running, but the health check on port $PORT failed." >&2
        exit 1
      fi
    else
      echo 'Console is stopped.'
    fi
    ;;
  *) echo 'Usage: ./console.sh start|stop|status' >&2; exit 2 ;;
esac
