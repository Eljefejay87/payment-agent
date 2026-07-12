from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from agents.cash_flow_hq.config import load_cash_flow_settings, validate_cash_flow_settings
from agents.weekly_remit_agent.config import load_remit_settings, validate_remit_settings
from shared.logging import configure_logging

from .service import ICRRemitImportService


def main() -> int:
    parser = argparse.ArgumentParser(description="ICR remit import")
    parser.add_argument("command", choices=["icr-remit-import"])
    parser.add_argument("--file", required=True, help="Path to the exported ICR remit .xlsx or .csv file.")
    parser.add_argument("--dry-run", action="store_true", help="Parse only; do not create Notion rows or email drafts.")
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    args = parser.parse_args()

    remit_settings = load_remit_settings(args.env_file)
    cash_flow_settings = load_cash_flow_settings(args.env_file)
    configure_logging(cash_flow_settings.log_level)
    errors = validate_cash_flow_settings(cash_flow_settings)
    if not args.dry_run:
        errors.extend(validate_remit_settings(remit_settings))
    if errors:
        for error in errors:
            logging.error(error)
        return 2
    result = ICRRemitImportService(remit_settings, cash_flow_settings).import_file(Path(args.file), dry_run=args.dry_run)
    logging.info(
        "ICR remit result: Due to Agency=%s Due to Client=%s Total Collected=%s",
        result.due_to_agency,
        result.due_to_client,
        result.total_collected,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
