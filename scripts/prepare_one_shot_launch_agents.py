#!/usr/bin/env python3
"""Generate inactive one-shot LaunchAgent plist files; never calls launchctl."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.one_shot_schedules import JOB_BY_KEY, write_launch_agents


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate UCM one-shot LaunchAgent plists without installing them.")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--log-directory", required=True)
    parser.add_argument("--lock-directory", required=True)
    parser.add_argument("--job", action="append", choices=sorted(JOB_BY_KEY), help="Generate only the selected job key. Repeat to include multiple jobs.")
    args = parser.parse_args()
    write_launch_agents(
        output_directory=Path(args.output_directory),
        project_root=Path(args.project_root),
        python_path=args.python,
        log_directory=Path(args.log_directory),
        lock_directory=Path(args.lock_directory),
        job_keys=tuple(args.job) if args.job else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
