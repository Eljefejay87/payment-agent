"""Approved, inactive launchd schedules for bounded UCM one-shot jobs."""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path


EASTERN_TIMEZONE = "America/New_York"
WEEKDAYS = (1, 2, 3, 4, 5)  # launchd: Monday through Friday


@dataclass(frozen=True)
class OneShotJob:
    key: str
    label: str
    command: tuple[str, ...]
    timeout_seconds: int
    schedule: str
    calendar_intervals: tuple[dict[str, int], ...]


def _calendar_entries(hours: range, minutes: tuple[int, ...], *, weekdays: tuple[int, ...] | None) -> tuple[dict[str, int], ...]:
    entries: list[dict[str, int]] = []
    for hour in hours:
        for minute in minutes:
            if weekdays is None:
                entries.append({"Hour": hour, "Minute": minute})
            else:
                entries.extend({"Weekday": weekday, "Hour": hour, "Minute": minute} for weekday in weekdays)
    return tuple(entries)


def _payment_calendar_entries() -> tuple[dict[str, int], ...]:
    entries = list(_calendar_entries(range(7, 19), (0, 15, 30, 45), weekdays=WEEKDAYS))
    entries.extend(
        {"Weekday": weekday, "Hour": 19, "Minute": minute}
        for weekday in WEEKDAYS
        for minute in (0, 15)
    )
    return tuple(entries)


ONE_SHOT_JOBS = (
    OneShotJob(
        "payment", "com.ucm.payment-agent", ("scan-once",), 600,
        "Mon–Fri every 15 minutes, 7:00 AM–7:15 PM ET",
        _payment_calendar_entries(),
    ),
    OneShotJob(
        "operations", "com.ucm.operations-intelligence-agent", ("ops-scan-once",), 900,
        "Mon–Fri at 5:00 PM, 5:45 PM, and 6:00 PM ET",
        tuple(
            {"Weekday": weekday, "Hour": hour, "Minute": minute}
            for weekday in WEEKDAYS for hour, minute in ((17, 0), (17, 45), (18, 0))
        ),
    ),
    OneShotJob(
        "cash_flow", "com.ucm.cash-flow-hq", ("cashflow-run", "--days", "1", "--limit", "50"), 900,
        "Mon–Fri at 10:00 AM and 5:00 PM ET",
        tuple(
            {"Weekday": weekday, "Hour": hour, "Minute": 0}
            for weekday in WEEKDAYS for hour in (10, 17)
        ),
    ),
    OneShotJob(
        "shared_sync", "com.ucm.shared-data-sync", ("shared-data-sync-once", "--limit", "100"), 900,
        "Mon–Fri hourly, 7:00 AM–7:00 PM ET",
        _calendar_entries(range(7, 20), (0,), weekdays=WEEKDAYS),
    ),
    OneShotJob(
        "voicemail", "com.ucm.voicemail-tracker", ("voicemail-scheduled-once",), 600,
        "Daily every 15 minutes, 7:00 AM–10:00 PM ET; at most one due retry per run",
        _calendar_entries(range(7, 23), (0, 15, 30, 45), weekdays=None),
    ),
)
JOB_BY_KEY = {job.key: job for job in ONE_SHOT_JOBS}


def build_launch_agent(job: OneShotJob, *, python_path: str, project_root: Path, log_directory: Path, lock_directory: Path) -> dict:
    """Return a launchd plist payload without installing or loading it."""
    return {
        "Label": job.label,
        "ProgramArguments": [
            python_path, "-m", "shared.one_shot_runner", "--job", job.key,
            "--project-root", str(project_root), "--lock-directory", str(lock_directory),
        ],
        "WorkingDirectory": str(project_root),
        "EnvironmentVariables": {"TZ": EASTERN_TIMEZONE},
        "StartCalendarInterval": list(job.calendar_intervals),
        "StandardOutPath": str(log_directory / f"{job.key}.one-shot.out.log"),
        "StandardErrorPath": str(log_directory / f"{job.key}.one-shot.err.log"),
        # Intentionally no KeepAlive or RunAtLoad: launchd starts one bounded run only.
    }


def write_launch_agents(
    *,
    output_directory: Path,
    python_path: str,
    project_root: Path,
    log_directory: Path,
    lock_directory: Path,
    job_keys: tuple[str, ...] | None = None,
) -> list[Path]:
    """Write inactive plist files; callers must explicitly install/load them later."""
    output_directory.mkdir(parents=True, exist_ok=True)
    selected_jobs = ONE_SHOT_JOBS
    if job_keys is not None:
        unknown = [job_key for job_key in job_keys if job_key not in JOB_BY_KEY]
        if unknown:
            unknown_list = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown one-shot job(s): {unknown_list}")
        selected_jobs = tuple(JOB_BY_KEY[job_key] for job_key in job_keys)
    written: list[Path] = []
    for job in selected_jobs:
        path = output_directory / f"{job.label}.plist"
        with path.open("wb") as handle:
            plistlib.dump(build_launch_agent(job, python_path=python_path, project_root=project_root, log_directory=log_directory, lock_directory=lock_directory), handle, sort_keys=True)
        path.chmod(0o644)
        written.append(path)
    return written
