from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.payment_agent.config import Settings as PaymentSettings
from agents.payment_agent.database import PaymentDatabase
from agents.payment_agent.parser import cents_to_currency
from agents.payment_agent.reports import today_in_timezone
from agents.payment_agent.service import PaymentAgent
from agents.weekly_remit_agent.config import RemitSettings
from agents.weekly_remit_agent.database import RemitDatabase
from agents.weekly_remit_agent.file_detector import RemitFileValidationError, find_required_remit_files
from agents.weekly_remit_agent.service import WeeklyRemitAgent


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


class DashboardService:
    def __init__(self, payment_settings: PaymentSettings, remit_settings: RemitSettings) -> None:
        self.payment_settings = payment_settings
        self.remit_settings = remit_settings

    def snapshot(self) -> dict:
        return {
            "payment": self.payment_snapshot(),
            "remit": self.remit_snapshot(),
            "future_agents": [
                {"name": "Placement Agent", "status": "Planned", "priority": "High"},
                {"name": "Compliance Agent", "status": "Planned", "priority": "High"},
                {"name": "Finance Agent", "status": "Planned", "priority": "Medium"},
                {"name": "Executive Dashboard", "status": "Planned", "priority": "Medium"},
            ],
        }

    def payment_snapshot(self) -> dict:
        db = PaymentDatabase(self.payment_settings.database_path)
        today = today_in_timezone(self.payment_settings.timezone)
        try:
            db.initialize()
            rows = db.payments_for_local_date(today)
            recent = self._recent_payments(db.path)
        except sqlite3.Error as exc:
            return {
                "status": "Needs Attention",
                "today_count": 0,
                "today_total": "$0.00",
                "recent": [],
                "detail": f"Database error: {exc}",
            }

        total_cents = sum(row["payment_amount_cents"] for row in rows)
        return {
            "status": "Ready",
            "today_count": len(rows),
            "today_total": cents_to_currency(total_cents),
            "recent": recent,
            "detail": f"Last checked {self._local_time_label()}",
        }

    def remit_snapshot(self) -> dict:
        settings = self.remit_settings
        try:
            files = find_required_remit_files(
                settings.incoming_folder,
                settings.remit_filename_contains,
                settings.liquidation_filename_contains,
                settings.allowed_extensions,
            )
            file_status = "Ready"
            detail = "Both ICR remit files are ready."
            filenames = [files.remit.name, files.liquidation.name]
        except RemitFileValidationError as exc:
            file_status = "Waiting"
            detail = str(exc)
            filenames = []

        last_sent = self._last_remit_sent(settings.database_path, settings.broker_name)
        return {
            "status": file_status,
            "broker": settings.broker_name,
            "incoming_folder": str(settings.incoming_folder),
            "detail": detail,
            "files": filenames,
            "last_sent": last_sent,
            "send_deadline": f"{settings.run_day.title()} by {settings.send_deadline}",
        }

    def scan_payments(self) -> ActionResult:
        try:
            count = PaymentAgent(self.payment_settings).scan_once()
        except Exception as exc:
            return ActionResult(False, f"Payment scan failed: {exc}")
        return ActionResult(True, f"Payment scan complete. Processed {count} new payment(s).")

    def send_weekly_remit(self) -> ActionResult:
        try:
            sent = WeeklyRemitAgent(self.remit_settings).scan_once()
        except Exception as exc:
            return ActionResult(False, f"Weekly remit failed: {exc}")
        if sent:
            return ActionResult(True, "Weekly remit sent and archived.")
        return ActionResult(False, "Weekly remit was not sent. Check files, deadline, or duplicate status.")

    def open_remit_folder(self) -> ActionResult:
        folder = self.remit_settings.incoming_folder
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["open", str(folder)], check=False)
        except Exception as exc:
            return ActionResult(False, f"Could not open folder: {exc}")
        return ActionResult(True, f"Opened {folder}.")

    def _recent_payments(self, database_path: Path) -> list[dict]:
        with sqlite3.connect(database_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT p.account_number, p.payment_amount_cents, p.payment_type, p.payment_date, p.created_at
                FROM payments p
                LEFT JOIN processed_emails e ON e.message_id = p.message_id
                WHERE p.id IN (
                    SELECT MIN(p2.id)
                    FROM payments p2
                    LEFT JOIN processed_emails e2 ON e2.message_id = p2.message_id
                    GROUP BY COALESCE(
                        e2.internet_message_id,
                        p2.account_number || '|' || p2.payment_amount_cents || '|' ||
                        COALESCE(p2.payment_date, '') || '|' || COALESCE(p2.payment_type, '')
                    )
                )
                ORDER BY p.created_at DESC
                LIMIT 5
                """
            ).fetchall()
        return [
            {
                "account": row["account_number"],
                "amount": cents_to_currency(row["payment_amount_cents"]),
                "type": row["payment_type"] or "",
                "date": row["payment_date"] or "",
            }
            for row in rows
        ]

    def _last_remit_sent(self, database_path: Path, broker_name: str) -> str:
        try:
            RemitDatabase(database_path).initialize()
            with sqlite3.connect(database_path) as conn:
                row = conn.execute(
                    """
                    SELECT sent_date, week_start
                    FROM remit_batches
                    WHERE lower(broker_name) = lower(?)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (broker_name,),
                ).fetchone()
        except sqlite3.Error:
            return "Unavailable"
        if not row:
            return "Never"
        return f"{row[0]} for week {row[1]}"

    def _local_time_label(self) -> str:
        now = datetime.now(ZoneInfo(self.payment_settings.timezone))
        return now.strftime("%Y-%m-%d %I:%M %p")
