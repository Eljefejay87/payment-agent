#!/usr/bin/env bash
set -euo pipefail

SERVICE_ROLE="${RAILWAY_SERVICE_ROLE:-payment-agent}"

case "$SERVICE_ROLE" in
	payment-agent|payment_agent|default|"")
		python main.py init-db
		exec python main.py run
		;;
	cash-flow-hq-bridge|cash_flow_hq_bridge|bridge)
		exec python main.py cash-flow-bridge
		;;
	*)
		echo "Unsupported RAILWAY_SERVICE_ROLE: $SERVICE_ROLE" >&2
		echo "Supported values: payment-agent, cash-flow-hq-bridge" >&2
		exit 2
		;;
esac
