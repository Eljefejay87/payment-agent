#!/bin/zsh
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.ucm.shared-data-sync.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "Uninstalled com.ucm.shared-data-sync"
