"""Read-only persisted status sources for Chief of Staff adapters."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agents.voicemail_tracker_agent.runtime_store import VoicemailRuntimeStore
from agents.voicemail_tracker_agent.status_store import VoicemailStatusStore

from .models import StoredStatusSnapshot


class CashFlowStatusSource(Protocol):
    def cash_flow_snapshot(self) -> StoredStatusSnapshot: ...


class VoicemailStatusSource(Protocol):
    def voicemail_snapshot(self) -> StoredStatusSnapshot: ...


class ReadOnlySQLiteStatusSource:
    """Read existing shared status without initializing or changing the database."""

    def __init__(self, database_path: str | Path, *, timeout_seconds: float = 1.0) -> None:
        self.database_path = Path(database_path).expanduser()
        self.timeout_seconds = timeout_seconds

    def cash_flow_snapshot(self) -> StoredStatusSnapshot:
        with self._connect() as connection:
            metrics = {
                "total_bills": self._count(
                    connection,
                    "record_type = ?",
                    ("bill",),
                ),
                "needs_review": self._count(
                    connection,
                    """
                    record_type = ?
                    AND review_status NOT IN (?, ?, ?)
                    AND (
                        status = ?
                        OR review_status = ?
                        OR COALESCE(action_required, '') != ''
                        OR (confidence IS NOT NULL AND confidence < ?)
                    )
                    """,
                    ("bill", "approved", "rejected", "resolved", "needs_review", "pending", 0.72),
                ),
                "past_due": self._count(
                    connection,
                    "record_type = ? AND status = ?",
                    ("bill", "past_due"),
                ),
                "upcoming": self._count(
                    connection,
                    "record_type = ? AND status = ?",
                    ("bill", "upcoming"),
                ),
            }
            last_attempt, last_success, outcome, current_error = self._run_state(
                connection,
                agent_names=("shared_data_sync",),
                external_service="notion",
            )
        return StoredStatusSnapshot(last_attempt, last_success, outcome, metrics, current_error)

    def _connect(self) -> sqlite3.Connection:
        database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=self.timeout_seconds,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    @staticmethod
    def _count(
        connection: sqlite3.Connection,
        where_clause: str,
        parameters: tuple[object, ...],
    ) -> int:
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM shared_records WHERE {where_clause}",
            parameters,
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def _run_state(
        connection: sqlite3.Connection,
        *,
        agent_names: tuple[str, ...],
        external_service: str | None = None,
    ) -> tuple[datetime | None, datetime | None, str, str | None]:
        placeholders = ", ".join("?" for _ in agent_names)
        service_filter = ""
        parameters: list[object] = list(agent_names)
        if external_service:
            service_filter = " AND external_services_json LIKE ?"
            parameters.append(f'%"{external_service}"%')

        latest = connection.execute(
            f"""
            SELECT started_at, status, error_message
            FROM shared_agent_runs
            WHERE agent_name IN ({placeholders}){service_filter}
            ORDER BY started_at DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        successful = connection.execute(
            f"""
            SELECT COALESCE(completed_at, started_at) AS completed_at
            FROM shared_agent_runs
            WHERE agent_name IN ({placeholders})
              AND status = 'completed'{service_filter}
            ORDER BY started_at DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        last_attempt = datetime.fromisoformat(latest["started_at"]) if latest else None
        last_success = None
        if successful and successful["completed_at"]:
            last_success = datetime.fromisoformat(successful["completed_at"])
        current_error = None
        outcome = "Not Yet Run"
        if latest:
            outcome = {
                "completed": "Success",
                "failed": "Error",
                "in_progress": "In Progress",
            }.get(latest["status"], str(latest["status"]).title())
        if latest and latest["status"] == "failed":
            current_error = latest["error_message"] or "Latest persisted run failed."
        return last_attempt, last_success, outcome, current_error


class ReadOnlyVoicemailStatusSource:
    """Read the agent-local JSON snapshot without triggering Voicemail Tracker."""

    def __init__(
        self,
        status_path: str | Path,
        runtime_state_path: str | Path | None = None,
    ) -> None:
        self.store = VoicemailStatusStore(status_path)
        self.runtime_store = (
            VoicemailRuntimeStore(runtime_state_path)
            if runtime_state_path is not None
            else None
        )

    def voicemail_snapshot(self) -> StoredStatusSnapshot:
        snapshot = self.store.read()
        pending_count = self._pending_callback_count()
        if snapshot is None:
            return StoredStatusSnapshot(
                last_run_outcome="Not Yet Run",
                summary_metrics={
                    "pending_callbacks": pending_count,
                    "records_processed": None,
                },
            )
        return StoredStatusSnapshot(
            last_attempted_run=snapshot.last_attempted_run,
            last_successful_run=snapshot.last_successful_run,
            last_run_outcome=snapshot.last_run_outcome.value,
            summary_metrics={
                "pending_callbacks": (
                    pending_count
                    if pending_count is not None
                    else snapshot.pending_callback_count
                ),
                "records_processed": snapshot.total_records_processed,
            },
            current_error=snapshot.last_error_message,
        )

    def _pending_callback_count(self) -> int | None:
        if self.runtime_store is None or not self.runtime_store.path.exists():
            return None
        return len(self.runtime_store.read().pending_callbacks())
