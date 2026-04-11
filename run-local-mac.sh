#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Repo the agent sees (default: this project folder).
export CURSOR_PROJECT_DIR="${CURSOR_PROJECT_DIR:-$ROOT}"

# Cursor.app 번들 안의 CLI (PATH에 cursor 없을 때)
if ! command -v cursor >/dev/null 2>&1; then
  MAC_CURSOR="/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
  if [[ -x "$MAC_CURSOR" ]]; then
    export CURSOR_CLI_PATH="$MAC_CURSOR"
    echo "Using CURSOR_CLI_PATH=$CURSOR_CLI_PATH"
  else
    echo "Install Cursor.app or add cursor to PATH." >&2
    exit 1
  fi
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  echo "Warning: CURSOR_API_KEY is not set. Headless agent often needs it; set in .env or export." >&2
  echo "  Dashboard: https://cursor.com → API / CLI keys" >&2
fi

if [[ ! -d "$ROOT/.venv" ]]; then
  python3 -m venv .venv
  "$ROOT/.venv/bin/pip" install -r requirements.txt
fi

# 127.0.0.1: 이 Mac만 접속. 같은 Wi‑Fi의 안드로이드에서 쓰려면:
#   UVICORN_HOST=0.0.0.0 ./run-local-mac.sh
# 그다음 Mac의 사설 IP로 요청 (예: http://192.168.0.12:8000/chat).
HOST="${UVICORN_HOST:-127.0.0.1}"
PORT="${UVICORN_PORT:-8000}"
exec "$ROOT/.venv/bin/uvicorn" main:app --host "$HOST" --port "$PORT" --reload
