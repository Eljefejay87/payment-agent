from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from agents.weekly_remit_agent.database import RemitDatabase
from agents.weekly_remit_agent.file_detector import RemitFileValidationError, find_required_remit_files
from agents.weekly_remit_agent.models import RemitBatch, RemitFiles
from agents.weekly_remit_agent.reports import build_broker_email_html, build_broker_email_subject
from agents.weekly_remit_agent.service import WeeklyRemitAgent


class WeeklyRemitFileTests(unittest.TestCase):
    def test_finds_required_icr_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            remit = folder / "United Remit week.xlsx"
            liq = folder / "United Liq week.xlsx"
            remit.write_text("remit")
            liq.write_text("liq")

            files = find_required_remit_files(folder, "United Remit", "United Liq", (".xlsx", ".xls"))

            self.assertEqual(files.remit, remit)
            self.assertEqual(files.liquidation, liq)

    def test_missing_liquidation_file_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "United Remit week.xlsx").write_text("remit")

            with self.assertRaises(RemitFileValidationError):
                find_required_remit_files(folder, "United Remit", "United Liq", (".xlsx", ".xls"))

    def test_finds_csv_exports_when_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            remit = folder / "United Remit 7-6-26.csv"
            liq = folder / "United Liq Rate.csv"
            remit.write_text("remit")
            liq.write_text("liq")

            files = find_required_remit_files(folder, "United Remit", "United Liq", (".xlsx", ".xls", ".csv"))

            self.assertEqual(files.remit, remit)
            self.assertEqual(files.liquidation, liq)


class WeeklyRemitReportTests(unittest.TestCase):
    def test_broker_email_template_is_professional_and_lists_attachments(self) -> None:
        batch = RemitBatch(
            broker_name="ICR",
            recipient_email="jprawel@icroffice.com",
            week_start="2026-07-06",
            sent_date="2026-07-06",
            files=RemitFiles(
                remit=Path("United Remit 7-6-26.xlsx"),
                liquidation=Path("United Liq Rate.xlsx"),
            ),
            remit_hash="remit-hash",
            liquidation_hash="liq-hash",
        )

        subject = build_broker_email_subject(batch)
        html = build_broker_email_html(batch)

        self.assertIn("United Capital Management Weekly Remit", subject)
        self.assertIn("Hi Jim", html)
        self.assertIn("week of 2026-07-06", html)
        self.assertIn("United Remit 7-6-26.xlsx", html)
        self.assertIn("United Liq Rate.xlsx", html)


class WeeklyRemitDatabaseTests(unittest.TestCase):
    def test_batch_exists_after_successful_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = RemitDatabase(Path(temp_dir) / "remits.sqlite3")
            db.initialize()
            self.assertFalse(db.batch_exists("ICR", "2026-06-29"))


class WeeklyRemitServiceTests(unittest.TestCase):
    def test_successful_send_records_and_moves_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            incoming = base / "incoming"
            incoming.mkdir()
            (incoming / "United Remit week.xlsx").write_bytes(b"remit")
            (incoming / "United Liq week.xlsx").write_bytes(b"liq")
            settings = build_settings(base)
            agent = build_agent(settings)

            sent = agent.scan_once(force=True)

            self.assertTrue(sent)
            self.assertEqual(agent.graph.sent_count, 1)
            self.assertEqual(agent.teams.sent_count, 1)
            self.assertTrue(agent.db.batch_exists("ICR", "2026-06-29"))
            self.assertTrue((base / "sent" / "2026-06-29" / "United Remit week.xlsx").exists())
            self.assertTrue((base / "sent" / "2026-06-29" / "United Liq week.xlsx").exists())

    def test_duplicate_batch_does_not_send_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            incoming = base / "incoming"
            incoming.mkdir()
            (incoming / "United Remit week.xlsx").write_bytes(b"remit")
            (incoming / "United Liq week.xlsx").write_bytes(b"liq")
            settings = build_settings(base)
            agent = build_agent(settings)

            self.assertTrue(agent.scan_once(force=True))
            (incoming / "United Remit week.xlsx").write_bytes(b"remit")
            (incoming / "United Liq week.xlsx").write_bytes(b"liq")

            self.assertFalse(agent.scan_once(force=True))
            self.assertEqual(agent.graph.sent_count, 1)
            self.assertTrue((base / "duplicates" / "2026-06-29" / "United Remit week.xlsx").exists())
            self.assertTrue((base / "duplicates" / "2026-06-29" / "United Liq week.xlsx").exists())


def build_settings(base: Path) -> SimpleNamespace:
    return SimpleNamespace(
        dry_run=False,
        database_path=base / "remits.sqlite3",
        timezone="America/New_York",
        mailbox_user_id="owner@example.com",
        graph_tenant_id="tenant",
        graph_client_id="client",
        graph_client_secret="secret",
        teams_graph_tenant_id="tenant",
        teams_graph_client_id="client",
        teams_graph_client_secret="secret",
        teams_graph_token_cache_path=base / "teams-token.bin",
        broker_name="ICR",
        broker_email="jprawel@icroffice.com",
        incoming_folder=base / "incoming",
        sent_folder=base / "sent",
        failed_folder=base / "failed",
        duplicate_folder=base / "duplicates",
        remit_filename_contains="United Remit",
        liquidation_filename_contains="United Liq",
        allowed_extensions=(".xlsx", ".xls"),
        send_mode="send",
        run_day="monday",
        send_deadline="15:00",
        scan_interval_minutes=15,
        send_owner_teams_update=True,
        owner_teams_chat_id="owner-chat",
    )


def build_agent(settings: SimpleNamespace) -> WeeklyRemitAgent:
    agent = TestableWeeklyRemitAgent.__new__(TestableWeeklyRemitAgent)
    agent.settings = settings
    agent.db = RemitDatabase(settings.database_path)
    agent.graph = FakeGraph()
    agent.teams = FakeTeams()
    return agent


class FakeGraph:
    def __init__(self) -> None:
        self.sent_count = 0

    def send_user_mail(self, **kwargs) -> None:
        self.sent_count += 1


class FakeTeams:
    def __init__(self) -> None:
        self.sent_count = 0

    def send(self, message) -> None:
        self.sent_count += 1


class TestableWeeklyRemitAgent(WeeklyRemitAgent):
    def _now(self) -> datetime:
        return datetime.fromisoformat("2026-06-29T12:00:00-04:00")
