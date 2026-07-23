from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from subprocess import CompletedProcess

from agents.dashboard.ai_control import AIControlCenter, APPROVED_LAUNCH_AGENTS, budget_warning_state
from agents.dashboard.web import _is_loopback_address, render_ai_control_page
from shared.ai_budget import AIBudgetGuard


class AIControlCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.now = [datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)]
        self.guard = AIBudgetGuard(
            self.base / "budget.sqlite3",
            timezone_name="America/New_York",
            clock=lambda: self.now[0],
        )
        self.calls: list[list[str]] = []
        self.launch_agents = self.base / "LaunchAgents"
        self.launch_agents.mkdir()
        (self.launch_agents / "com.ucm.payment-agent.plist").write_text("<plist/>")
        self.payment_health = self.base / "payment_health.json"
        self.voicemail_status = self.base / "voicemail_status.json"
        self.voicemail_runtime = self.base / "voicemail_runtime.json"
        self.center = AIControlCenter(
            self.guard,
            audit_path=self.base / "audit.sqlite3",
            project_root=self.base,
            launch_agents_directory=self.launch_agents,
            command_runner=self._runner,
            now_provider=lambda: self.now[0],
            user_id=501,
            health_paths={"payment": self.payment_health, "voicemail": self.voicemail_status},
            voicemail_runtime_state_path=self.voicemail_runtime,
        )

    def _runner(self, command, **_kwargs):
        self.calls.append(list(command))
        if command[:2] == ["launchctl", "print-disabled"]:
            return CompletedProcess(command, 0, '"com.ucm.cash-flow-hq" => disabled\n', "")
        if command[:2] == ["launchctl", "print"]:
            if command[-1].endswith("com.ucm.payment-agent"):
                return CompletedProcess(command, 0, "last exit code = 0\n", "")
            return CompletedProcess(command, 3, "", "")
        return CompletedProcess(command, 0, "", "")

    def test_budget_display_and_warning_thresholds(self) -> None:
        self.guard.reserve(agent="Voicemail Tracker", estimated_cost="10.00", model="test")
        snapshot = self.center.snapshot()

        self.assertEqual(snapshot["budget"]["monthly_budget"], "$20.00")
        self.assertEqual(snapshot["budget"]["spend_this_month"], "$10.00")
        self.assertEqual(snapshot["warning_state"], "yellow")
        self.guard.reserve(agent="Voicemail Tracker", estimated_cost="5.00", model="test")
        self.assertEqual(budget_warning_state(self.guard.status()), "orange")

    def test_pause_and_resume_use_budget_guard_and_are_audited(self) -> None:
        paused = self.center.pause_ai_usage(request_id="pause-request-123")
        resumed = self.center.resume_ai_usage(request_id="resume-request-123")

        self.assertTrue(paused.ok)
        self.assertTrue(resumed.ok)
        self.assertFalse(self.guard.status().manually_paused)
        self.assertEqual([event["type"] for event in self.center.activity_feed()][:2], ["control", "control"])

    def test_service_status_detection_never_starts_a_service(self) -> None:
        agents = {agent["key"]: agent for agent in self.center.service_statuses()}

        self.assertEqual(agents["payment"]["state"], "Running")
        self.assertEqual(agents["payment"]["last_exit_code"], "0")
        self.assertEqual(agents["cash_flow"]["state"], "Stopped")
        self.assertEqual(agents["voicemail"]["state"], "Stopped")
        self.assertFalse(any(command[:2] == ["launchctl", "bootstrap"] for command in self.calls))

    def test_resume_all_is_permanently_blocked(self) -> None:
        result = self.center.resume_all_services(request_id="resume-all-123")

        self.assertFalse(result.ok)
        self.assertIn("Event-driven schedules", result.message)
        self.assertFalse(any(command[:2] == ["launchctl", "bootstrap"] for command in self.calls))

    def test_one_time_job_requires_confirmation_and_duplicate_request_does_not_repeat(self) -> None:
        denied = self.center.run_one_time_job(
            "payment", request_id="one-time-request-123", confirmation="NO"
        )
        first = self.center.run_one_time_job(
            "payment", request_id="one-time-request-456", confirmation="RUN ONE-TIME JOB"
        )
        second = self.center.run_one_time_job(
            "payment", request_id="one-time-request-456", confirmation="RUN ONE-TIME JOB"
        )

        self.assertFalse(denied.ok)
        self.assertTrue(first.ok)
        self.assertTrue(second.duplicate)
        executions = [command for command in self.calls if command and command[0].endswith("python")]
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0][-1], "scan-once")
        self.assertNotIn("run", executions[0])

    def test_pause_all_only_targets_approved_labels_and_requires_confirmation(self) -> None:
        denied = self.center.pause_all_services(
            request_id="pause-all-request-1", confirmation="not confirmed"
        )
        accepted = self.center.pause_all_services(
            request_id="pause-all-request-2", confirmation="PAUSE ALL SERVICES"
        )

        self.assertFalse(denied.ok)
        self.assertTrue(accepted.ok)
        bootouts = [command[-1] for command in self.calls if command[:2] == ["launchctl", "bootout"]]
        self.assertEqual(set(item.rsplit("/", 1)[-1] for item in bootouts), set(APPROVED_LAUNCH_AGENTS))

    def test_activity_feed_redacts_secret_like_errors(self) -> None:
        def failing_runner(command, **_kwargs):
            self.calls.append(list(command))
            return CompletedProcess(command, 7, "", "sk-proj-sensitive-value")

        self.center.command_runner = failing_runner
        self.center.run_one_time_job(
            "payment", request_id="redaction-request-123", confirmation="RUN ONE-TIME JOB"
        )
        serialized = str(self.center.activity_feed())

        self.assertNotIn("sk-proj-sensitive-value", serialized)

    def test_activity_feed_and_30_day_spending_chart(self) -> None:
        self.guard.reserve(agent="Voicemail Tracker", estimated_cost=Decimal("1.25"), model="test")
        self.now[0] = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)
        self.guard.reserve(agent="Voicemail Tracker", estimated_cost=Decimal("0.75"), model="test")
        self.now[0] = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)

        chart = self.center.spending_chart()
        activity = self.center.activity_feed()

        amounts = {point["date"]: point["amount"] for point in chart}
        self.assertEqual(amounts["2026-07-22"], 0.75)
        self.assertEqual(amounts["2026-07-23"], 1.25)
        self.assertTrue(any(event["type"] == "budget" for event in activity))

    def test_agent_runs_and_transcription_retries_are_sanitized_activity(self) -> None:
        self.payment_health.write_text(
            '{"updated_at":"2026-07-23T16:00:00+00:00",'
            '"last_successful_run":"2026-07-23T16:00:00+00:00","last_error":null}'
        )
        self.voicemail_status.write_text(
            '{"last_attempted_run":"2026-07-23T15:00:00+00:00",'
            '"last_successful_run":null,"last_run_outcome":"Error",'
            '"last_error_message":"janise@example.com"}'
        )
        self.voicemail_runtime.write_text(
            '{"transcription_jobs":{"private-id":{"status":"Pending",'
            '"next_retry_at":"2026-07-23T17:00:00+00:00",'
            '"message_id":"sensitive"}}}'
        )

        activity = self.center.activity_feed()
        serialized = str(activity)

        self.assertTrue(any(event["type"] == "agent-run" for event in activity))
        self.assertTrue(any(event["type"] == "transcription-retry" for event in activity))
        self.assertNotIn("janise@example.com", serialized)
        self.assertNotIn("private-id", serialized)
        self.assertNotIn("sensitive", serialized)

    def test_page_renders_without_secrets(self) -> None:
        self.guard.reserve(agent="Voicemail Tracker", estimated_cost="1.00", model="test")
        html = render_ai_control_page(self.center.snapshot(), csrf_token="csrf-secret")

        self.assertIn("AI Control Center", html)
        self.assertIn("Pause AI Usage", html)
        self.assertIn("Resume All Services", html)
        self.assertIn("30-Day AI Spending", html)
        self.assertIn("controlInFlight", html)
        self.assertNotIn("OPENAI_API_KEY", html)
        self.assertNotIn("csrf-secret", html.replace('const csrfToken = "csrf-secret";', ""))

    def test_control_actions_are_limited_to_loopback_clients(self) -> None:
        self.assertTrue(_is_loopback_address("127.0.0.1"))
        self.assertTrue(_is_loopback_address("::1"))
        self.assertFalse(_is_loopback_address("192.168.1.10"))

    def test_audit_database_has_private_mode_and_durable_actions(self) -> None:
        self.center.pause_ai_usage(request_id="durable-request-123")
        mode = (self.base / "audit.sqlite3").stat().st_mode & 0o777
        with sqlite3.connect(self.base / "audit.sqlite3") as connection:
            count = connection.execute("SELECT COUNT(*) FROM control_actions").fetchone()[0]

        self.assertEqual(mode, 0o600)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
