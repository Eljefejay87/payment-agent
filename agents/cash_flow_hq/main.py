from __future__ import annotations

import argparse
import json
import logging
import sys

from shared.logging import configure_logging
from shared.integrations.microsoft_graph import GraphClient

from .alerts import CashFlowTeamsAlerts
from .config import NOTION_SETUP_MESSAGE, load_cash_flow_settings, validate_cash_flow_settings
from .email_scan import CashFlowEmailScanner
from .graph_client import CashFlowGraphClient
from .service import CashFlowHQService


def main() -> int:
    parser = argparse.ArgumentParser(description="Cash Flow HQ Notion tools")
    parser.add_argument(
        "command",
        choices=[
            "cash-flow-init",
            "cash-flow-preview",
            "cash-flow-list-notion-data-sources",
            "cashflow-teams-alerts",
            "cash-flow-teams-alerts",
            "cashflow-scan-email",
            "cash-flow-scan-email",
        ],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and parse without creating Notion records.")
    parser.add_argument("--debug", action="store_true", help="Log candidate extraction details.")
    parser.add_argument("--days", type=int, default=7, help="Number of Inbox days to scan.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum recent emails to inspect.")
    args = parser.parse_args()

    settings = load_cash_flow_settings(args.env_file)
    configure_logging(settings.log_level)
    service = CashFlowHQService(settings)

    if args.command == "cash-flow-preview":
        print(json.dumps(service.build_payload_preview(), indent=2))
        return 0

    if args.command == "cash-flow-list-notion-data-sources":
        if not settings.notion_api_key:
            logging.error("NOTION_API_KEY is required.")
            return 2
        print(json.dumps(service.list_data_source_metadata(), indent=2))
        return 0

    if args.command in {"cashflow-teams-alerts", "cash-flow-teams-alerts"}:
        errors = validate_cash_flow_settings(settings, include_teams=True)
        if errors:
            log_config_errors(errors)
            return 2
        graph = GraphClient(
            tenant_id=settings.teams_graph_tenant_id,
            client_id=settings.teams_graph_client_id,
            client_secret=settings.teams_graph_client_secret,
            delegated_token_cache_path=settings.teams_graph_token_cache_path,
        )
        CashFlowTeamsAlerts(settings, service, graph).send_alerts(dry_run=args.dry_run)
        logging.info("Cash Flow HQ Teams alert command complete.")
        return 0

    if args.command in {"cashflow-scan-email", "cash-flow-scan-email"}:
        errors = validate_cash_flow_settings(settings, include_graph=True)
        if errors:
            log_config_errors(errors)
            return 2
        scanner = CashFlowEmailScanner(service, CashFlowGraphClient(settings))
        result = scanner.scan(
            days=max(args.days, 1),
            limit=max(args.limit, 1),
            dry_run=args.dry_run,
            debug=args.debug,
        )
        logging.info(
            "Cash Flow HQ email scan complete. Would import=%s Imported=%s Skipped=%s Needs review=%s Errors=%s",
            len(result.would_import),
            len(result.imported),
            len(result.skipped),
            len(result.flagged),
            len(result.errors),
        )
        return 1 if result.errors else 0

    errors = validate_cash_flow_settings(settings)
    if errors:
        log_config_errors(errors)
        return 2

    result = service.ensure_foundation()
    logging.info(
        "Cash Flow HQ Phase 1 complete. Database=%s Views created=%s",
        result["database_id"],
        ", ".join(result["views_created"]) or "none",
    )
    return 0


def log_config_errors(errors: list[str]) -> None:
    notion_prefixes = ("NOTION_API_KEY", "CASH_FLOW_HQ_PARENT_PAGE_ID", "CASH_FLOW_HQ_DATABASE_NAME")
    if any(error.startswith(notion_prefixes) for error in errors):
        logging.error(NOTION_SETUP_MESSAGE)
    for error in errors:
        logging.error(error)


if __name__ == "__main__":
    sys.exit(main())
