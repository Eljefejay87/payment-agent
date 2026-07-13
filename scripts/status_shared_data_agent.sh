#!/bin/zsh
set -euo pipefail
launchctl print "gui/$(id -u)/com.ucm.shared-data-sync"
