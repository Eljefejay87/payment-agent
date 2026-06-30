#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.payment-agent"
launchctl print "gui/$(id -u)/$LABEL"
