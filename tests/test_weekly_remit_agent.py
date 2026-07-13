from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from agents.icr_remit_agent.database import ICRRemitDatabase
from agents.icr_remit_agent.parser import parse_icr_remit_file
from agents.icr_remit_agent.service import ICRRemitImportService
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


class ICRRemitImportTests(unittest.TestCase):
    def test_icr_totals_are_rounded_to_currency_precision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "icr.csv"
            path.write_text("AgencyFee,ClientFee\n1617.909999999999949,2426.720000000000091\n")

            result = parse_icr_remit_file(path)

            self.assertEqual(result.due_to_agency, Decimal("1617.91"))
            self.assertEqual(result.due_to_client, Decimal("2426.72"))
            self.assertEqual(result.total_collected, Decimal("4044.63"))

    def test_icr_xlsx_column_detection_and_sums(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "icr-remit.xlsx"
            write_sample_xlsx(path)

            result = parse_icr_remit_file(path, today=datetime.fromisoformat("2026-07-08T12:00:00").date())

            self.assertEqual(result.due_to_agency, Decimal("75.50"))
            self.assertEqual(result.due_to_client, Decimal("350.25"))
            self.assertEqual(result.total_collected, Decimal("425.75"))
            self.assertEqual(result.remit_week.isoformat(), "2026-07-06")

    def test_icr_dry_run_creates_no_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "icr.csv"
            csv_path.write_text("AgencyFee,ClientFee\n10.00,20.00\n")
            liquidation_path = base / "liq.csv"
            liquidation_path.write_text("liquidation")
            service = build_icr_service(base)

            result = service.import_file(csv_path, liquidation_path, dry_run=True)

            self.assertEqual(result.due_to_client, Decimal("20.00"))
            self.assertFalse(service.db.import_exists("ICR", result.remit_week.isoformat(), "icr.csv"))
            self.assertEqual(service.cash_flow.created_pages, 0)
            self.assertEqual(service.graph.drafts, [])

    def test_icr_live_import_tracks_creates_cash_flow_obligation_and_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "icr.csv"
            csv_path.write_text("AgencyFee,ClientFee\n10.00,20.00\n")
            liquidation_path = base / "liq.csv"
            liquidation_path.write_text("liquidation")
            service = build_icr_service(base)

            result = service.import_file(csv_path, liquidation_path, dry_run=False)

            self.assertTrue(service.db.import_exists("ICR", result.remit_week.isoformat(), "icr.csv"))
            self.assertEqual(service.cash_flow.created_pages, 1)
            payload = service.cash_flow.last_page_payload["properties"]
            self.assertEqual(payload["Vendor / Payee"]["rich_text"][0]["text"]["content"], "ICR")
            self.assertEqual(payload["Category"]["select"]["name"], "Broker Remit")
            self.assertEqual(payload["Amount"]["number"], 20.0)
            self.assertEqual(
                payload["Notes"]["rich_text"][0]["text"]["content"],
                "Due to Agency: $10.00 | Due to Client (owed to Jim): $20.00 | Total Collected: $30.00",
            )
            self.assertEqual(len(service.graph.drafts), 1)
            self.assertIn("Weekly ICR Remit", service.graph.drafts[0]["subject"])
            self.assertNotIn("Due to Agency", service.graph.drafts[0]["html_content"])
            self.assertIn("Attached files:", service.graph.drafts[0]["html_content"])
            self.assertEqual(service.graph.drafts[0]["attachments"], [csv_path, liquidation_path])

    def test_icr_import_requires_liquidation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "icr.csv"
            csv_path.write_text("AgencyFee,ClientFee\n10.00,20.00\n")
            service = build_icr_service(base)

            with self.assertRaisesRegex(ValueError, "liquidation report was not found"):
                service.import_file(csv_path, base / "missing-liq.csv", dry_run=False)

            self.assertEqual(service.cash_flow.created_pages, 0)
            self.assertEqual(service.graph.drafts, [])

    def test_icr_duplicate_prevention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "icr.csv"
            csv_path.write_text("AgencyFee,ClientFee\n10.00,20.00\n")
            liquidation_path = base / "liq.csv"
            liquidation_path.write_text("liquidation")
            service = build_icr_service(base)

            service.import_file(csv_path, liquidation_path, dry_run=False)

            with self.assertRaisesRegex(RuntimeError, "Duplicate ICR remit import"):
                service.import_file(csv_path, liquidation_path, dry_run=False)


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


def build_icr_service(base: Path) -> ICRRemitImportService:
    settings = build_settings(base)
    settings.dry_run = False
    settings.broker_email = "jim@example.com"
    cash_settings = SimpleNamespace(
        notion_api_key="secret",
        notion_version="2026-03-11",
        notion_parent_page_id="page-id",
        database_name="Cash Flow HQ",
        cash_flow_data_source_id="source-id",
        vendor_rules_data_source_id="vendor-source-id",
    )
    service = ICRRemitImportService.__new__(ICRRemitImportService)
    service.remit_settings = settings
    service.cash_flow_settings = cash_settings
    service.db = ICRRemitDatabase(settings.database_path)
    service.cash_flow = FakeCashFlow()
    service.graph = FakeDraftGraph()
    return service


class FakeCashFlow:
    def __init__(self) -> None:
        self.created_pages = 0
        self.last_page_payload = {}
        self.notion = self

    def ensure_foundation(self) -> dict:
        raise AssertionError("ICR import must not run Cash Flow HQ provisioning.")

    def create_manual_expense_payload(self, **kwargs) -> dict:
        return {
            "Expense Name": {"title": [{"type": "text", "text": {"content": kwargs["expense_name"]}}]},
            "Vendor / Payee": {"rich_text": [{"type": "text", "text": {"content": kwargs["vendor_payee"]}}]},
            "Category": {"select": {"name": kwargs["category"]}},
            "Amount": {"number": kwargs["amount"]},
            "Due Date": {"date": {"start": kwargs["due_date"]}},
            "Status": {"select": {"name": "Upcoming"}},
            "Payment Type": {"select": {"name": "Manual"}},
            "Source": {"select": {"name": kwargs["source"]}},
        }

    def request(self, method: str, path: str, **kwargs):
        if method == "POST" and path == "/pages":
            self.created_pages += 1
            self.last_page_payload = kwargs["json"]
            return {"id": "page-id"}
        raise AssertionError(f"Unexpected request: {method} {path}")


class FakeDraftGraph:
    def __init__(self) -> None:
        self.drafts: list[dict] = []

    def create_user_mail_draft(self, **kwargs):
        self.drafts.append(kwargs)
        return {"id": "draft-id"}


def write_sample_xlsx(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>Account</t></is></c><c r="B1" t="inlineStr"><is><t>AgencyFee</t></is></c><c r="C1" t="inlineStr"><is><t>ClientFee</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>A</t></is></c><c r="B2"><v>50.25</v></c><c r="C2"><v>300.25</v></c></row>
    <row r="3"><c r="A3" t="inlineStr"><is><t>B</t></is></c><c r="B3"><v>25.25</v></c><c r="C3"><v>50.00</v></c></row>
  </sheetData>
</worksheet>
""",
        )
