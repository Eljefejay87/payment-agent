from __future__ import annotations

import sys

from agents.payment_agent.main import main
from agents.dashboard.main import main as dashboard_main
from agents.weekly_remit_agent.main import main as remit_main
from agents.voicemail_tracker_agent.main import main as voicemail_main
from agents.operations_intelligence_agent.main import main as operations_main
from agents.cash_flow_hq.main import main as cash_flow_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        sys.exit(dashboard_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("voicemail-"):
        sys.argv[1] = sys.argv[1].replace("voicemail-", "", 1)
        sys.exit(voicemail_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("remit-"):
        sys.exit(remit_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("ops-"):
        sys.exit(operations_main())
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith("cash-flow-") or sys.argv[1].startswith("cashflow-")
    ):
        sys.exit(cash_flow_main())
    if len(sys.argv) > 1 and sys.argv[1] == "debug-remit-files":
        sys.exit(remit_main())
    sys.exit(main())
