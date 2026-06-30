from __future__ import annotations

import sys

from agents.payment_agent.main import main
from agents.dashboard.main import main as dashboard_main
from agents.weekly_remit_agent.main import main as remit_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        sys.exit(dashboard_main())
    if len(sys.argv) > 1 and sys.argv[1].startswith("remit-"):
        sys.exit(remit_main())
    if len(sys.argv) > 1 and sys.argv[1] == "debug-remit-files":
        sys.exit(remit_main())
    sys.exit(main())
