#!/usr/bin/env bash
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
LOG="${NOHUP_LOG:-$ROOT/nohup-uvicorn.log}"

if [[ ! -x "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "Missing $ROOT/.venv/bin/uvicorn — create venv and: pip install -r requirements.txt" >&2
  exit 1
fi

nohup "$ROOT/.venv/bin/uvicorn" main:app --host "$HOST" --port "$PORT" \
  >>"$LOG" 2>&1 &
echo "uvicorn PID $! (host=$HOST port=$PORT, log=$LOG)"
