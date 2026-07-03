#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.operations-intelligence-agent"
launchctl print "gui/$(id -u)/$LABEL"
echo
echo "Runtime logs:"
echo "  $HOME/Library/Application Support/UCM/operations-intelligence-agent/logs/operations-agent.out.log"
echo "  $HOME/Library/Application Support/UCM/operations-intelligence-agent/logs/operations-agent.err.log"
