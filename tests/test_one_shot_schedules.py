from __future__ import annotations

import fcntl
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from shared.one_shot_runner import run_one_shot
from shared.one_shot_schedules import JOB_BY_KEY, ONE_SHOT_JOBS, build_launch_agent, write_launch_agents


class OneShotScheduleTests(unittest.TestCase):
    def test_exact_schedule_windows_and_one_shot_commands(self) -> None:
        payment = JOB_BY_KEY["payment"]
        self.assertEqual(len(payment.calendar_intervals), 250)
        self.assertEqual(payment.command, ("scan-once",))
        self.assertTrue(all(item["Weekday"] in {1, 2, 3, 4, 5} for item in payment.calendar_intervals))
        payment_times = {(item["Hour"], item["Minute"]) for item in payment.calendar_intervals}
        self.assertIn((19, 0), payment_times)
        self.assertIn((19, 15), payment_times)
        self.assertNotIn((19, 30), payment_times)
        self.assertNotIn((19, 45), payment_times)
        self.assertEqual(len(JOB_BY_KEY["operations"].calendar_intervals), 15)
        self.assertEqual(len(JOB_BY_KEY["cash_flow"].calendar_intervals), 10)
        self.assertEqual(len(JOB_BY_KEY["shared_sync"].calendar_intervals), 65)
        voicemail = JOB_BY_KEY["voicemail"]
        self.assertEqual(len(voicemail.calendar_intervals), 64)
        self.assertEqual(voicemail.command, ("voicemail-scheduled-once",))
        self.assertTrue(all("run" not in job.command for job in ONE_SHOT_JOBS for command in job.command))

    def test_plists_are_inactive_and_set_eastern_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = build_launch_agent(
                JOB_BY_KEY["payment"], python_path="/tmp/python", project_root=root,
                log_directory=root / "logs", lock_directory=root / "locks",
            )
            self.assertNotIn("KeepAlive", payload)
            self.assertNotIn("RunAtLoad", payload)
            self.assertEqual(payload["EnvironmentVariables"]["TZ"], "America/New_York")
            written = write_launch_agents(
                output_directory=root / "plists", python_path="/tmp/python", project_root=root,
                log_directory=root / "logs", lock_directory=root / "locks",
            )
            self.assertEqual(len(written), 5)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o644 for path in written))

    def test_runner_uses_fixed_command_timeout_and_private_lock(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_one_shot(
                "payment", project_root=root, lock_directory=root / "locks",
                runner=lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
            )
            self.assertEqual(result, 0)
            self.assertEqual(calls[0][0][-1], "scan-once")
            self.assertEqual(calls[0][1]["timeout"], JOB_BY_KEY["payment"].timeout_seconds)
            self.assertEqual((root / "locks").stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "locks/payment.lock").stat().st_mode & 0o777, 0o600)

    def test_runner_skips_overlap_and_handles_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locks = root / "locks"
            locks.mkdir(mode=0o700)
            lock_path = locks / "payment.lock"
            with lock_path.open("a+") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(run_one_shot("payment", project_root=root, lock_directory=locks), 0)
            def timeout(*_args, **_kwargs):
                raise subprocess.TimeoutExpired("payment", 1)
            self.assertEqual(run_one_shot("payment", project_root=root, lock_directory=locks, runner=timeout), 124)


if __name__ == "__main__":
    unittest.main()
