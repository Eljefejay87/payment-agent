from __future__ import annotations

import argparse
import logging
import sys

from shared.logging import configure_logging
from shared.scheduler import AgentScheduler

from .config import load_remit_settings, validate_remit_settings
from .database import RemitDatabase
from .service import WeeklyRemitAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="UCM Weekly Remit Agent")
    parser.add_argument(
        "command",
        choices=["remit-init-db", "remit-scan-once", "remit-run", "debug-remit-files"],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    parser.add_argument("--force", action="store_true", help="Run outside the Monday deadline window.")
    args = parser.parse_args()

    settings = load_remit_settings(args.env_file)
    configure_logging(settings.log_level)

    if args.command == "remit-init-db":
        RemitDatabase(settings.database_path).initialize()
        logging.info("Remit database initialized at %s", settings.database_path)
        return 0

    errors = validate_remit_settings(settings)
    if args.command == "debug-remit-files":
        errors = [
            error
            for error in errors
            if not error.startswith("MAILBOX_USER_ID")
            and not error.startswith("MS_GRAPH_")
            and not error.startswith("TEAMS_")
            and not error.startswith("REMIT_OWNER_TEAMS_CHAT_ID")
            and not error.startswith("REMIT_BROKER_EMAIL")
        ]
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    agent = WeeklyRemitAgent(settings)

    if args.command == "debug-remit-files":
        return debug_remit_files(agent)

    if args.command == "remit-scan-once":
        sent = agent.scan_once(force=args.force)
        logging.info("Weekly remit scan complete. Sent=%s", sent)
        return 0

    if args.command == "remit-run":
        scheduler = AgentScheduler()
        agent.initialize()
        logging.info(
            "Weekly Remit Agent running. Checks every %s minute(s) on %s until %s.",
            settings.scan_interval_minutes,
            settings.run_day,
            settings.send_deadline,
        )
        scheduler.every_minutes(settings.scan_interval_minutes, agent.scan_once)
        agent.scan_once()
        scheduler.run_forever()

    return 0


def debug_remit_files(agent: WeeklyRemitAgent) -> int:
    from .file_detector import find_required_remit_files

    agent.initialize()
    settings = agent.settings
    print("Weekly Remit Agent file check:")
    print(f"  broker: {settings.broker_name}")
    print(f"  incoming folder: {settings.incoming_folder}")
    print(f"  remit filename contains: {settings.remit_filename_contains}")
    print(f"  liquidation filename contains: {settings.liquidation_filename_contains}")
    try:
        files = find_required_remit_files(
            settings.incoming_folder,
            settings.remit_filename_contains,
            settings.liquidation_filename_contains,
            settings.allowed_extensions,
        )
    except Exception as exc:
        print(f"  status: not ready - {exc}")
        return 0
    print("  status: ready")
    print(f"  remit: {files.remit.name}")
    print(f"  liquidation: {files.liquidation.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
