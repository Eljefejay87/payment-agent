#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.payment-agent"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
INSTALL_DIR="$HOME/Library/Application Support/UCM/payment-agent"

launchctl bootout "gui/$(id -u)" "$PLIST_TARGET" >/dev/null 2>&1 || true
rm -f "$PLIST_TARGET"

echo "Uninstalled $LABEL"
echo "Runtime files were left in place:"
echo "  $INSTALL_DIR"
