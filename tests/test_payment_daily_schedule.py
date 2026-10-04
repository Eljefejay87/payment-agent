from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from agents.payment_agent.daily_schedule import due_daily_report_date
from agents.payment_agent.database import PaymentDatabase
from agents.payment_agent.main import register_daily_report_schedule
from agents.payment_agent.service import PaymentAgent


NEW_YORK = "America/New_York"


class DailyReportScheduleTests(unittest.TestCase):
    def test_before_five_pm_is_not_eligible(self) -> None:
        self.assertIsNone(
            due_daily_report_date(
                datetime(2026, 9, 14, 19, 28, tzinfo=timezone.utc),
                NEW_YORK,
                "17:00",
            )
        )
        self.assertIsNone(
            due_daily_report_date(
                datetime(2026, 9, 14, 20, 59, tzinfo=timezone.utc),
                NEW_YORK,
                "17:00",
            )
        )

    def test_five_pm_is_eligible_in_summer_and_winter(self) -> None:
        self.assertEqual(
            due_daily_report_date(
                datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc),
                NEW_YORK,
                "17:00",
            ),
            "2026-07-01",
        )
        self.assertEqual(
            due_daily_report_date(
                datetime(2026, 12, 1, 22, 0, tzinfo=timezone.utc),
                NEW_YORK,
                "17:00",
            ),
            "2026-12-01",
        )

    def test_weekend_is_not_a_business_day(self) -> None:
        self.assertIsNone(
            due_daily_report_date(
                datetime(2026, 9, 12, 22, 0, tzinfo=timezone.utc),
                NEW_YORK,
                "17:00",
            )
        )

    def test_daily_report_has_one_timezone_aware_cutoff_poller(self) -> None:
        scheduler = RecordingScheduler()
        job = lambda: None

        register_daily_report_schedule(
            scheduler,
            SimpleNamespace(daily_enabled=True),  # type: ignore[arg-type]
            job,
        )

        self.assertEqual(scheduler.registrations, [(1, job)])

    def test_disabled_daily_report_registers_no_job(self) -> None:
        scheduler = RecordingScheduler()
        register_daily_report_schedule(
            scheduler,
            SimpleNamespace(daily_enabled=False),  # type: ignore[arg-type]
            lambda: None,
        )
        self.assertEqual(scheduler.registrations, [])


class DailyReportDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "payments.sqlite3"

    def test_no_before_five_pm_send_and_one_send_at_cutoff(self) -> None:
        agent, teams = self._agent()

        self.assertFalse(
            agent.send_daily_report(datetime(2026, 9, 14, 19, 28, tzinfo=timezone.utc))
        )
        self.assertEqual(teams.sent_count, 0)

        self.assertTrue(
            agent.send_daily_report(datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc))
        )
        self.assertEqual(teams.sent_count, 1)
        self.assertFalse(
            agent.send_daily_report(datetime(2026, 9, 14, 21, 1, tzinfo=timezone.utc))
        )
        self.assertEqual(teams.sent_count, 1)

    def test_failed_delivery_releases_the_reservation_for_a_retry(self) -> None:
        agent, _teams = self._agent(FailingTeamsNotifier())
        with self.assertRaises(RuntimeError):
            agent.send_daily_report(datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc))

        retry_agent, retry_teams = self._agent()
        self.assertTrue(
            retry_agent.send_daily_report(datetime(2026, 9, 14, 21, 1, tzinfo=timezone.utc))
        )
        self.assertEqual(retry_teams.sent_count, 1)

    def test_restart_after_report_does_not_send_a_duplicate(self) -> None:
        first_agent, first_teams = self._agent()
        self.assertTrue(
            first_agent.send_daily_report(datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc))
        )
        self.assertEqual(first_teams.sent_count, 1)

        restarted_agent, restarted_teams = self._agent()
        self.assertFalse(
            restarted_agent.send_daily_report(datetime(2026, 9, 14, 21, 30, tzinfo=timezone.utc))
        )
        self.assertEqual(restarted_teams.sent_count, 0)

    def test_restart_before_five_pm_does_not_send(self) -> None:
        restarted_agent, teams = self._agent()
        self.assertFalse(
            restarted_agent.send_daily_report(datetime(2026, 9, 14, 19, 30, tzinfo=timezone.utc))
        )
        self.assertEqual(teams.sent_count, 0)

    def _agent(
        self,
        teams: "FakeTeamsNotifier | None" = None,
    ) -> tuple[PaymentAgent, "FakeTeamsNotifier"]:
        agent = PaymentAgent.__new__(PaymentAgent)
        agent.settings = SimpleNamespace(
            database_path=self.database_path,
            save_email_html=False,
            timezone=NEW_YORK,
            daily_report_time="17:00",
        )
        agent.db = PaymentDatabase(self.database_path)
        agent.teams = teams or FakeTeamsNotifier()
        return agent, agent.teams


class FakeTeamsNotifier:
    def __init__(self) -> None:
        self.sent_count = 0

    def send(self, _message: object) -> None:
        self.sent_count += 1


class FailingTeamsNotifier(FakeTeamsNotifier):
    def send(self, _message: object) -> None:
        raise RuntimeError("Teams unavailable")


class RecordingScheduler:
    def __init__(self) -> None:
        self.registrations: list[tuple[int, object]] = []

    def every_minutes(self, minutes: int, job: object) -> None:
        self.registrations.append((minutes, job))


class StaleReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "payments.sqlite3"

    def test_stale_reservation_is_recoverable(self) -> None:
        """A stale 'sending' reservation older than 24 hours is removed on initialize."""
        db = PaymentDatabase(self.database_path)
        db.initialize()
        
        # Manually insert a stale reservation (25 hours ago)
        stale_time = datetime.now(timezone.utc) - timedelta(hours=25)
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_report_runs (report_date, status, reserved_at, sent_at)
                VALUES (?, 'sending', ?, NULL)
                """,
                ("2026-10-01", stale_time.isoformat()),
            )
        
        # Re-initialize should recover the stale reservation
        db.initialize()
        
        # The stale reservation should be gone
        self.assertIsNone(db.daily_report_status("2026-10-01"))
        
        # Should be able to reserve again
        result = db.reserve_daily_report("2026-10-01", datetime.now(timezone.utc))
        self.assertTrue(result)

    def test_recent_reservation_is_not_removed(self) -> None:
        """A recent 'sending' reservation is NOT removed by initialize."""
        db = PaymentDatabase(self.database_path)
        db.initialize()
        
        # Reserve a report (recent)
        report_date = "2026-10-05"
        reserved_at = datetime.now(timezone.utc)
        result = db.reserve_daily_report(report_date, reserved_at)
        self.assertTrue(result)
        
        # Re-initialize should NOT remove the recent reservation
        db.initialize()
        
        # The reservation should still exist
        self.assertEqual(db.daily_report_status(report_date), "sending")
        
        # Should NOT be able to reserve again (duplicate protection)
        result2 = db.reserve_daily_report(report_date, reserved_at)
        self.assertFalse(result2)

    def test_sent_reservation_is_not_removed(self) -> None:
        """A 'sent' reservation is NOT removed by initialize."""
        db = PaymentDatabase(self.database_path)
        db.initialize()
        
        # Create a sent reservation
        report_date = "2026-10-05"
        reserved_at = datetime.now(timezone.utc) - timedelta(hours=25)
        sent_at = datetime.now(timezone.utc) - timedelta(hours=24)
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_report_runs (report_date, status, reserved_at, sent_at)
                VALUES (?, 'sent', ?, ?)
                """,
                (report_date, reserved_at.isoformat(), sent_at.isoformat()),
            )
        
        # Re-initialize should NOT remove the sent reservation
        db.initialize()
        
        # The sent reservation should still exist
        self.assertEqual(db.daily_report_status(report_date), "sent")

    def test_duplicate_send_protection_still_works(self) -> None:
        """Duplicate send protection still works after the fix."""
        db = PaymentDatabase(self.database_path)
        db.initialize()
        
        report_date = "2026-10-05"
        reserved_at = datetime.now(timezone.utc)
        
        # First reservation should succeed
        result1 = db.reserve_daily_report(report_date, reserved_at)
        self.assertTrue(result1)
        
        # Second reservation should fail
        result2 = db.reserve_daily_report(report_date, reserved_at)
        self.assertFalse(result2)
        
        # Mark as sent
        db.mark_daily_report_sent(report_date, datetime.now(timezone.utc))
        
        # Third reservation should still fail (already sent)
        result3 = db.reserve_daily_report(report_date, reserved_at)
        self.assertFalse(result3)

    def test_normal_report_send_still_works(self) -> None:
        """Normal report send flow still works after the fix."""
        db = PaymentDatabase(self.database_path)
        db.initialize()
        
        report_date = "2026-10-05"
        reserved_at = datetime.now(timezone.utc)
        
        # Reserve should succeed
        result = db.reserve_daily_report(report_date, reserved_at)
        self.assertTrue(result)
        
        # Mark as sent
        db.mark_daily_report_sent(report_date, datetime.now(timezone.utc))
        
        # Status should be 'sent'
        self.assertEqual(db.daily_report_status(report_date), "sent")


if __name__ == "__main__":
    unittest.main()
