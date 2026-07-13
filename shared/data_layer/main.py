from __future__ import annotations

import argparse
import json

from agents.dashboard.config import load_dashboard_settings

from .sqlite_repository import SQLiteSharedRecordRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Maintain the durable shared UCM data store.")
    parser.add_argument(
        "command",
        choices=["shared-data-init", "shared-data-status"],
        help="Initialize the schema or run non-destructive integrity checks.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to an environment file.")
    args = parser.parse_args()

    settings = load_dashboard_settings(args.env_file)
    repository = SQLiteSharedRecordRepository(settings.shared_database_path)
    repository.initialize()
    if args.command == "shared-data-init":
        print(f"Shared UCM data store ready: {repository.database_path}")
        return 0
    print(json.dumps(repository.reconciliation_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
