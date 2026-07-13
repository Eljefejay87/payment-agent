from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from shared.data_layer.models import (
    AgentRunRecord,
    Priority,
    RecordType,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    Status,
)
from shared.data_layer.repository import SharedRecordRepository


CONFIDENCE_REVIEW_THRESHOLD = 0.72
UNRESOLVED_REVIEW_STATUSES = {ReviewStatus.PENDING}
SAFE_METADATA_FIELDS = {
    "vendor",
    "invoice_number",
    "category",
    "payment_method",
    "autopay",
    "review_reasons",
    "broker",
    "due_to_agency",
    "due_to_client",
    "total_collected",
    "remit_week",
    "week_ending",
    "existing_status",
}


@dataclass(frozen=True)
class ReviewQueueFilters:
    record_type: RecordType | None = None
    source_system: SourceSystem | None = None
    priority: Priority | None = None
    review_status: ReviewStatus | None = None
    action_required: bool | None = None
    date_from: date | None = None
    date_to: date | None = None


class ReadOnlyDashboardDataService:
    """Read-only projections over normalized shared records and agent runs."""

    def __init__(self, repository: SharedRecordRepository, *, today: date | None = None) -> None:
        self._repository = repository
        self._today = today

    def summary(self) -> dict[str, Any]:
        today = self._today or datetime.now(timezone.utc).date()
        records = self._repository.list()
        bills = [record for record in records if record.record_type == RecordType.BILL]
        remits = [record for record in records if record.record_type == RecordType.REMIT]
        review_items = self.needs_review()
        failed_runs = self.failed_agent_runs()
        unresolved_dates = [item["created_at"] for item in review_items]
        oldest_age = 0
        if unresolved_dates:
            oldest = min(datetime.fromisoformat(value) for value in unresolved_dates)
            oldest_age = max(0, (today - oldest.date()).days)
        return {
            "record_type_counts": _counts(record.record_type.value for record in records),
            "status_counts": _counts(record.status.value for record in records),
            "total_amount": _decimal_sum(record.amount for record in records),
            "due_next_7_days": _bill_total(bills, today, today + timedelta(days=7)),
            "due_next_30_days": _bill_total(bills, today, today + timedelta(days=30)),
            "past_due_total": _decimal_sum(
                record.amount for record in bills if _is_past_due(record, today)
            ),
            "recent_remit_total": _decimal_sum(
                record.amount
                for record in remits
                if record.effective_date and today - timedelta(days=30) <= record.effective_date <= today
            ),
            "needs_review": {
                "unresolved_count": len(review_items),
                "critical_high_count": sum(
                    item["priority"] in {Priority.CRITICAL.value, Priority.HIGH.value}
                    for item in review_items
                ),
                "past_due_count": sum(item["status"] == Status.PAST_DUE.value for item in review_items),
                "failed_agent_run_count": len(failed_runs),
                "oldest_unresolved_age_days": oldest_age,
                "top_items": review_items[:10],
            },
            "upcoming_bills": [record.to_dict() for record in self.upcoming_bills()],
            "past_due_bills": [record.to_dict() for record in self.past_due_bills()],
            "recent_remits": [record.to_dict() for record in self.recent_remits()],
            "agent_health": self.agent_health(),
        }

    def needs_review(
        self,
        filters: ReviewQueueFilters | None = None,
        *,
        page: int = 1,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        today = self._today or datetime.now(timezone.utc).date()
        items = [
            _review_item(record, today)
            for record in self._repository.list()
            if _review_reasons(record)
        ]
        items.extend(_failed_run_review_item(run, today) for run in self.failed_agent_runs())
        items = [item for item in items if _matches_review_filters(item, filters)]
        items.sort(key=_review_sort_key)
        if page_size is not None:
            start = max(0, page - 1) * page_size
            items = items[start : start + page_size]
        return items

    def records_requiring_action(self) -> list[SharedRecord]:
        return [record for record in self._repository.list() if bool(record.action_required)]

    def records_pending_review(self) -> list[SharedRecord]:
        return [
            record
            for record in self._repository.list()
            if record.review_status == ReviewStatus.PENDING
        ]

    def review_item(self, record_id: str) -> dict[str, Any] | None:
        if record_id.startswith("agent-run:"):
            run_id = record_id.removeprefix("agent-run:")
            run = next((item for item in self.failed_agent_runs() if item.run_id == run_id), None)
            return _failed_run_review_item(run, self._today or datetime.now(timezone.utc).date()) if run else None
        record = self._repository.get(record_id)
        if record is None or not _review_reasons(record):
            return None
        return _review_item(record, self._today or datetime.now(timezone.utc).date())

    def upcoming_bills(self, *, days: int = 30) -> list[SharedRecord]:
        today = self._today or datetime.now(timezone.utc).date()
        return sorted(
            (
                record
                for record in self._repository.list()
                if record.record_type == RecordType.BILL
                and record.effective_date is not None
                and today <= record.effective_date <= today + timedelta(days=days)
                and record.status not in {Status.PAID, Status.CANCELLED}
            ),
            key=lambda record: record.effective_date or date.max,
        )

    def past_due_bills(self) -> list[SharedRecord]:
        today = self._today or datetime.now(timezone.utc).date()
        return [
            record
            for record in self._repository.list()
            if record.record_type == RecordType.BILL and _is_past_due(record, today)
        ]

    def recent_remits(self, *, days: int = 30) -> list[SharedRecord]:
        today = self._today or datetime.now(timezone.utc).date()
        return sorted(
            (
                record
                for record in self._repository.list()
                if record.record_type == RecordType.REMIT
                and record.effective_date is not None
                and today - timedelta(days=days) <= record.effective_date <= today
            ),
            key=lambda record: record.effective_date or date.min,
            reverse=True,
        )

    def recent_agent_runs(self, *, limit: int = 20) -> list[AgentRunRecord]:
        return sorted(self._repository.list_agent_runs(), key=lambda run: run.started_at, reverse=True)[:limit]

    def failed_agent_runs(self) -> list[AgentRunRecord]:
        return [run for run in self.recent_agent_runs() if run.status == Status.FAILED]

    def agent_health(self) -> dict[str, Any]:
        runs = self.recent_agent_runs()
        return {
            "recent_runs": [run.to_dict() for run in runs],
            "failed_runs": [run.to_dict() for run in runs if run.status == Status.FAILED],
        }

    def records_by_source(self, source_system: SourceSystem) -> list[SharedRecord]:
        return [record for record in self._repository.list() if record.source_system == source_system]

    def records_by_date_range(self, start: date, end: date) -> list[SharedRecord]:
        return [
            record
            for record in self._repository.list()
            if record.effective_date is not None and start <= record.effective_date <= end
        ]


def _review_reasons(record: SharedRecord) -> list[str]:
    reasons = []
    if record.status == Status.NEEDS_REVIEW:
        reasons.append("Status requires review")
    if record.review_status in UNRESOLVED_REVIEW_STATUSES:
        reasons.append("Review is pending")
    if bool(record.action_required):
        reasons.append(record.action_required or "Action is required")
    if record.confidence is not None and record.confidence < CONFIDENCE_REVIEW_THRESHOLD:
        reasons.append(f"Confidence is below {CONFIDENCE_REVIEW_THRESHOLD:.0%}")
    return list(dict.fromkeys(reasons))


def _review_item(record: SharedRecord, today: date) -> dict[str, Any]:
    reasons = _review_reasons(record)
    return {
        "id": record.id,
        "record_type": record.record_type.value,
        "source_system": record.source_system.value,
        "title": record.title,
        "summary": record.summary,
        "amount": str(record.amount) if record.amount is not None else None,
        "effective_date": record.effective_date.isoformat() if record.effective_date else None,
        "priority": record.priority.value,
        "status": record.status.value,
        "review_status": record.review_status.value,
        "action_required": bool(record.action_required),
        "confidence": record.confidence,
        "owner": record.owner,
        "source_url": record.source_url,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "metadata": {key: value for key, value in record.metadata.items() if key in SAFE_METADATA_FIELDS},
        "review_reason": "; ".join(reasons),
        "past_due": _is_past_due(record, today),
    }


def _failed_run_review_item(run: AgentRunRecord, today: date) -> dict[str, Any]:
    return {
        "id": f"agent-run:{run.run_id}",
        "record_type": RecordType.AGENT_RUN.value,
        "source_system": SourceSystem.SQLITE.value,
        "title": f"{run.agent_name} failed",
        "summary": run.error_message,
        "amount": None,
        "effective_date": run.started_at.date().isoformat(),
        "priority": Priority.HIGH.value,
        "status": Status.FAILED.value,
        "review_status": ReviewStatus.PENDING.value,
        "action_required": True,
        "confidence": None,
        "owner": None,
        "source_url": None,
        "created_at": run.started_at.isoformat(),
        "updated_at": (run.completed_at or run.started_at).isoformat(),
        "metadata": {
            "agent_name": run.agent_name,
            "dry_run": run.dry_run,
            "records_flagged_for_review": run.records_flagged_for_review,
        },
        "review_reason": "Agent run failed and requires operational review",
        "past_due": run.started_at.date() < today,
    }


def _matches_review_filters(item: dict[str, Any], filters: ReviewQueueFilters | None) -> bool:
    if filters is None:
        return True
    if filters.record_type and item["record_type"] != filters.record_type.value:
        return False
    if filters.source_system and item["source_system"] != filters.source_system.value:
        return False
    if filters.priority and item["priority"] != filters.priority.value:
        return False
    if filters.review_status and item["review_status"] != filters.review_status.value:
        return False
    if filters.action_required is not None and item["action_required"] != filters.action_required:
        return False
    effective_date = date.fromisoformat(item["effective_date"]) if item["effective_date"] else None
    if filters.date_from and (effective_date is None or effective_date < filters.date_from):
        return False
    if filters.date_to and (effective_date is None or effective_date > filters.date_to):
        return False
    return True


def _review_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    priority_rank = {Priority.CRITICAL.value: 0, Priority.HIGH.value: 1}
    return (
        priority_rank.get(item["priority"], 2),
        0 if item["past_due"] or item["status"] == Status.FAILED.value else 1,
        item["created_at"],
    )


def _is_past_due(record: SharedRecord, today: date) -> bool:
    return (
        record.status == Status.PAST_DUE
        or (
            record.record_type == RecordType.BILL
            and record.effective_date is not None
            and record.effective_date < today
            and record.status not in {Status.PAID, Status.CANCELLED, Status.COMPLETED}
        )
    )


def _bill_total(records: list[SharedRecord], start: date, end: date) -> str:
    return _decimal_sum(
        record.amount
        for record in records
        if record.effective_date is not None
        and start <= record.effective_date <= end
        and record.status not in {Status.PAID, Status.CANCELLED}
    )


def _decimal_sum(values: Any) -> str:
    return str(sum((value for value in values if value is not None), Decimal("0.00")))


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
