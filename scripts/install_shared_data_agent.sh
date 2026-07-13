#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/UCM/payment-agent"
LABEL="com.ucm.shared-data-sync"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)" "$PLIST_TARGET" >/dev/null 2>&1 || true
mkdir -p "$HOME/Library/LaunchAgents" "$INSTALL_DIR" "$INSTALL_DIR/logs"

rsync -a \
  --exclude ".git" --exclude ".venv" --exclude "__pycache__" --exclude "*.pyc" \
  --exclude "logs" --exclude "reports" --exclude "screenshots" --exclude "work" \
  --exclude "*.sqlite3" --exclude "*.sqlite3-wal" --exclude "*.sqlite3-shm" \
  "$PROJECT_DIR/" "$INSTALL_DIR/"

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

cat > "$PLIST_TARGET" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$INSTALL_DIR/.venv/bin/python</string><string>main.py</string><string>shared-data-run</string>
  </array>
  <key>WorkingDirectory</key><string>$INSTALL_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$INSTALL_DIR/logs/shared-data-sync.out.log</string>
  <key>StandardErrorPath</key><string>$INSTALL_DIR/logs/shared-data-sync.err.log</string>
</dict></plist>
PLIST

chmod 644 "$PLIST_TARGET"
launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Installed and started $LABEL"
