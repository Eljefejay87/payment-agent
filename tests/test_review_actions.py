from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from agents.dashboard.review_actions import (
    ReviewActionError,
    ReviewActionService,
    ReviewConflictError,
)
from agents.dashboard.shared_data import ReadOnlyDashboardDataService
from shared.data_layer import (
    InMemorySharedRecordRepository,
    RecordType,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    Status,
)


NOW = datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc)


class ReviewActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemorySharedRecordRepository()
        self.record = self.repository.upsert(build_record())
        self.actions = ReviewActionService(self.repository)

    def test_approve_updates_local_review_status_and_appends_audit(self) -> None:
        result = self.apply("approve")

        self.assertEqual(result.record.review_status, ReviewStatus.APPROVED)
        self.assertEqual(result.audit_event.previous_review_status, ReviewStatus.PENDING)
        self.assertEqual(result.audit_event.new_review_status, ReviewStatus.APPROVED)
        self.assertEqual(result.audit_event.reviewer, "Jaye")
        self.assertEqual(len(self.repository.list_review_audits(self.record.id)), 1)

    def test_terminal_decision_removes_record_from_open_queue(self) -> None:
        queue = ReadOnlyDashboardDataService(self.repository, today=date(2026, 7, 12))
        self.assertEqual(len(queue.needs_review()), 1)

        self.apply("resolve")

        self.assertEqual(queue.needs_review(), [])

    def test_reject_requires_reason(self) -> None:
        with self.assertRaisesRegex(ReviewActionError, "reason is required"):
            self.apply("reject", reason=None)

    def test_stale_timestamp_is_rejected_without_audit(self) -> None:
        stale = (self.record.updated_at - timedelta(seconds=1)).isoformat()

        with self.assertRaisesRegex(ReviewConflictError, "changed after"):
            self.actions.apply(
                self.record.id,
                action="approve",
                reviewer="Jaye",
                reason=None,
                expected_updated_at=stale,
                request_id="request-stale",
            )

        self.assertEqual(self.repository.list_review_audits(), [])

    def test_duplicate_request_is_idempotent(self) -> None:
        first = self.apply("approve", request_id="same-request")
        second = self.actions.apply(
            self.record.id,
            action="approve",
            reviewer="Jaye",
            reason=None,
            expected_updated_at=self.record.updated_at.isoformat(),
            request_id="same-request",
        )

        self.assertFalse(first.duplicate_request)
        self.assertTrue(second.duplicate_request)
        self.assertEqual(first.audit_event, second.audit_event)
        self.assertEqual(len(self.repository.list_review_audits()), 1)

    def test_request_id_cannot_be_reused_for_different_action(self) -> None:
        self.apply("approve", request_id="same-request")

        with self.assertRaisesRegex(ReviewConflictError, "different action"):
            self.actions.apply(
                self.record.id,
                action="reject",
                reviewer="Jaye",
                reason="Incorrect bill",
                expected_updated_at=self.record.updated_at.isoformat(),
                request_id="same-request",
            )

    def test_failed_agent_run_projection_is_not_actionable(self) -> None:
        with self.assertRaisesRegex(ReviewActionError, "read-only operational alert"):
            self.actions.apply(
                "agent-run:failed-1",
                action="resolve",
                reviewer="Jaye",
                reason=None,
                expected_updated_at=NOW.isoformat(),
                request_id="agent-run-request",
            )

    def test_already_resolved_item_cannot_be_changed(self) -> None:
        resolved = self.apply("approve").record

        with self.assertRaisesRegex(ReviewConflictError, "already been resolved"):
            self.actions.apply(
                resolved.id,
                action="reject",
                reviewer="Jaye",
                reason="Changed mind",
                expected_updated_at=resolved.updated_at.isoformat(),
                request_id="second-decision",
            )

    def apply(
        self,
        action: str,
        *,
        reason: str | None = None,
        request_id: str = "request-1",
    ):
        return self.actions.apply(
            self.record.id,
            action=action,
            reviewer="Jaye",
            reason=reason,
            expected_updated_at=self.record.updated_at.isoformat(),
            request_id=request_id,
        )


def build_record() -> SharedRecord:
    return SharedRecord(
        id="review-1",
        record_type=RecordType.BILL,
        source_system=SourceSystem.OUTLOOK,
        source_record_id="source-review-1",
        created_at=NOW - timedelta(days=2),
        updated_at=NOW,
        effective_date=date(2026, 7, 15),
        status=Status.NEEDS_REVIEW,
        review_status=ReviewStatus.PENDING,
        action_required="Confirm amount",
        amount=Decimal("100.00"),
        title="Review bill",
        idempotency_key="review-key-1",
    )


if __name__ == "__main__":
    unittest.main()
