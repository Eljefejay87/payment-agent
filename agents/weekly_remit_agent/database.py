from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from shared.database import SQLiteDatabase

from .models import RemitApprovalPreview, RemitBatch


SCHEMA = """
CREATE TABLE IF NOT EXISTS remit_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_name TEXT NOT NULL,
    week_start TEXT NOT NULL,
    sent_date TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    remit_file_name TEXT NOT NULL,
    remit_file_hash TEXT NOT NULL,
    liquidation_file_name TEXT NOT NULL,
    liquidation_file_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(broker_name, week_start)
);

CREATE INDEX IF NOT EXISTS idx_remit_batches_broker_week
ON remit_batches(broker_name, week_start);

CREATE INDEX IF NOT EXISTS idx_remit_batches_status
ON remit_batches(status);

CREATE TABLE IF NOT EXISTS remit_run_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_name TEXT NOT NULL,
    week_start TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(broker_name, week_start, status)
);

CREATE TABLE IF NOT EXISTS remit_approvals (
    approval_id TEXT PRIMARY KEY,
    authorized_user_id TEXT NOT NULL,
    broker_name TEXT NOT NULL,
    week_start TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    remit_filename TEXT NOT NULL,
    remit_hash TEXT NOT NULL,
    liquidation_filename TEXT NOT NULL,
    liquidation_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_remit_approvals_lookup
ON remit_approvals(approval_id, authorized_user_id, status);
"""


class RemitDatabase(SQLiteDatabase):
    def __init__(self, path: Path) -> None:
        super().__init__(path)

    def initialize(self) -> None:
        self.initialize_schema(SCHEMA)

    def batch_exists(self, broker_name: str, week_start: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM remit_batches
                WHERE lower(broker_name) = lower(?) AND week_start = ?
                """,
                (broker_name, week_start),
            ).fetchone()
            return row is not None

    def save_sent_batch(self, batch: RemitBatch) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO remit_batches
                (broker_name, week_start, sent_date, recipient_email, remit_file_name,
                 remit_file_hash, liquidation_file_name, liquidation_file_hash,
                 status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.broker_name,
                    batch.week_start,
                    batch.sent_date,
                    batch.recipient_email,
                    batch.files.remit.name,
                    batch.remit_hash,
                    batch.files.liquidation.name,
                    batch.liquidation_hash,
                    "sent",
                    now,
                    now,
                ),
            )

    def reserve_status_notification(
        self,
        broker_name: str,
        week_start: str,
        status: str,
    ) -> bool:
        """Reserve one owner notification per broker/week/status outcome."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO remit_run_notifications
                (broker_name, week_start, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (broker_name, week_start, status, now),
            )
            return cursor.rowcount == 1

    def save_approval(self, preview: RemitApprovalPreview) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO remit_approvals
                (approval_id, authorized_user_id, broker_name, week_start, recipient_email,
                 subject, remit_filename, remit_hash, liquidation_filename, liquidation_hash,
                 status, created_at, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview.approval_id, preview.authorized_user_id, preview.broker_name,
                    preview.week_start, preview.recipient_email, preview.subject,
                    preview.remit_filename, preview.remit_hash, preview.liquidation_filename,
                    preview.liquidation_hash, preview.status, preview.created_at,
                    preview.expires_at, preview.created_at,
                ),
            )

    def get_approval(self, approval_id: str) -> RemitApprovalPreview | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT approval_id, created_at, expires_at, authorized_user_id, broker_name,
                recipient_email, week_start, subject, remit_filename, liquidation_filename,
                remit_hash, liquidation_hash, status FROM remit_approvals WHERE approval_id = ?""",
                (approval_id,),
            ).fetchone()
        return RemitApprovalPreview(**dict(row)) if row else None

    def update_approval_status(self, approval_id: str, user_id: str, expected: str, status: str, updated_at: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE remit_approvals SET status = ?, updated_at = ?
                WHERE approval_id = ? AND authorized_user_id = ? AND status = ?""",
                (status, updated_at, approval_id, user_id, expected),
            )
            return cursor.rowcount == 1
