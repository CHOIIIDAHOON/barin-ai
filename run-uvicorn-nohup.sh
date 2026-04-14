#!/usr/bin/env bash
# When invoked as `sh run-uvicorn-nohup.sh`, dash ignores the shebang and lacks pipefail.
if [ -z "${BASH_VERSION-}" ]; then
  exec /usr/bin/env bash "$0" "$@"
fi
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${UVICORN_PORT:-8000}"
LOG="${NOHUP_LOG:-$ROOT/logs/nohup-uvicorn.log}"
PIDFILE="${UVICORN_PIDFILE:-$ROOT/uvicorn.$PORT.pid}"

mkdir -p "$(dirname "$LOG")"

stop_pidfile() {
  [[ ! -f "$PIDFILE" ]] && return 0
  local old
  old="$(tr -d ' \n\r\t' <"$PIDFILE" 2>/dev/null || true)"
  [[ -z "$old" ]] && { rm -f "$PIDFILE"; return 0; }
  if kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null || true
    sleep 1
    if kill -0 "$old" 2>/dev/null; then
      kill -9 "$old" 2>/dev/null || true
    fi
  fi
  rm -f "$PIDFILE"
}

free_port() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" 2>/dev/null || true
    return 0
  fi
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

if [[ ! -x "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "Missing $ROOT/.venv/bin/uvicorn — create venv and: pip install -r requirements.txt" >&2
  exit 1
fi

stop_pidfile
free_port "$PORT"
sleep 0.5

nohup "$ROOT/.venv/bin/uvicorn" main:app --host "$HOST" --port "$PORT" \
  >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "uvicorn PID $(cat "$PIDFILE") (host=$HOST port=$PORT, log=$LOG, pidfile=$PIDFILE)"
