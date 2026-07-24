"""Run one approved UCM schedule command with a private non-overlap lock."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import subprocess
import sys
from pathlib import Path

from .one_shot_schedules import JOB_BY_KEY


def run_one_shot(job_key: str, *, project_root: Path, lock_directory: Path, runner=subprocess.run) -> int:
    job = JOB_BY_KEY.get(job_key)
    if job is None:
        logging.error("Unknown one-shot schedule job.")
        return 2
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_directory.chmod(0o700)
    lock_path = lock_directory / f"{job.key}.lock"
    with lock_path.open("a+") as lock_file:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info("One-shot %s skipped because a prior run is still active.", job.key)
            return 0
        try:
            completed = runner(
                [sys.executable, "main.py", *job.command],
                cwd=project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=job.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            logging.error("One-shot %s timed out.", job.key)
            return 124
    if completed.returncode:
        logging.error("One-shot %s failed with exit code %s.", job.key, completed.returncode)
        return int(completed.returncode)
    logging.info("One-shot %s completed.", job.key)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one approved UCM one-shot schedule job.")
    parser.add_argument("--job", choices=sorted(JOB_BY_KEY))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--lock-directory", required=True)
    args = parser.parse_args()
    return run_one_shot(args.job, project_root=Path(args.project_root), lock_directory=Path(args.lock_directory))


if __name__ == "__main__":
    raise SystemExit(main())
