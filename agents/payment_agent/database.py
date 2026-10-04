from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.database import SQLiteDatabase

from .models import PaymentRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_emails (
    message_id TEXT PRIMARY KEY,
    internet_message_id TEXT,
    subject TEXT NOT NULL,
    sender_email TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    account_number TEXT NOT NULL,
    payment_type TEXT,
    note TEXT,
    payment_date TEXT,
    payment_amount_cents INTEGER NOT NULL,
    email_received_at TEXT NOT NULL,
    email_subject TEXT NOT NULL,
    sender_email TEXT,
    snapshot_path TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(message_id) REFERENCES processed_emails(message_id)
);

CREATE INDEX IF NOT EXISTS idx_payments_payment_date ON payments(payment_date);
CREATE INDEX IF NOT EXISTS idx_payments_received_at ON payments(email_received_at);

CREATE TABLE IF NOT EXISTS daily_report_runs (
    report_date TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('sending', 'sent')),
    reserved_at TEXT NOT NULL,
    sent_at TEXT
);
"""


class PaymentDatabase(SQLiteDatabase):
    def __init__(self, path: Path) -> None:
        super().__init__(path)

    def initialize(self) -> None:
        self.initialize_schema(SCHEMA)
        self._recover_stale_reservations()

    def _recover_stale_reservations(self, max_age_hours: int = 24) -> int:
        """Remove stale 'sending' reservations that were left behind by a crashed process.
        
        Returns the number of reservations recovered.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM daily_report_runs
                WHERE status = 'sending' AND reserved_at < ?
                """,
                (cutoff.isoformat(),),
            )
            return cursor.rowcount

    def is_processed(self, message_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_emails WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return row is not None

    def is_processed_email(self, message_id: str, internet_message_id: str | None) -> bool:
        with self.connect() as conn:
            if internet_message_id:
                row = conn.execute(
                    """
                    SELECT 1 FROM processed_emails
                    WHERE message_id = ? OR internet_message_id = ?
                    """,
                    (message_id, internet_message_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM processed_emails WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
            return row is not None

    def is_duplicate_payment(self, payment: PaymentRecord, internet_message_id: str | None) -> bool:
        with self.connect() as conn:
            if internet_message_id:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM payments p
                    JOIN processed_emails e ON e.message_id = p.message_id
                    WHERE e.internet_message_id = ?
                    """,
                    (internet_message_id,),
                ).fetchone()
                if row is not None:
                    return True

            row = conn.execute(
                """
                SELECT 1 FROM payments
                WHERE account_number = ?
                  AND payment_amount_cents = ?
                  AND COALESCE(payment_date, '') = COALESCE(?, '')
                  AND COALESCE(payment_type, '') = COALESCE(?, '')
                """,
                (
                    payment.account_number,
                    payment.payment_amount_cents,
                    payment.payment_date,
                    payment.payment_type,
                ),
            ).fetchone()
            return row is not None

    def save_payment(
        self,
        payment: PaymentRecord,
        internet_message_id: str | None,
        processed_at: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO processed_emails
                (message_id, internet_message_id, subject, sender_email, received_at, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payment.message_id,
                    internet_message_id,
                    payment.email_subject,
                    payment.sender_email,
                    payment.email_received_at,
                    processed_at.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO payments
                (message_id, account_number, payment_type, note, payment_date, payment_amount_cents,
                 email_received_at, email_subject, sender_email, snapshot_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment.message_id,
                    payment.account_number,
                    payment.payment_type,
                    payment.note,
                    payment.payment_date,
                    payment.payment_amount_cents,
                    payment.email_received_at,
                    payment.email_subject,
                    payment.sender_email,
                    payment.snapshot_path,
                    processed_at.isoformat(),
                ),
            )

    def payments_for_local_date(
        self,
        local_date: str,
        timezone_name: str = "America/New_York",
    ) -> list[sqlite3.Row]:
        """Return unique payments received during one local calendar day."""
        business_date = date.fromisoformat(local_date)
        business_zone = ZoneInfo(timezone_name)
        start_local = datetime.combine(business_date, time.min, tzinfo=business_zone)
        end_local = datetime.combine(business_date + timedelta(days=1), time.min, tzinfo=business_zone)
        start_utc = start_local.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        end_utc = end_local.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT p.*
                    FROM payments p
                    LEFT JOIN processed_emails e ON e.message_id = p.message_id
                    WHERE p.email_received_at >= ? AND p.email_received_at < ?
                      AND p.id IN (
                        SELECT MIN(p2.id)
                        FROM payments p2
                        LEFT JOIN processed_emails e2 ON e2.message_id = p2.message_id
                        WHERE p2.email_received_at >= ? AND p2.email_received_at < ?
                        GROUP BY COALESCE(
                            e2.internet_message_id,
                            p2.account_number || '|' || p2.payment_amount_cents || '|' ||
                            COALESCE(p2.payment_date, '') || '|' || COALESCE(p2.payment_type, '')
                        )
                    )
                    ORDER BY payment_date, account_number
                    """,
                    (start_utc, end_utc, start_utc, end_utc),
                )
            )

    def reserve_daily_report(self, report_date: str, reserved_at: datetime) -> bool:
        """Reserve one normal daily report for a local business date."""
        with self.connect() as conn:
            result = conn.execute(
                """
                INSERT OR IGNORE INTO daily_report_runs(report_date, status, reserved_at, sent_at)
                VALUES (?, 'sending', ?, NULL)
                """,
                (report_date, reserved_at.isoformat()),
            )
            return result.rowcount == 1

    def mark_daily_report_sent(self, report_date: str, sent_at: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE daily_report_runs
                SET status = 'sent', sent_at = ?
                WHERE report_date = ? AND status = 'sending'
                """,
                (sent_at.isoformat(), report_date),
            )

    def release_daily_report(self, report_date: str) -> None:
        """Allow a known failed send to retry without retaining a stale reservation."""
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM daily_report_runs WHERE report_date = ? AND status = 'sending'",
                (report_date,),
            )

    def daily_report_status(self, report_date: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM daily_report_runs WHERE report_date = ?",
                (report_date,),
            ).fetchone()
            return row["status"] if row is not None else None
