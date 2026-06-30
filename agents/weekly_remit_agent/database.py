from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from shared.database import SQLiteDatabase

from .models import RemitBatch


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

