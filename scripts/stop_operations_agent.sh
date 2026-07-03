#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.operations-intelligence-agent"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
echo "Stopped $LABEL"
