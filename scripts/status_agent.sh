#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.payment-agent"
launchctl print "gui/$(id -u)/$LABEL"
echo
echo "Runtime logs:"
echo "  $HOME/Library/Application Support/UCM/payment-agent/logs/payment-agent.out.log"
echo "  $HOME/Library/Application Support/UCM/payment-agent/logs/payment-agent.err.log"
