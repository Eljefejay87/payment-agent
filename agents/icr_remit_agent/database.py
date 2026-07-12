from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from shared.database import SQLiteDatabase

from .models import ICRRemitResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS icr_remit_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker TEXT NOT NULL,
    contact TEXT NOT NULL,
    remit_week TEXT NOT NULL,
    file_name TEXT NOT NULL,
    due_to_agency_total REAL NOT NULL,
    due_to_client_total REAL NOT NULL,
    total_collected REAL NOT NULL,
    status TEXT NOT NULL,
    created_date TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(broker, remit_week, file_name)
);
"""


class ICRRemitDatabase(SQLiteDatabase):
    def initialize(self) -> None:
        self.initialize_schema(SCHEMA)

    def import_exists(self, broker: str, remit_week: str, file_name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM icr_remit_imports
                WHERE lower(broker) = lower(?) AND remit_week = ? AND file_name = ?
                """,
                (broker, remit_week, file_name),
            ).fetchone()
            return row is not None

    def save_import(self, result: ICRRemitResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO icr_remit_imports
                (broker, contact, remit_week, file_name, due_to_agency_total,
                 due_to_client_total, total_collected, status, created_date,
                 notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.broker,
                    result.contact,
                    result.remit_week.isoformat(),
                    result.file_path.name,
                    float(result.due_to_agency),
                    float(result.due_to_client),
                    float(result.total_collected),
                    result.status,
                    now[:10],
                    result.notes,
                    now,
                    now,
                ),
            )

