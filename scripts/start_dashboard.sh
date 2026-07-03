#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/jcollins/Documents/AI AGENT UCM/payment-agent"
cd "$PROJECT_DIR"

PORT="${DASHBOARD_PORT:-8080}"

if command -v lsof >/dev/null 2>&1; then
  EXISTING_PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$EXISTING_PIDS" ]; then
    echo "Stopping existing dashboard on port $PORT..."
    kill $EXISTING_PIDS 2>/dev/null || true
    sleep 1
    STILL_RUNNING="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$STILL_RUNNING" ]; then
      kill -9 $STILL_RUNNING 2>/dev/null || true
      sleep 1
    fi
  fi
fi

export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
export DASHBOARD_PORT="$PORT"

exec "$PROJECT_DIR/.venv/bin/python" main.py dashboard
