#!/bin/zsh
set -euo pipefail

LABEL="com.ucm.cash-flow-hq"
launchctl print "gui/$(id -u)/$LABEL"
echo
echo "Runtime logs:"
echo "  $HOME/Library/Application Support/UCM/cash-flow-hq/logs/cash-flow-hq.out.log"
echo "  $HOME/Library/Application Support/UCM/cash-flow-hq/logs/cash-flow-hq.err.log"
