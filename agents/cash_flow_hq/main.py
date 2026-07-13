from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from shared.logging import configure_logging
from shared.integrations.microsoft_graph import GraphClient
from shared.scheduler import AgentScheduler

from .alerts import CashFlowTeamsAlerts
from .config import NOTION_SETUP_MESSAGE, load_cash_flow_settings, validate_cash_flow_settings
from .email_scan import CashFlowEmailScanner
from .graph_client import CashFlowGraphClient
from .payment_scan import CashFlowPaymentScanner
from .service import CashFlowHQService


def main() -> int:
    parser = argparse.ArgumentParser(description="Cash Flow HQ Notion tools")
    parser.add_argument(
        "command",
        choices=[
            "cash-flow-init",
            "cash-flow-preview",
            "cash-flow-list-notion-data-sources",
            "cash-flow-debug-action-required",
            "cashflow-debug-action-required",
            "cash-flow-diagnose-action-required",
            "cashflow-diagnose-action-required",
            "cash-flow-patch-action-required",
            "cashflow-patch-action-required",
            "cashflow-test-notification",
            "cash-flow-test-notification",
            "cashflow-teams-alerts",
            "cash-flow-teams-alerts",
            "cashflow-notifications",
            "cash-flow-notifications",
            "cashflow-scan-email",
            "cash-flow-scan-email",
            "cashflow-payment-scan",
            "cash-flow-payment-scan",
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

    if args.command in {"cash-flow-debug-action-required", "cashflow-debug-action-required"}:
        errors = validate_cash_flow_settings(settings)
        if errors:
            log_config_errors(errors)
            return 2
        foundation = service.get_existing_foundation()
        diagnostics = service.action_required_formula_debug(foundation["data_source_id"])
        for property_name, property_type in diagnostics["property_types"].items():
            print(f"{property_name} -> {property_type}")
        print()
        print("Safety Report")
        for operation, property_names in diagnostics["safety_report"].items():
            safe_properties = ", ".join(property_names) if property_names else "None"
            print(f"{operation}: {safe_properties}")
        print()
        print("Formula")
        print(diagnostics["full_formula"])
        return 0

    if args.command in {"cash-flow-diagnose-action-required", "cashflow-diagnose-action-required"}:
        errors = validate_cash_flow_settings(settings)
        if errors:
            log_config_errors(errors)
            return 2
        foundation = service.get_existing_foundation()
        print("Notion-Version:")
        print(settings.notion_version)
        print()
        print("Existing Action Required property schema:")
        print(json.dumps(service.action_required_property_schema(foundation["data_source_id"]), indent=2, default=str))
        print()
        results = []
        for step, (label, expression) in enumerate(
            service.action_required_formula_diagnostic_steps(foundation["data_source_id"]),
            start=1,
        ):
            print(f"Step {step}: PATCHING - {label}")
            print("Formula:")
            print(expression)
            patch_body = service.action_required_formula_patch_body(expression)
            print("JSON PATCH body:")
            print(json.dumps(patch_body, indent=2, default=str))
            try:
                response = service.patch_action_required_formula_diagnostic_step(
                    foundation["data_source_id"],
                    expression,
                )
            except RuntimeError as exc:
                result = {
                    "step": step,
                    "label": label,
                    "status": "FAIL",
                    "formula": expression,
                    "patch_body": patch_body,
                    "response": str(exc),
                }
                results.append(result)
                print(f"Step {step}: FAIL - {label}")
                print("Notion response:")
                print(json.dumps(result["response"], indent=2, default=str))
                print()
                continue
            result = {
                "step": step,
                "label": label,
                "status": "PASS",
                "formula": expression,
                "patch_body": patch_body,
                "response": response,
            }
            results.append(result)
            print(f"Step {step}: PASS - {label}")
            print("Notion response:")
            print(json.dumps(response, indent=2, default=str))
            print()
        return 1 if any(result["status"] == "FAIL" for result in results) else 0

    if args.command in {"cash-flow-patch-action-required", "cashflow-patch-action-required"}:
        errors = validate_cash_flow_settings(settings)
        if errors:
            log_config_errors(errors)
            return 2
        foundation = service.get_existing_foundation()
        expression = service.patch_action_required_formula(foundation["data_source_id"])
        logging.info("Cash Flow HQ Action Required formula patched. Formula=%s", expression)
        return 0

    if args.command in {
        "cashflow-test-notification",
        "cash-flow-test-notification",
        "cashflow-teams-alerts",
        "cash-flow-teams-alerts",
    }:
        errors = validate_cash_flow_settings(settings, include_teams=True)
        if errors:
            log_config_errors(errors)
            return 2
        CashFlowTeamsAlerts(settings, service, build_teams_graph(settings)).send_morning_brief(
            dry_run=args.dry_run,
            force=True,
            record_sent=args.command not in {"cashflow-test-notification", "cash-flow-test-notification"},
        )
        logging.info("Cash Flow HQ Teams alert command complete.")
        return 0

    if args.command in {"cashflow-notifications", "cash-flow-notifications"}:
        errors = validate_cash_flow_settings(settings, include_teams=True)
        if errors:
            log_config_errors(errors)
            return 2
        alerts = CashFlowTeamsAlerts(settings, service, build_teams_graph(settings))

        def send_weekday_brief() -> None:
            if date.today().weekday() < 5:
                alerts.send_morning_brief()

        scheduler = AgentScheduler()
        scheduler.every_day_at(settings.cash_flow_notification_time, send_weekday_brief)
        logging.info("Cash Flow HQ notifications scheduled weekdays at %s.", settings.cash_flow_notification_time)
        scheduler.run_forever()
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

    if args.command in {"cashflow-payment-scan", "cash-flow-payment-scan"}:
        errors = validate_cash_flow_settings(settings, include_graph=True)
        if errors:
            log_config_errors(errors)
            return 2
        scanner = CashFlowPaymentScanner(service, CashFlowGraphClient(settings))
        result = scanner.scan(
            days=max(args.days, 1),
            limit=max(args.limit, 1),
            dry_run=args.dry_run,
            debug=args.debug,
        )
        logging.info(
            "Cash Flow HQ payment scan complete. Would mark paid=%s Marked paid=%s Skipped=%s Needs review=%s Errors=%s",
            len(result.would_mark_paid),
            len(result.marked_paid),
            len(result.skipped),
            len(result.needs_review),
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


def build_teams_graph(settings) -> GraphClient:
    return GraphClient(
        tenant_id=settings.teams_graph_tenant_id,
        client_id=settings.teams_graph_client_id,
        client_secret=settings.teams_graph_client_secret,
        delegated_token_cache_path=settings.teams_graph_token_cache_path,
    )


if __name__ == "__main__":
    sys.exit(main())
