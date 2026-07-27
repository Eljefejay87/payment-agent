#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/UCM/payment-agent"
PLIST_TARGET_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/logs" "$INSTALL_DIR/locks" "$PLIST_TARGET_DIR"

rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  --exclude ".env" \
  --exclude ".graph_teams_token_cache.bin" \
  --exclude "logs" \
  --exclude "remits" \
  --exclude "reports" \
  --exclude "screenshots" \
  --exclude "work" \
  --exclude "database" \
  --exclude "*.sqlite3" \
  --exclude "*.sqlite3-*" \
  --exclude "*.sqlite3-wal" \
  --exclude "*.sqlite3-shm" \
  --exclude "payment_agent_health.json" \
  --exclude "voicemail_status.json" \
  --exclude "voicemail_runtime_state.json" \
  --exclude "voicemail_health.json" \
  "$PROJECT_DIR/" "$INSTALL_DIR/"

if [[ -f "$PROJECT_DIR/.env" && ! -f "$INSTALL_DIR/.env" ]]; then
  cp "$PROJECT_DIR/.env" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi

"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/prepare_one_shot_launch_agents.py" \
  --job payment \
  --output-directory "$PLIST_TARGET_DIR" \
  --project-root "$INSTALL_DIR" \
  --python "$INSTALL_DIR/.venv/bin/python" \
  --log-directory "$INSTALL_DIR/logs" \
  --lock-directory "$INSTALL_DIR/locks"

chmod 644 "$PLIST_TARGET_DIR/com.ucm.payment-agent.plist"

echo "Prepared com.ucm.payment-agent one-shot runtime and plist without loading launchctl."
