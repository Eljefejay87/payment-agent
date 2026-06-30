#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.payment-agent"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Started $LABEL"
