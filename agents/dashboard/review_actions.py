from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from shared.data_layer.models import ReviewAuditEvent, ReviewStatus, SharedRecord
from shared.data_layer.repository import SharedRecordRepository


ACTION_STATUS = {
    "approve": ReviewStatus.APPROVED,
    "reject": ReviewStatus.REJECTED,
    "resolve": ReviewStatus.RESOLVED,
}
TERMINAL_REVIEW_STATUSES = set(ACTION_STATUS.values())


class ReviewActionError(ValueError):
    pass


class ReviewConflictError(ReviewActionError):
    pass


@dataclass(frozen=True)
class ReviewActionResult:
    record: SharedRecord
    audit_event: ReviewAuditEvent
    duplicate_request: bool = False


class ReviewActionService:
    """Controlled local review transitions with append-only audit events."""

    def __init__(self, repository: SharedRecordRepository) -> None:
        self._repository = repository

    def apply(
        self,
        record_id: str,
        *,
        action: str,
        reviewer: str,
        reason: str | None,
        expected_updated_at: str,
        request_id: str,
    ) -> ReviewActionResult:
        action = action.strip().lower()
        reviewer = reviewer.strip()
        reason = reason.strip() if reason else None
        request_id = request_id.strip()
        if action not in ACTION_STATUS:
            raise ReviewActionError("Unsupported review action.")
        if not reviewer:
            raise ReviewActionError("Reviewer name is required.")
        if len(reviewer) > 100:
            raise ReviewActionError("Reviewer name is too long.")
        if action == "reject" and not reason:
            raise ReviewActionError("A reason is required when rejecting an item.")
        if reason and len(reason) > 500:
            raise ReviewActionError("Review reason is too long.")
        if not request_id or len(request_id) > 100:
            raise ReviewActionError("A valid request ID is required.")

        existing_event = next(
            (event for event in self._repository.list_review_audits() if event.request_id == request_id),
            None,
        )
        if existing_event is not None:
            if existing_event.record_id != record_id or existing_event.action != action:
                raise ReviewConflictError("Request ID was already used for a different action.")
            record = self._required_record(record_id)
            return ReviewActionResult(record, existing_event, duplicate_request=True)

        record = self._required_record(record_id)
        if record.review_status in TERMINAL_REVIEW_STATUSES:
            raise ReviewConflictError("This review item has already been resolved.")
        try:
            expected = datetime.fromisoformat(expected_updated_at)
        except (TypeError, ValueError) as exc:
            raise ReviewActionError("A valid expected updated timestamp is required.") from exc
        if expected.tzinfo is None or expected.utcoffset() is None:
            raise ReviewActionError("Expected updated timestamp must include a timezone.")
        if record.updated_at != expected:
            raise ReviewConflictError("This item changed after the page was loaded. Refresh before retrying.")

        now = datetime.now(timezone.utc)
        new_status = ACTION_STATUS[action]
        metadata = dict(record.metadata)
        metadata["reviewed_at"] = now.isoformat()
        metadata["reviewed_by"] = reviewer
        metadata["review_action"] = action
        if reason:
            metadata["review_reason"] = reason
        updated = self._repository.upsert(
            replace(record, review_status=new_status, updated_at=now, metadata=metadata)
        )
        event = ReviewAuditEvent(
            event_id=str(uuid4()),
            record_id=record.id,
            action=action,
            reviewer=reviewer,
            reason=reason,
            request_id=request_id,
            previous_review_status=record.review_status,
            new_review_status=new_status,
            record_updated_at=updated.updated_at,
            created_at=now,
        )
        self._repository.append_review_audit(event)
        return ReviewActionResult(updated, event)

    def audit_history(self, record_id: str) -> list[dict]:
        return [event.to_dict() for event in self._repository.list_review_audits(record_id)]

    def _required_record(self, record_id: str) -> SharedRecord:
        if record_id.startswith("agent-run:"):
            raise ReviewActionError("Failed agent runs are read-only operational alerts.")
        record = self._repository.get(record_id)
        if record is None:
            raise ReviewActionError("Review item was not found.")
        return record
