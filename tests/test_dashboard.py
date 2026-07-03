from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.dashboard.config import DashboardSettings
from agents.dashboard.service import DashboardService
from agents.dashboard.web import render_dashboard, render_operations_page
from agents.operations_intelligence_agent.database import OperationsDatabase
from agents.operations_intelligence_agent.models import ExtractedReport, MetricValue
from agents.payment_agent.database import PaymentDatabase


class DashboardTests(unittest.TestCase):
    def test_snapshot_contains_payment_and_remit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            payment_settings = build_payment_settings(base)
            remit_settings = build_remit_settings(base)
            PaymentDatabase(payment_settings.database_path).initialize()

            snapshot = DashboardService(
                payment_settings,
                remit_settings,
                build_dashboard_settings(),
            ).snapshot()

            self.assertEqual(snapshot["payment"]["status"], "Ready")
            self.assertEqual(snapshot["remit"]["status"], "Waiting")
            self.assertEqual(snapshot["remit"]["broker"], "ICR")
            self.assertEqual(snapshot["operations"]["message"], "No operations report available yet.")

    def test_dashboard_html_renders_agent_cards(self) -> None:
        snapshot = {
            "payment": {
                "status": "Ready",
                "today_count": 0,
                "today_total": "$0.00",
                "recent": [],
                "detail": "Ready",
            },
            "remit": {
                "status": "Waiting",
                "broker": "ICR",
                "incoming_folder": "remits/incoming/ICR",
                "detail": "Missing remit report",
                "files": [],
                "last_sent": "Never",
                "send_deadline": "Monday by 15:00",
            },
            "future_agents": [
                {"name": "Placement Agent", "status": "Planned", "priority": "High"},
            ],
            "manager_checklist": {
                "status": "Ready",
                "detail": "Daily checklist",
                "url": "https://example.com/checklist",
                "sheet_url": "https://example.com/sheet",
                "schedule": "Mon-Thu 5:00 PM, Fri 3:30 PM",
            },
            "operations": {
                "status": "Ready",
                "has_report": True,
                "message": "",
                "card": {
                    "performance_score": "87 / 100 — GOOD DAY",
                    "collected_today": "$5,513.82",
                    "future_payments": "$3,973.00",
                    "pending_payments": "$1,256.05",
                    "calls": "735",
                    "live_contacts": "21",
                    "accounts_worked": "110",
                    "takeaway": "Collections were strong enough to support a good daily score.",
                    "last_updated": "2026-07-01 05:25 PM",
                    "confidence": "87%",
                    "quality": "neutral",
                },
                "detail": {
                    "executive_kpis": empty_executive_kpis(),
                    "latest_brief": "Daily brief",
                    "trend_7_day": "Trend",
                    "summary_30_day": "Summary",
                    "historical_trends": empty_historical_trends(),
                    "trend_cards": empty_trend_cards(),
                    "charts": empty_charts(),
                    "executive_insights": ["Not enough historical data yet."],
                    "duplicate_audit": "No duplicate report dates found.",
                    "historical_reports": [],
                    "manual_review_reports": [],
                },
            },
        }

        html = render_dashboard(snapshot)

        self.assertIn("UCM Admin Dashboard", html)
        self.assertIn("Payment Agent", html)
        self.assertIn("Weekly Remit Agent", html)
        self.assertIn("Placement Agent", html)
        self.assertIn("Operations Intelligence", html)
        self.assertIn("View Full Operations Report", html)

    def test_operations_snapshot_reads_latest_processed_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            payment_settings = build_payment_settings(base)
            OperationsDatabase(payment_settings.database_path).initialize()
            OperationsDatabase(payment_settings.database_path).save_report(
                build_operations_report("2026-07-01", "hash-1"),
                "📊 UCM Daily Operations Brief\n87 / 100 — GOOD DAY\n🧠 AI Insights\n✅ Strong collection day.",
            )

            snapshot = DashboardService(
                payment_settings,
                build_remit_settings(base),
                build_dashboard_settings(),
            ).snapshot()

            operations = snapshot["operations"]
            self.assertEqual(operations["status"], "Ready")
            self.assertEqual(operations["card"]["collected_today"], "$150.00")
            self.assertEqual(operations["card"]["future_payments"], "$500.00")
            self.assertEqual(operations["card"]["calls"], "20")
            self.assertEqual(operations["card"]["takeaway"], "✅ Strong collection day.")
            self.assertEqual(operations["detail"]["executive_kpis"][0]["value"], "$150.00")

    def test_operations_detail_collapses_duplicate_business_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            payment_settings = build_payment_settings(base)
            db = OperationsDatabase(payment_settings.database_path)
            db.initialize()
            db.save_report(build_operations_report("2026-07-01", "manual-hash"), "manual")
            manual = build_operations_report("2026-07-01", "manual-later-hash", posted_cash=999)
            manual.metrics["attempts"] = MetricValue(None, "", 0.0)
            db.save_report(manual, "manual later")
            db.save_report(build_operations_report("2026-07-02", "ready-hash", posted_cash=200), "ready")

            snapshot = DashboardService(
                payment_settings,
                build_remit_settings(base),
                build_dashboard_settings(),
            ).snapshot()

            reports = snapshot["operations"]["detail"]["historical_reports"]
            self.assertEqual([report["report_date"] for report in reports], ["2026-07-02", "2026-07-01"])
            july_1 = reports[1]
            self.assertEqual(july_1["screenshot_hash"], "manual-hash")
            self.assertEqual(july_1["metrics"]["posted_cash"]["value"], 100.0)
            self.assertIn("2026-07-01: 2 records", snapshot["operations"]["detail"]["duplicate_audit"])

    def test_operations_snapshot_adds_dashboard_only_historical_trends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            payment_settings = build_payment_settings(base)
            db = OperationsDatabase(payment_settings.database_path)
            db.initialize()
            db.save_report(
                build_operations_report("2026-07-01", "hash-1", posted_cash=100, contact_rate=10),
                "older",
            )
            db.save_report(
                build_operations_report("2026-07-02", "hash-2", posted_cash=200, contact_rate=20),
                "middle",
            )
            db.save_report(
                build_operations_report("2026-07-08", "hash-3", posted_cash=400, contact_rate=30),
                "latest",
            )

            snapshot = DashboardService(
                payment_settings,
                build_remit_settings(base),
                build_dashboard_settings(),
            ).snapshot()

            trends = snapshot["operations"]["detail"]["historical_trends"]
            self.assertEqual(trends["average_7_day_collections"], "$200.00")
            self.assertEqual(trends["average_30_day_collections"], "$200.00")
            self.assertEqual(trends["best_collection_day"], "2026-07-08 ($450.00)")
            self.assertEqual(trends["lowest_collection_day"], "2026-07-01 ($150.00)")
            self.assertEqual(trends["same_weekday_average"], "$150.00")
            self.assertEqual(trends["collection_trend_vs_7_day"], "+$250.00")
            self.assertEqual(trends["contact_rate_trend_vs_30_day"], "+15.00%")
            self.assertEqual(trends["forecast"], "Beta / Dashboard only")

    def test_operations_snapshot_hides_bad_metrics_when_quality_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            payment_settings = build_payment_settings(base)
            report = build_operations_report("2026-07-01", "hash-1")
            report.metrics["attempts"] = MetricValue(None, "", 0.0)
            OperationsDatabase(payment_settings.database_path).initialize()
            OperationsDatabase(payment_settings.database_path).save_report(report, "bad ocr")

            snapshot = DashboardService(
                payment_settings,
                build_remit_settings(base),
                build_dashboard_settings(),
            ).snapshot()

            self.assertEqual(snapshot["operations"]["status"], "Manual Review")
            self.assertEqual(snapshot["operations"]["card"]["collected_today"], "Manual review needed")
            self.assertEqual(snapshot["operations"]["card"]["takeaway"], "Manual review needed before dashboard metrics are shown.")

    def test_operations_detail_page_renders_latest_brief_and_lists(self) -> None:
        operations = {
            "status": "Ready",
            "card": {"quality": "ready", "confidence": "95%"},
            "detail": {
                "latest_brief": "Full daily brief",
                "trend_7_day": "Collections are up.",
                "summary_30_day": "Average daily collections: $100.00.",
                "executive_kpis": [
                    {"label": "Today's Collections", "value": "$150.00", "tone": "ready"},
                    {"label": "Performance Score", "value": "87 / 100 — GOOD DAY", "tone": "neutral"},
                    {"label": "Future Payments", "value": "$500.00", "tone": "neutral"},
                    {"label": "Live Contacts", "value": "5", "tone": "neutral"},
                    {"label": "AI Confidence", "value": "95%", "tone": "ready"},
                ],
                "historical_trends": {
                    **empty_historical_trends(),
                    "average_7_day_collections": "$125.00",
                    "forecast": "Beta / Dashboard only",
                },
                "trend_cards": [
                    {"label": "7-day avg collections", "value": "$125.00"},
                    {"label": "30-day avg collections", "value": "$150.00"},
                    {"label": "Best collection day", "value": "2026-07-01 ($150.00)"},
                    {"label": "Lowest collection day", "value": "2026-07-01 ($150.00)"},
                    {"label": "Reports passing quality gate", "value": "1"},
                    {"label": "Forecast confidence", "value": "Beta / Dashboard only"},
                ],
                "charts": {
                    "collections": [{"label": "2026-07-01", "value": 150.0}],
                    "performance_score": [{"label": "2026-07-01", "value": 87.0}],
                    "contact_rate": [{"label": "2026-07-01", "value": 25.0}],
                    "calls_vs_collections": [{"label": "2026-07-01", "x": 20, "y": 150.0}],
                },
                "executive_insights": ["Collections are above the 30-day average."],
                "duplicate_audit": "No duplicate report dates found.",
                "historical_reports": [operation_report_row()],
                "manual_review_reports": [],
            },
        }

        html = render_operations_page(operations)

        self.assertIn("Future Payments", html)
        self.assertIn("Performance Score", html)
        self.assertIn("30-Day Movement", html)
        self.assertIn("Executive Insights", html)
        self.assertIn("View Latest Full Brief", html)
        self.assertIn("Full daily brief", html)
        self.assertIn("Historical Trends", html)
        self.assertIn("7-day avg collections", html)
        self.assertIn("Beta / Dashboard only", html)
        self.assertIn("filterOpsReports('ready'", html)
        self.assertIn("Performance Score</th>", html)
        self.assertIn("Historical Reports", html)
        self.assertIn("/operations/report-file?date=2026-07-01", html)


def build_payment_settings(base: Path) -> SimpleNamespace:
    return SimpleNamespace(
        database_path=base / "ucm.sqlite3",
        timezone="America/New_York",
        mailbox_user_id="payments@example.com",
        sender_email="sender@example.com",
        subject_contains="Online Payment -",
        email_provider="microsoft365",
        graph_tenant_id="tenant",
        graph_client_id="client",
        graph_client_secret="secret",
        dry_run=True,
    )


def build_dashboard_settings() -> DashboardSettings:
    return DashboardSettings(
        host="0.0.0.0",
        port=8080,
        log_level="INFO",
        manager_checklist_url="https://example.com/checklist",
        manager_checklist_sheet_url="https://example.com/sheet",
    )


def build_remit_settings(base: Path) -> SimpleNamespace:
    return SimpleNamespace(
        database_path=base / "ucm.sqlite3",
        broker_name="ICR",
        incoming_folder=base / "incoming" / "ICR",
        remit_filename_contains="United Remit",
        liquidation_filename_contains="United Liq",
        allowed_extensions=(".xlsx", ".xls"),
        run_day="monday",
        send_deadline="15:00",
    )


def build_operations_report(
    report_date: str,
    screenshot_hash: str,
    *,
    posted_cash: float = 100.0,
    contact_rate: float = 25.0,
) -> ExtractedReport:
    metrics = {
        "posted_cash": MetricValue(posted_cash, str(posted_cash), 0.95),
        "posted_fees": MetricValue(50.0, "50", 0.95),
        "green_cleared_cash": MetricValue(0.0, "0", 0.95),
        "pending_cash": MetricValue(25.0, "25", 0.95),
        "pending_fees": MetricValue(5.0, "5", 0.95),
        "future_scheduled_cash": MetricValue(500.0, "500", 0.95),
        "future_scheduled_fees": MetricValue(0.0, "0", 0.95),
        "accounts_worked": MetricValue(10, "10", 0.95),
        "attempts": MetricValue(20, "20", 0.95),
        "live_contacts": MetricValue(5, "5", 0.95),
        "contact_rate": MetricValue(contact_rate, f"{contact_rate}%", 0.95),
        "close_rate": MetricValue(10.0, "10%", 0.95),
    }
    return ExtractedReport(
        report_date=report_date,
        screenshot_hash=screenshot_hash,
        screenshot_path=Path("shot.png"),
        ocr_text="ocr",
        metrics=metrics,
        collector_totals=[],
        missing_fields=[],
        manual_review_notes=[],
    )


def operation_report_row() -> dict:
    return {
        "report_date": "2026-07-01",
        "screenshot_hash": "hash-1",
        "screenshot_path": "shot.png",
        "report_path": "reports/operations-intelligence/2026-07-01.txt",
        "metrics": {
            "accounts_worked": {"value": 10, "confidence": 0.95},
            "attempts": {"value": 20, "confidence": 0.95},
            "live_contacts": {"value": 5, "confidence": 0.95},
            "contact_rate": {"value": 25.0, "confidence": 0.95},
            "posted_cash": {"value": 100.0, "confidence": 0.95},
            "posted_fees": {"value": 50.0, "confidence": 0.95},
            "green_cleared_cash": {"value": 0.0, "confidence": 0.95},
        },
    }


def empty_historical_trends() -> dict:
    return {
        "average_7_day_collections": "Manual review",
        "average_30_day_collections": "Manual review",
        "best_collection_day": "Manual review",
        "lowest_collection_day": "Manual review",
        "same_weekday_average": "Manual review",
        "collection_trend_vs_7_day": "Manual review",
        "contact_rate_trend_vs_30_day": "Manual review",
        "forecast": "Beta / Dashboard only",
    }


def empty_executive_kpis() -> list[dict]:
    return [
        {"label": "Today's Collections", "value": "Manual review", "tone": "warn"},
        {"label": "Performance Score", "value": "Manual review", "tone": "warn"},
        {"label": "Future Payments", "value": "Manual review", "tone": "warn"},
        {"label": "Live Contacts", "value": "Manual review", "tone": "warn"},
        {"label": "AI Confidence", "value": "Manual review", "tone": "warn"},
    ]


def empty_trend_cards() -> list[dict]:
    return [
        {"label": "7-day avg collections", "value": "Manual review"},
        {"label": "30-day avg collections", "value": "Manual review"},
        {"label": "Best collection day", "value": "Manual review"},
        {"label": "Lowest collection day", "value": "Manual review"},
        {"label": "Reports passing quality gate", "value": "0"},
        {"label": "Forecast confidence", "value": "Beta / Dashboard only"},
    ]


def empty_charts() -> dict:
    return {
        "collections": [],
        "performance_score": [],
        "contact_rate": [],
        "calls_vs_collections": [],
    }
