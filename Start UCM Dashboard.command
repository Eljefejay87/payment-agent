#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/jcollins/Documents/AI AGENT UCM/payment-agent"
cd "$PROJECT_DIR"

exec "$PROJECT_DIR/scripts/start_dashboard.sh"
