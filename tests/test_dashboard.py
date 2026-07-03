from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.dashboard.config import DashboardSettings
from agents.dashboard.service import DashboardService
from agents.dashboard.web import render_dashboard
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
        }

        html = render_dashboard(snapshot)

        self.assertIn("UCM Admin Dashboard", html)
        self.assertIn("Payment Agent", html)
        self.assertIn("Weekly Remit Agent", html)
        self.assertIn("Placement Agent", html)


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
