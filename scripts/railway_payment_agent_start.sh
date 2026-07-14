#!/usr/bin/env bash
set -euo pipefail

python main.py init-db
exec python main.py run
