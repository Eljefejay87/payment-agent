from __future__ import annotations

import argparse
import json
import logging

from agents.cash_flow_hq.config import load_cash_flow_settings
from agents.dashboard.config import load_dashboard_settings
from agents.weekly_remit_agent.config import load_remit_settings
from shared.logging import configure_logging
from shared.scheduler import AgentScheduler

from .config import load_shared_data_sync_settings, validate_shared_data_sync_settings
from .sqlite_repository import SQLiteSharedRecordRepository
from .sync import ScheduledSharedDataSync, SharedDataSyncService, load_cash_flow_records, load_icr_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Maintain the durable shared UCM data store.")
    parser.add_argument(
        "command",
        choices=[
            "shared-data-init",
            "shared-data-status",
            "shared-data-sync",
            "shared-data-sync-once",
            "shared-data-run",
        ],
        help="Initialize, inspect, or synchronize the shared data store.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to an environment file.")
    parser.add_argument(
        "--source",
        choices=["cash-flow", "icr", "all"],
        default="all",
        help="Source to preview or synchronize. Used by shared-data-sync.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional positive per-source record limit.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the reconciled plan to shared SQLite. Default is dry-run.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Required value APPLY_SHARED_SYNC when --apply is used.",
    )
    args = parser.parse_args()

    settings = load_dashboard_settings(args.env_file)
    repository = SQLiteSharedRecordRepository(settings.shared_database_path)
    repository.initialize()
    if args.command == "shared-data-init":
        print(f"Shared UCM data store ready: {repository.database_path}")
        return 0
    if args.command == "shared-data-status":
        print(json.dumps(repository.reconciliation_report(), indent=2, sort_keys=True))
        return 0
    if args.command in {"shared-data-sync-once", "shared-data-run"}:
        sync_settings = load_shared_data_sync_settings(args.env_file)
        errors = validate_shared_data_sync_settings(sync_settings)
        if errors:
            for error in errors:
                print(error)
            return 2
        configure_logging(load_cash_flow_settings(args.env_file).log_level)
        runner = ScheduledSharedDataSync(
            repository,
            load_cash_flow_settings(args.env_file),
            load_remit_settings(args.env_file),
        )
        if args.command == "shared-data-sync-once":
            report = runner.run_once(source=sync_settings.source, limit=sync_settings.limit)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["run"]["status"] == "completed" else 1
        if not sync_settings.enabled:
            logging.info("Shared data synchronization is disabled.")
            return 0
        scheduler = AgentScheduler()
        job = lambda: runner.run_once(source=sync_settings.source, limit=sync_settings.limit)
        logging.info(
            "Shared data scheduler running every %s minute(s); source=%s; limit=%s",
            sync_settings.interval_minutes,
            sync_settings.source,
            sync_settings.limit,
        )
        if sync_settings.run_at_start:
            job()
        scheduler.every_minutes(sync_settings.interval_minutes, job)
        scheduler.run_forever()
        return 0
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero.")
    if args.apply and args.confirm != "APPLY_SHARED_SYNC":
        parser.error("--apply requires --confirm APPLY_SHARED_SYNC.")

    records = []
    errors = []
    try:
        if args.source in {"cash-flow", "all"}:
            loaded = load_cash_flow_records(
                load_cash_flow_settings(args.env_file),
                limit=args.limit,
            )
            records.extend(loaded.records)
            errors.extend(loaded.errors)
        if args.source in {"icr", "all"}:
            loaded = load_icr_records(load_remit_settings(args.env_file), limit=args.limit)
            records.extend(loaded.records)
            errors.extend(loaded.errors)
    except Exception as exc:
        print(json.dumps({"mode": "apply" if args.apply else "dry-run", "error": str(exc)}, indent=2))
        return 1

    service = SharedDataSyncService(repository)
    plan = service.plan(records, source_errors=errors)
    before = repository.reconciliation_report()
    if not args.apply:
        report = plan.to_dict()
        report["database_before"] = before
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not errors else 1
    try:
        report = service.apply(plan)
    except RuntimeError as exc:
        report = plan.to_dict()
        report["error"] = str(exc)
        report["database_before"] = before
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2
    report["database_before"] = before
    report["database_after"] = repository.reconciliation_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
