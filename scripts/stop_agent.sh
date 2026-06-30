#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.payment-agent"
launchctl kill TERM "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
echo "Stopped $LABEL"
