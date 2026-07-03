from __future__ import annotations

import argparse
import json
import logging
import sys

from shared.logging import configure_logging

from .config import load_settings, validate_settings
from .sample_data import SAMPLE_VOICEMAIL_MESSAGES
from .service import VoicemailTrackerAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="United Account Services Voicemail Tracker Agent")
    parser.add_argument(
        "command",
        choices=["scan-once", "test-sample"],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)

    agent = VoicemailTrackerAgent(settings)

    if args.command == "test-sample":
        agent.scan_sample(SAMPLE_VOICEMAIL_MESSAGES)
        return 0

    errors = validate_settings(settings)
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    records = agent.scan_once()
    print(json.dumps(records, indent=2))
    logging.info("Voicemail intake scan complete. Parsed %s voicemail(s).", len(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
