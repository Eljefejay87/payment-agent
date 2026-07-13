from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from decimal import Decimal

from shared.logging import configure_logging
from shared.integrations.microsoft_graph import GraphClient
from shared.scheduler import AgentScheduler

from .alerts import CashFlowTeamsAlerts
from .automation import run_cash_flow_automation_once
from .config import NOTION_SETUP_MESSAGE, load_cash_flow_settings, validate_cash_flow_settings
from .email_scan import CashFlowEmailScanner
from .graph_client import CashFlowGraphClient
from .payment_scan import CashFlowPaymentScanner
from .review import build_review_report, format_review_report, ignore_email
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
            "cashflow-review",
            "cash-flow-review",
            "cashflow-update-bill",
            "cash-flow-update-bill",
            "cashflow-mark-paid",
            "cash-flow-mark-paid",
            "cashflow-ignore-email",
            "cash-flow-ignore-email",
            "cashflow-run",
            "cash-flow-run",
            "cashflow-scheduler",
            "cash-flow-scheduler",
        ],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and parse without creating Notion records.")
    parser.add_argument("--debug", action="store_true", help="Log candidate extraction details.")
    parser.add_argument("--days", type=int, default=7, help="Number of Inbox days to scan.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum recent emails to inspect.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    parser.add_argument("--no-payment-scan", action="store_true", help="Skip Outlook payment confirmation review scan.")
    parser.add_argument("--page-id", default="", help="Notion page ID for manual bill update commands.")
    parser.add_argument("--payment-date", default="", help="Payment date in YYYY-MM-DD format.")
    parser.add_argument("--payment-method", choices=["Auto Pay", "Manual"], default="Manual")
    parser.add_argument("--confirmation-link", default="", help="Optional Outlook confirmation URL.")
    parser.add_argument("--subject", default="", help="Optional payment confirmation subject.")
    parser.add_argument("--amount", default="", help="Bill amount for manual update.")
    parser.add_argument("--due-date", default="", help="Bill due date in YYYY-MM-DD format.")
    parser.add_argument("--status", choices=["Upcoming", "Paid", "Past Due", "Needs Review"], default=None)
    parser.add_argument("--category", default="", help="Bill category for manual update.")
    parser.add_argument("--payment-type", choices=["Auto Pay", "Manual"], default=None)
    parser.add_argument("--frequency", default="", help="Bill frequency for manual update.")
    parser.add_argument("--notes", default=None, help="Replacement Notes value for manual update.")
    parser.add_argument("--message-id", default="", help="Outlook message ID to ignore in review queue.")
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

    if args.command in {"cashflow-review", "cash-flow-review"}:
        errors = validate_cash_flow_settings(settings, include_graph=not args.no_payment_scan)
        if errors:
            log_config_errors(errors)
            return 2
        graph = None if args.no_payment_scan else CashFlowGraphClient(settings)
        report = build_review_report(service, graph=graph, days=max(args.days, 1), limit=max(args.limit, 1))
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(format_review_report(report))
        return 0

    if args.command in {"cashflow-mark-paid", "cash-flow-mark-paid"}:
        errors = validate_cash_flow_settings(settings)
        if errors:
            log_config_errors(errors)
            return 2
        if not args.page_id or not args.payment_date:
            logging.error("--page-id and --payment-date are required.")
            return 2
        foundation = service.get_existing_foundation()
        service.ensure_payment_confirmation_properties(foundation["data_source_id"])
        service.mark_bill_paid_manually(
            args.page_id,
            parse_iso_date(args.payment_date),
            payment_method=args.payment_method,
            confirmation_link=args.confirmation_link or None,
            confirmation_subject=args.subject or None,
        )
        logging.info("Marked Cash Flow HQ bill paid: %s", args.page_id)
        return 0

    if args.command in {"cashflow-update-bill", "cash-flow-update-bill"}:
        errors = validate_cash_flow_settings(settings)
        if errors:
            log_config_errors(errors)
            return 2
        if not args.page_id:
            logging.error("--page-id is required.")
            return 2
        service.update_bill_fields(
            args.page_id,
            amount=Decimal(args.amount) if args.amount else None,
            due_date_value=parse_iso_date(args.due_date) if args.due_date else None,
            status=args.status,
            category=args.category or None,
            payment_type=args.payment_type,
            frequency=args.frequency or None,
            notes=args.notes,
        )
        logging.info("Updated Cash Flow HQ bill: %s", args.page_id)
        return 0

    if args.command in {"cashflow-ignore-email", "cash-flow-ignore-email"}:
        if not args.message_id:
            logging.error("--message-id is required.")
            return 2
        ignored = ignore_email(settings.cash_flow_review_state_path, args.message_id)
        logging.info("Ignored Cash Flow HQ review email: %s. Ignored count=%s", args.message_id, len(ignored))
        return 0

    if args.command in {"cashflow-run", "cash-flow-run"}:
        errors = validate_cash_flow_settings(settings, include_graph=True)
        if errors:
            log_config_errors(errors)
            return 2
        result = run_cash_flow_automation_once(
            service,
            CashFlowGraphClient(settings),
            days=max(args.days, 1),
            limit=max(args.limit, 1),
            dry_run=args.dry_run,
            debug=args.debug,
        )
        return 1 if result.bill_scan.errors or result.payment_scan.errors else 0

    if args.command in {"cashflow-scheduler", "cash-flow-scheduler"}:
        errors = validate_cash_flow_settings(settings, include_graph=True)
        if errors:
            log_config_errors(errors)
            return 2

        def scheduled_run() -> None:
            run_cash_flow_automation_once(
                service,
                CashFlowGraphClient(settings),
                days=max(args.days, 1),
                limit=max(args.limit, 1),
                dry_run=args.dry_run,
                debug=args.debug,
            )

        scheduler = AgentScheduler()
        for run_time in settings.cash_flow_run_times:
            scheduler.every_day_at(run_time, scheduled_run)
        logging.info("Cash Flow HQ scheduler running daily at %s.", ", ".join(settings.cash_flow_run_times))
        scheduler.run_forever()
        return 0

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


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


if __name__ == "__main__":
    sys.exit(main())
