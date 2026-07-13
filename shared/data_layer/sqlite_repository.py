from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .models import (
    AgentRunRecord,
    Priority,
    RecordType,
    ReviewAuditEvent,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    Status,
    utc_now,
)
from .repository import RecordFilters, SharedRecordRepository


SCHEMA_VERSION = 1


class SQLiteSharedRecordRepository(SharedRecordRepository):
    """Durable SQLite repository for normalized records, runs, and review audits."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_records (
                    id TEXT PRIMARY KEY,
                    record_type TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    source_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    effective_date TEXT,
                    status TEXT NOT NULL,
                    owner TEXT,
                    priority TEXT NOT NULL,
                    action_required TEXT,
                    review_status TEXT NOT NULL,
                    confidence REAL,
                    amount TEXT,
                    currency TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    metadata_json TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    schema_version INTEGER NOT NULL,
                    UNIQUE(source_system, source_record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_shared_records_type_status
                    ON shared_records(record_type, status);
                CREATE INDEX IF NOT EXISTS idx_shared_records_review
                    ON shared_records(review_status, priority, effective_date);
                CREATE INDEX IF NOT EXISTS idx_shared_records_effective_date
                    ON shared_records(effective_date);

                CREATE TABLE IF NOT EXISTS shared_agent_runs (
                    run_id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    records_found INTEGER NOT NULL,
                    records_created INTEGER NOT NULL,
                    records_updated INTEGER NOT NULL,
                    records_skipped INTEGER NOT NULL,
                    records_flagged_for_review INTEGER NOT NULL,
                    error_message TEXT,
                    dry_run INTEGER NOT NULL,
                    external_services_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_shared_agent_runs_started
                    ON shared_agent_runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_shared_agent_runs_status
                    ON shared_agent_runs(status, started_at DESC);

                CREATE TABLE IF NOT EXISTS shared_review_audits (
                    event_id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    reason TEXT,
                    request_id TEXT NOT NULL UNIQUE,
                    previous_review_status TEXT NOT NULL,
                    new_review_status TEXT NOT NULL,
                    record_updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(record_id) REFERENCES shared_records(id)
                );
                CREATE INDEX IF NOT EXISTS idx_shared_review_audits_record
                    ON shared_review_audits(record_id, created_at);

                CREATE TABLE IF NOT EXISTS shared_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO shared_schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now().isoformat()),
            )
        os.chmod(self.database_path, 0o600)

    def reconciliation_report(self) -> dict[str, object]:
        """Return non-destructive integrity, count, and duplicate checks."""
        self.initialize()
        with self._connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_issues = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
            counts = {
                "shared_records": connection.execute("SELECT COUNT(*) FROM shared_records").fetchone()[0],
                "agent_runs": connection.execute("SELECT COUNT(*) FROM shared_agent_runs").fetchone()[0],
                "review_audits": connection.execute("SELECT COUNT(*) FROM shared_review_audits").fetchone()[0],
            }
            duplicate_sources = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT source_system, source_record_id
                    FROM shared_records
                    GROUP BY source_system, source_record_id
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            duplicate_keys = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT idempotency_key FROM shared_records
                    WHERE idempotency_key IS NOT NULL
                    GROUP BY idempotency_key HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            schema_versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM shared_schema_migrations ORDER BY version"
                )
            ]
        return {
            "database_path": str(self.database_path),
            "integrity": integrity,
            "foreign_key_issues": foreign_key_issues,
            "counts": counts,
            "duplicate_source_groups": duplicate_sources,
            "duplicate_idempotency_groups": duplicate_keys,
            "schema_versions": schema_versions,
        }

    def upsert(self, record: SharedRecord) -> SharedRecord:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._upsert(connection, record)

    def get(self, record_id: str) -> SharedRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM shared_records WHERE id = ?", (record_id,)).fetchone()
        return _record_from_row(row) if row else None

    def get_by_source(self, source_system: SourceSystem, source_record_id: str) -> SharedRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM shared_records WHERE source_system = ? AND source_record_id = ?",
                (source_system.value, source_record_id),
            ).fetchone()
        return _record_from_row(row) if row else None

    def get_by_idempotency_key(self, idempotency_key: str) -> SharedRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM shared_records WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _record_from_row(row) if row else None

    def list(self, filters: RecordFilters | None = None) -> list[SharedRecord]:
        self.initialize()
        clauses: list[str] = []
        values: list[str] = []
        if filters:
            for column, value in (
                ("record_type", filters.record_type),
                ("source_system", filters.source_system),
                ("status", filters.status),
                ("review_status", filters.review_status),
            ):
                if value is not None:
                    clauses.append(f"{column} = ?")
                    values.append(value.value)
            if filters.owner is not None:
                clauses.append("owner = ?")
                values.append(filters.owner)
            if filters.effective_date_from:
                clauses.append("effective_date >= ?")
                values.append(filters.effective_date_from.isoformat())
            if filters.effective_date_to:
                clauses.append("effective_date <= ?")
                values.append(filters.effective_date_to.isoformat())
        sql = "SELECT * FROM shared_records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        return [_record_from_row(row) for row in rows]

    def mark_reviewed(
        self,
        record_id: str,
        review_status: ReviewStatus = ReviewStatus.APPROVED,
        reviewer: str | None = None,
    ) -> SharedRecord:
        record = self._required(record_id)
        metadata = dict(record.metadata)
        metadata["reviewed_at"] = utc_now().isoformat()
        if reviewer:
            metadata["reviewed_by"] = reviewer
        return self.upsert(replace(record, review_status=review_status, metadata=metadata, updated_at=utc_now()))

    def update_status(self, record_id: str, status: Status) -> SharedRecord:
        record = self._required(record_id)
        return self.upsert(replace(record, status=status, updated_at=utc_now()))

    def record_agent_run(self, run: AgentRunRecord) -> AgentRunRecord:
        self.initialize()
        payload = run.to_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO shared_agent_runs (
                    run_id, agent_name, started_at, completed_at, status,
                    records_found, records_created, records_updated, records_skipped,
                    records_flagged_for_review, error_message, dry_run,
                    external_services_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    agent_name=excluded.agent_name, started_at=excluded.started_at,
                    completed_at=excluded.completed_at, status=excluded.status,
                    records_found=excluded.records_found, records_created=excluded.records_created,
                    records_updated=excluded.records_updated, records_skipped=excluded.records_skipped,
                    records_flagged_for_review=excluded.records_flagged_for_review,
                    error_message=excluded.error_message, dry_run=excluded.dry_run,
                    external_services_json=excluded.external_services_json,
                    schema_version=excluded.schema_version
                """,
                (
                    run.run_id,
                    run.agent_name,
                    payload["started_at"],
                    payload["completed_at"],
                    payload["status"],
                    run.records_found,
                    run.records_created,
                    run.records_updated,
                    run.records_skipped,
                    run.records_flagged_for_review,
                    run.error_message,
                    int(run.dry_run),
                    json.dumps(payload["external_services_used"]),
                    run.schema_version,
                ),
            )
        return run

    def list_agent_runs(self) -> list[AgentRunRecord]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM shared_agent_runs ORDER BY started_at DESC"
            ).fetchall()
        return [_agent_run_from_row(row) for row in rows]

    def append_review_audit(self, event: ReviewAuditEvent) -> ReviewAuditEvent:
        self.initialize()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM shared_review_audits WHERE request_id = ?",
                (event.request_id,),
            ).fetchone()
            if existing:
                return _audit_from_row(existing)
            self._insert_audit(connection, event)
        return event

    def list_review_audits(self, record_id: str | None = None) -> list[ReviewAuditEvent]:
        self.initialize()
        sql = "SELECT * FROM shared_review_audits"
        values: tuple[str, ...] = ()
        if record_id is not None:
            sql += " WHERE record_id = ?"
            values = (record_id,)
        sql += " ORDER BY created_at, event_id"
        with self._connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        return [_audit_from_row(row) for row in rows]

    def commit_review_decision(
        self,
        record: SharedRecord,
        event: ReviewAuditEvent,
        expected_updated_at: datetime,
    ) -> tuple[SharedRecord, ReviewAuditEvent]:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM shared_review_audits WHERE request_id = ?",
                (event.request_id,),
            ).fetchone()
            if existing:
                return self._required_in_connection(connection, record.id), _audit_from_row(existing)
            current = self._required_in_connection(connection, record.id)
            if current.updated_at != expected_updated_at:
                raise RuntimeError("Shared record changed before the review transaction committed.")
            updated = self._upsert(connection, record)
            self._insert_audit(connection, event)
            return updated, event

    def _upsert(self, connection: sqlite3.Connection, record: SharedRecord) -> SharedRecord:
        existing_row = None
        if record.idempotency_key:
            existing_row = connection.execute(
                "SELECT * FROM shared_records WHERE idempotency_key = ?",
                (record.idempotency_key,),
            ).fetchone()
        if existing_row is None:
            existing_row = connection.execute(
                "SELECT * FROM shared_records WHERE source_system = ? AND source_record_id = ?",
                (record.source_system.value, record.source_record_id),
            ).fetchone()
        if existing_row is not None and existing_row["id"] != record.id:
            existing = _record_from_row(existing_row)
            record = replace(record, id=existing.id, created_at=existing.created_at)
        payload = record.to_dict()
        connection.execute(
            """
            INSERT INTO shared_records (
                id, record_type, source_system, source_record_id, source_url,
                created_at, updated_at, effective_date, status, owner, priority,
                action_required, review_status, confidence, amount, currency,
                title, summary, metadata_json, idempotency_key, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                record_type=excluded.record_type, source_system=excluded.source_system,
                source_record_id=excluded.source_record_id, source_url=excluded.source_url,
                updated_at=excluded.updated_at, effective_date=excluded.effective_date,
                status=excluded.status, owner=excluded.owner, priority=excluded.priority,
                action_required=excluded.action_required, review_status=excluded.review_status,
                confidence=excluded.confidence, amount=excluded.amount, currency=excluded.currency,
                title=excluded.title, summary=excluded.summary, metadata_json=excluded.metadata_json,
                idempotency_key=excluded.idempotency_key, schema_version=excluded.schema_version
            """,
            (
                record.id,
                payload["record_type"],
                payload["source_system"],
                record.source_record_id,
                record.source_url,
                payload["created_at"],
                payload["updated_at"],
                payload["effective_date"],
                payload["status"],
                record.owner,
                payload["priority"],
                record.action_required,
                payload["review_status"],
                record.confidence,
                payload["amount"],
                record.currency,
                record.title,
                record.summary,
                json.dumps(record.metadata, sort_keys=True, default=str),
                record.idempotency_key,
                record.schema_version,
            ),
        )
        return record

    def _insert_audit(self, connection: sqlite3.Connection, event: ReviewAuditEvent) -> None:
        connection.execute(
            """
            INSERT INTO shared_review_audits (
                event_id, record_id, action, reviewer, reason, request_id,
                previous_review_status, new_review_status, record_updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.record_id,
                event.action,
                event.reviewer,
                event.reason,
                event.request_id,
                event.previous_review_status.value,
                event.new_review_status.value,
                event.record_updated_at.isoformat(),
                event.created_at.isoformat(),
            ),
        )

    def _required(self, record_id: str) -> SharedRecord:
        record = self.get(record_id)
        if record is None:
            raise KeyError(f"Shared record not found: {record_id}")
        return record

    def _required_in_connection(self, connection: sqlite3.Connection, record_id: str) -> SharedRecord:
        row = connection.execute("SELECT * FROM shared_records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(f"Shared record not found: {record_id}")
        return _record_from_row(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _record_from_row(row: sqlite3.Row) -> SharedRecord:
    return SharedRecord(
        id=row["id"],
        record_type=RecordType(row["record_type"]),
        source_system=SourceSystem(row["source_system"]),
        source_record_id=row["source_record_id"],
        source_url=row["source_url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        effective_date=date.fromisoformat(row["effective_date"]) if row["effective_date"] else None,
        status=Status(row["status"]),
        owner=row["owner"],
        priority=Priority(row["priority"]),
        action_required=row["action_required"],
        review_status=ReviewStatus(row["review_status"]),
        confidence=row["confidence"],
        amount=Decimal(row["amount"]) if row["amount"] is not None else None,
        currency=row["currency"],
        title=row["title"],
        summary=row["summary"],
        metadata=json.loads(row["metadata_json"]),
        idempotency_key=row["idempotency_key"],
        schema_version=row["schema_version"],
    )


def _agent_run_from_row(row: sqlite3.Row) -> AgentRunRecord:
    return AgentRunRecord(
        agent_name=row["agent_name"],
        run_id=row["run_id"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        status=Status(row["status"]),
        records_found=row["records_found"],
        records_created=row["records_created"],
        records_updated=row["records_updated"],
        records_skipped=row["records_skipped"],
        records_flagged_for_review=row["records_flagged_for_review"],
        error_message=row["error_message"],
        dry_run=bool(row["dry_run"]),
        external_services_used=tuple(SourceSystem(value) for value in json.loads(row["external_services_json"])),
        schema_version=row["schema_version"],
    )


def _audit_from_row(row: sqlite3.Row) -> ReviewAuditEvent:
    return ReviewAuditEvent(
        event_id=row["event_id"],
        record_id=row["record_id"],
        action=row["action"],
        reviewer=row["reviewer"],
        reason=row["reason"],
        request_id=row["request_id"],
        previous_review_status=ReviewStatus(row["previous_review_status"]),
        new_review_status=ReviewStatus(row["new_review_status"]),
        record_updated_at=datetime.fromisoformat(row["record_updated_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
