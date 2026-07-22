from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from agents.voicemail_tracker_agent.audio import process_audio_attachment
from shared.ai_budget import (
    AGENTS,
    AIBudgetGuard,
    estimate_transcription_cost,
    warning_for_spend,
)
from shared.ai_budget_cli import format_status


class AIBudgetGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "ai-budget.sqlite3"
        self.now = [datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)]
        self.guard = AIBudgetGuard(
            self.database_path,
            timezone_name="America/New_York",
            clock=lambda: self.now[0],
        )

    def test_warning_thresholds(self) -> None:
        cases = (
            ("9.999999", None),
            ("10.00", "50% warning"),
            ("15.00", "75% warning"),
            ("18.00", "90% warning"),
            ("20.00", "hard stop"),
        )
        for spend, expected in cases:
            with self.subTest(spend=spend):
                self.assertEqual(warning_for_spend(Decimal(spend)), expected)

    def test_hard_stop_blocks_without_exceeding_limit(self) -> None:
        first = self.guard.reserve(
            agent="Payment Agent", estimated_cost="20.00", model="test-model"
        )
        blocked = self.guard.reserve(
            agent="Operations Intelligence",
            estimated_cost="0.01",
            model="test-model",
        )

        self.assertTrue(first.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.spend_this_month, Decimal("20.000000"))
        self.assertTrue(self.guard.status().kill_switch_active)

    def test_monthly_reset_is_automatic(self) -> None:
        self.guard.reserve(
            agent="Cash Flow HQ", estimated_cost="20.00", model="test-model"
        )
        self.now[0] = datetime(2026, 8, 1, 4, 1, tzinfo=timezone.utc)

        status = self.guard.status()
        decision = self.guard.reserve(
            agent="Cash Flow HQ", estimated_cost="1.00", model="test-model"
        )

        self.assertEqual(status.month, "2026-08")
        self.assertEqual(status.spend_this_month, Decimal("0"))
        self.assertFalse(status.kill_switch_active)
        self.assertTrue(decision.allowed)

    def test_blocked_request_is_logged_with_agent_and_model(self) -> None:
        self.guard.pause()
        decision = self.guard.reserve(
            agent="Voicemail Tracker",
            estimated_cost="0.003",
            model="gpt-4o-mini-transcribe",
        )
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT month, agent, estimated_cost, request_count, timestamp, model, status
                FROM ai_budget_usage
                """
            ).fetchone()

        self.assertFalse(decision.allowed)
        self.assertEqual(row[0], "2026-07")
        self.assertEqual(row[1], "Voicemail Tracker")
        self.assertEqual(Decimal(str(row[2])), Decimal("0.003"))
        self.assertEqual(row[3], 1)
        self.assertEqual(row[5], "gpt-4o-mini-transcribe")
        self.assertEqual(row[6], "blocked")

    def test_manual_pause_resume_and_reset(self) -> None:
        self.guard.pause()
        self.assertTrue(self.guard.status().kill_switch_active)
        self.assertFalse(
            self.guard.reserve(
                agent="Shared Data Sync", estimated_cost="0.01", model="test-model"
            ).allowed
        )
        self.guard.resume()
        self.assertFalse(self.guard.status().kill_switch_active)
        self.assertTrue(
            self.guard.reserve(
                agent="Shared Data Sync", estimated_cost="1.00", model="test-model"
            ).allowed
        )
        self.now[0] = datetime(2026, 7, 22, 16, 1, tzinfo=timezone.utc)
        self.guard.reset()
        self.assertEqual(self.guard.status().spend_this_month, Decimal("0"))

    def test_per_agent_usage_totals(self) -> None:
        self.guard.reserve(
            agent="Payment Agent", estimated_cost="1.25", model="test-model"
        )
        self.guard.reserve(
            agent="Payment Agent", estimated_cost="0.75", model="test-model"
        )
        self.guard.reserve(
            agent="unrecognized-agent", estimated_cost="0.50", model="test-model"
        )
        self.guard.pause()
        self.guard.reserve(
            agent="Payment Agent", estimated_cost="0.10", model="test-model"
        )

        usage = self.guard.status().usage_by_agent
        self.assertEqual(usage["Payment Agent"].estimated_cost, Decimal("2.000000"))
        self.assertEqual(usage["Payment Agent"].request_count, 2)
        self.assertEqual(usage["Payment Agent"].blocked_count, 1)
        self.assertEqual(usage["Other/Unknown"].estimated_cost, Decimal("0.500000"))
        self.assertEqual(set(usage), set(AGENTS))

    def test_transcription_cost_uses_duration_and_safe_fallback(self) -> None:
        self.assertEqual(
            estimate_transcription_cost("00:02:30", "gpt-4o-mini-transcribe"),
            Decimal("0.007500"),
        )
        with patch.dict(
            "os.environ", {"AI_BUDGET_UNKNOWN_REQUEST_ESTIMATE_USD": "0.02"}
        ):
            self.assertEqual(
                estimate_transcription_cost("", "gpt-4o-mini-transcribe"),
                Decimal("0.020000"),
            )

    def test_status_output_contains_required_fields(self) -> None:
        output = format_status(self.guard)
        self.assertIn("Spend today: $0.00", output)
        self.assertIn("Spend this month: $0.00", output)
        self.assertIn("Monthly budget: $20.00", output)
        self.assertIn("Remaining budget: $20.00", output)
        self.assertIn("Percentage used: 0.00%", output)
        self.assertIn("Kill switch: inactive", output)
        self.assertIn("Usage by agent:", output)

    @patch("agents.voicemail_tracker_agent.audio.requests.post")
    @patch("agents.voicemail_tracker_agent.audio.subprocess.run")
    def test_voicemail_hard_stop_prevents_http_request(self, run, post) -> None:
        self.guard.reserve(
            agent="Other/Unknown", estimated_cost="20.00", model="test-model"
        )
        run.return_value.stdout = "30.0\n"
        attachment = {
            "name": "voicemail.mp3",
            "contentType": "audio/mpeg",
            "content_bytes": b"test-audio",
        }
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "test-key",
                "AI_BUDGET_DATABASE_PATH": str(self.database_path),
                "TIMEZONE": "America/New_York",
            },
        ):
            result = process_audio_attachment(attachment)

        self.assertEqual(result.transcription_status, "Pending")
        self.assertIn("budget guard", result.last_error or "")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
