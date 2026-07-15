from __future__ import annotations

import sys

from agents.payment_agent.main import main
from agents.dashboard.main import main as dashboard_main
from agents.weekly_remit_agent.main import main as remit_main
from agents.voicemail_tracker_agent.main import main as voicemail_main
from agents.operations_intelligence_agent.main import main as operations_main
from agents.cash_flow_hq.main import main as cash_flow_main
from agents.icr_remit_agent.main import main as icr_remit_main
from agents.chief_of_staff.main import main as chief_of_staff_main
from agents.chargeback_tracker.main import main as chargeback_main
from shared.data_layer.main import main as shared_data_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chief-of-staff":
        del sys.argv[1]
        sys.exit(chief_of_staff_main())
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        sys.exit(dashboard_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("chargeback-"):
        sys.exit(chargeback_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("shared-data-"):
        sys.exit(shared_data_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("voicemail-"):
        sys.argv[1] = sys.argv[1].replace("voicemail-", "", 1)
        sys.exit(voicemail_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("remit-"):
        sys.exit(remit_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("icr-remit-"):
        sys.exit(icr_remit_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("ops-"):
        sys.exit(operations_main())
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith("cash-flow-") or sys.argv[1].startswith("cashflow-")
    ):
        sys.exit(cash_flow_main())
    if len(sys.argv) > 1 and sys.argv[1] == "debug-remit-files":
        sys.exit(remit_main())
    sys.exit(main())
