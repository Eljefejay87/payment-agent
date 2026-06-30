from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

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
"""


class PaymentDatabase(SQLiteDatabase):
    def __init__(self, path: Path) -> None:
        super().__init__(path)

    def initialize(self) -> None:
        self.initialize_schema(SCHEMA)

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

    def payments_for_local_date(self, local_date: str) -> list[sqlite3.Row]:
        date_variants = _date_variants(local_date)
        placeholders = ", ".join("?" for _ in date_variants)
        with self.connect() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT p.*
                    FROM payments p
                    LEFT JOIN processed_emails e ON e.message_id = p.message_id
                    WHERE p.payment_date IN ({placeholders})
                      AND p.id IN (
                        SELECT MIN(p2.id)
                        FROM payments p2
                        LEFT JOIN processed_emails e2 ON e2.message_id = p2.message_id
                        WHERE p2.payment_date IN ({placeholders})
                        GROUP BY COALESCE(
                            e2.internet_message_id,
                            p2.account_number || '|' || p2.payment_amount_cents || '|' ||
                            COALESCE(p2.payment_date, '') || '|' || COALESCE(p2.payment_type, '')
                        )
                      )
                    ORDER BY payment_date, account_number
                    """,
                    (*date_variants, *date_variants),
                )
            )


def _date_variants(local_date: str) -> tuple[str, ...]:
    try:
        year, month, day = local_date.split("-", 2)
        return (
            local_date,
            f"{int(month)}/{int(day)}/{year}",
            f"{int(month):02d}/{int(day):02d}/{year}",
        )
    except ValueError:
        return (local_date,)
