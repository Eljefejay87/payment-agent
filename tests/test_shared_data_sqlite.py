from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.dashboard.review_actions import ReviewActionService
from shared.data_layer import (
    AgentRunRecord,
    Priority,
    RecordFilters,
    RecordType,
    ReviewAuditEvent,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    SQLiteSharedRecordRepository,
    Status,
)


NOW = datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc)


class SQLiteSharedRecordRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "shared.sqlite3"
        self.repository = SQLiteSharedRecordRepository(self.database_path)
        self.repository.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialize_creates_versioned_private_database(self) -> None:
        report = self.repository.reconciliation_report()

        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["schema_versions"], [1])
        self.assertEqual(stat.S_IMODE(os.stat(self.database_path).st_mode), 0o600)

    def test_record_persists_across_repository_instances(self) -> None:
        saved = self.repository.upsert(build_record())

        reopened = SQLiteSharedRecordRepository(self.database_path)

        self.assertEqual(reopened.get(saved.id), saved)
        self.assertEqual(reopened.get_by_source(SourceSystem.OUTLOOK, saved.source_record_id), saved)
        self.assertEqual(reopened.get_by_idempotency_key(saved.idempotency_key or ""), saved)

    def test_upsert_preserves_identity_and_prevents_duplicates(self) -> None:
        first = self.repository.upsert(build_record())
        replacement = replace(build_record(record_id="different"), summary="Updated")

        saved = self.repository.upsert(replacement)

        self.assertEqual(saved.id, first.id)
        self.assertEqual(saved.created_at, first.created_at)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertEqual(self.repository.get(first.id).summary, "Updated")

    def test_filters_are_applied_in_sql(self) -> None:
        self.repository.upsert(build_record())
        self.repository.upsert(
            replace(
                build_record(record_id="remit-1"),
                record_type=RecordType.REMIT,
                source_system=SourceSystem.LOCAL_FILE,
                source_record_id="remit-source",
                idempotency_key="remit-key",
                status=Status.COMPLETED,
                review_status=ReviewStatus.NOT_REQUIRED,
            )
        )

        records = self.repository.list(
            RecordFilters(
                record_type=RecordType.BILL,
                review_status=ReviewStatus.PENDING,
                effective_date_from=date(2026, 7, 1),
                effective_date_to=date(2026, 7, 31),
            )
        )

        self.assertEqual([record.id for record in records], ["record-1"])

    def test_agent_runs_persist_across_restart(self) -> None:
        run = AgentRunRecord(
            agent_name="cash_flow_hq",
            run_id="run-1",
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=2),
            status=Status.COMPLETED,
            records_found=2,
            records_created=1,
            external_services_used=(SourceSystem.OUTLOOK, SourceSystem.NOTION),
        )
        self.repository.record_agent_run(run)

        reopened = SQLiteSharedRecordRepository(self.database_path)

        self.assertEqual(reopened.list_agent_runs(), [run])

    def test_review_decision_and_audit_persist_atomically(self) -> None:
        record = self.repository.upsert(build_record())
        actions = ReviewActionService(self.repository)

        result = actions.apply(
            record.id,
            action="approve",
            reviewer="Jaye",
            reason=None,
            expected_updated_at=record.updated_at.isoformat(),
            request_id="request-1",
        )
        reopened = SQLiteSharedRecordRepository(self.database_path)

        self.assertEqual(reopened.get(record.id).review_status, ReviewStatus.APPROVED)
        self.assertEqual(reopened.list_review_audits(record.id), [result.audit_event])

    def test_failed_atomic_audit_insert_rolls_back_record_update(self) -> None:
        original = self.repository.upsert(build_record())
        updated = replace(
            original,
            review_status=ReviewStatus.APPROVED,
            updated_at=NOW + timedelta(minutes=1),
        )
        invalid_event = ReviewAuditEvent(
            event_id="event-1",
            record_id="missing-record",
            action="approve",
            reviewer="Jaye",
            reason=None,
            request_id="request-invalid",
            previous_review_status=ReviewStatus.PENDING,
            new_review_status=ReviewStatus.APPROVED,
            record_updated_at=updated.updated_at,
            created_at=updated.updated_at,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.commit_review_decision(
                updated,
                invalid_event,
                expected_updated_at=original.updated_at,
            )

        self.assertEqual(self.repository.get(original.id), original)
        self.assertEqual(self.repository.list_review_audits(), [])

    def test_reconciliation_reports_clean_counts(self) -> None:
        self.repository.upsert(build_record())

        report = self.repository.reconciliation_report()

        self.assertEqual(report["counts"]["shared_records"], 1)
        self.assertEqual(report["duplicate_source_groups"], 0)
        self.assertEqual(report["duplicate_idempotency_groups"], 0)
        self.assertEqual(report["foreign_key_issues"], [])

    def test_bulk_upsert_rolls_back_every_record_on_failure(self) -> None:
        circular: list[object] = []
        circular.append(circular)
        invalid = replace(
            build_record(record_id="invalid"),
            source_record_id="source-invalid",
            idempotency_key="key-invalid",
            metadata={"circular": circular},
        )

        with self.assertRaises(ValueError):
            self.repository.upsert_many([build_record(), invalid])

        self.assertEqual(self.repository.list(), [])


def build_record(*, record_id: str = "record-1") -> SharedRecord:
    return SharedRecord(
        id=record_id,
        record_type=RecordType.BILL,
        source_system=SourceSystem.OUTLOOK,
        source_record_id="source-1",
        source_url="https://example.com/source-1",
        created_at=NOW,
        updated_at=NOW,
        effective_date=date(2026, 7, 20),
        status=Status.NEEDS_REVIEW,
        priority=Priority.HIGH,
        action_required="Confirm amount",
        review_status=ReviewStatus.PENDING,
        confidence=0.65,
        amount=Decimal("10.50"),
        title="Test bill",
        metadata={"vendor": "Example"},
        idempotency_key="bill:key-1",
    )


if __name__ == "__main__":
    unittest.main()
