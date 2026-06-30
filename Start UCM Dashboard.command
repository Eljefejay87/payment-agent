#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/jcollins/Documents/AI AGENT UCM/payment-agent"
cd "$PROJECT_DIR"

"$PROJECT_DIR/.venv/bin/python" main.py dashboard

