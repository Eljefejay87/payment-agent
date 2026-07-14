from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from collections.abc import Callable
from typing import Any

from shared.logging import configure_logging
from shared.scheduler import AgentScheduler

from .config import load_settings, validate_settings
from .health import VoicemailHealth
from .sample_data import SAMPLE_VOICEMAIL_MESSAGES
from .service import VoicemailTrackerAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="United Account Services Voicemail Tracker Agent")
    parser.add_argument(
        "command",
        choices=["scan-once", "test-sample", "run", "health"],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)

    agent = VoicemailTrackerAgent(settings)

    if args.command == "health":
        print(json.dumps(VoicemailHealth(settings.health_path).read(), indent=2, sort_keys=True))
        return 0

    if args.command == "test-sample":
        agent.scan_sample(SAMPLE_VOICEMAIL_MESSAGES)
        return 0

    errors = validate_settings(settings)
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    if args.command == "scan-once":
        records = agent.scan_once()
        VoicemailHealth(settings.health_path).mark_success("scan_once", len(records))
        print(json.dumps(records, indent=2))
        logging.info("Voicemail intake scan complete. Parsed %s new voicemail(s).", len(records))
        return 0

    if args.command == "run":
        scheduler = AgentScheduler()
        health = VoicemailHealth(settings.health_path)
        health.mark_starting()
        health.mark_running()
        logging.info(
            "Voicemail Tracker running. Scan every %s minute(s); weekday summary preserved at %s; dry_run=%s",
            settings.scan_interval_minutes,
            settings.summary_time,
            settings.dry_run,
        )

        def stop_agent(signum: int, _frame: Any) -> None:
            logging.info("Voicemail Tracker shutdown requested by signal %s", signum)
            scheduler.stop()

        signal.signal(signal.SIGTERM, stop_agent)
        signal.signal(signal.SIGINT, stop_agent)

        scan_job = lambda: run_with_retry("scan_once", agent.scan_once, health)
        scheduler.every_minutes(settings.scan_interval_minutes, scan_job)
        _schedule_weekday_summary_slot(scheduler, settings.summary_time)
        if settings.run_startup_scan:
            scan_job()
        else:
            logging.info("Startup scan skipped by VOICEMAIL_RUN_STARTUP_SCAN=false")
        try:
            scheduler.run_forever()
        finally:
            health.mark_stopped()
    return 0


def run_with_retry(
    job_name: str,
    job: Callable[[], list[dict]],
    health: VoicemailHealth,
    attempts: int = 3,
    base_delay_seconds: int = 5,
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            records = job()
            health.mark_success(job_name, len(records))
            return
        except Exception as exc:
            health.mark_error(job_name, exc)
            if attempt >= attempts:
                logging.exception("%s failed after %s attempt(s)", job_name, attempt)
                return
            delay = base_delay_seconds * attempt
            logging.warning(
                "%s failed on attempt %s/%s; retrying in %s second(s): %s",
                job_name,
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)


def _preserve_existing_summary_slot() -> None:
    logging.info(
        "Voicemail Tracker weekday Teams summary slot reached. Existing summary behavior is preserved; this Railway readiness pass does not add or change Teams posting."
    )


def _schedule_weekday_summary_slot(scheduler: AgentScheduler, run_time: str) -> None:
    scheduler._schedule.every().monday.at(run_time).do(_preserve_existing_summary_slot)
    scheduler._schedule.every().tuesday.at(run_time).do(_preserve_existing_summary_slot)
    scheduler._schedule.every().wednesday.at(run_time).do(_preserve_existing_summary_slot)
    scheduler._schedule.every().thursday.at(run_time).do(_preserve_existing_summary_slot)
    scheduler._schedule.every().friday.at(run_time).do(_preserve_existing_summary_slot)


if __name__ == "__main__":
    sys.exit(main())
