#!/usr/bin/env bash
# One-time server prep for chatbot-api (Ubuntu, run with sudo).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/chatbot-api}"
PROJECT_DIR="${PROJECT_DIR:-/var/cursor-project}"
RUN_USER="${RUN_USER:-cursor-chat}"
RUN_GROUP="${RUN_GROUP:-cursor-chat}"
RUN_HOME="${RUN_HOME:-/var/lib/cursor-chat}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with: sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "$APP_DIR/main.py" ]]; then
  echo "Expected $APP_DIR/main.py — copy the app to $APP_DIR first." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip

if ! id -u "$RUN_USER" &>/dev/null; then
  useradd --system --home "$RUN_HOME" --create-home --shell /usr/sbin/nologin "$RUN_USER"
fi

mkdir -p "$PROJECT_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$PROJECT_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$APP_DIR"

sudo -u "$RUN_USER" bash <<EOF
set -euo pipefail
cd "$APP_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt
EOF

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo ""
  echo ">>> Create $APP_DIR/.env (see .env.example). Required: CURSOR_API_KEY, CURSOR_CLI_PATH, etc."
fi

echo ""
echo "Done. Next:"
echo "  sudo cp $APP_DIR/deploy/cursor-chat-api.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now cursor-chat-api"
