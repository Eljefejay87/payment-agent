from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


SCHEMA_VERSION = 1


class RecordType(str, Enum):
    BILL = "bill"
    REMIT = "remit"
    VOICEMAIL = "voicemail"
    COLLECTOR_METRIC = "collector_metric"
    NOTIFICATION = "notification"
    REVIEW_ITEM = "review_item"
    AGENT_RUN = "agent_run"


class Status(str, Enum):
    NEW = "new"
    UPCOMING = "upcoming"
    DUE = "due"
    PAST_DUE = "past_due"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class SourceSystem(str, Enum):
    NOTION = "notion"
    OUTLOOK = "outlook"
    TEAMS = "teams"
    LOCAL_FILE = "local_file"
    SQLITE = "sqlite"
    GOOGLE_SHEETS = "google_sheets"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SharedRecord:
    id: str
    record_type: RecordType
    source_system: SourceSystem
    source_record_id: str
    title: str
    source_url: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    effective_date: date | None = None
    status: Status = Status.NEW
    owner: str | None = None
    priority: Priority = Priority.NORMAL
    action_required: str | None = None
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    confidence: float | None = None
    amount: Decimal | None = None
    currency: str = "USD"
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.source_record_id.strip() or not self.title.strip():
            raise ValueError("id, source_record_id, and title are required.")
        _validate_enum(self.record_type, RecordType, "record_type")
        _validate_enum(self.source_system, SourceSystem, "source_system")
        _validate_enum(self.status, Status, "status")
        _validate_enum(self.priority, Priority, "priority")
        _validate_enum(self.review_status, ReviewStatus, "review_status")
        _validate_aware(self.created_at, "created_at")
        _validate_aware(self.updated_at, "updated_at")
        if self.amount is not None and not isinstance(self.amount, Decimal):
            raise TypeError("amount must be Decimal or None.")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1.")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_type": self.record_type.value,
            "source_system": self.source_system.value,
            "source_record_id": self.source_record_id,
            "source_url": self.source_url,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "status": self.status.value,
            "owner": self.owner,
            "priority": self.priority.value,
            "action_required": self.action_required,
            "review_status": self.review_status.value,
            "confidence": self.confidence,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "title": self.title,
            "summary": self.summary,
            "metadata": deepcopy(self.metadata),
            "idempotency_key": self.idempotency_key,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SharedRecord":
        return cls(
            id=payload["id"],
            record_type=RecordType(payload["record_type"]),
            source_system=SourceSystem(payload["source_system"]),
            source_record_id=payload["source_record_id"],
            source_url=payload.get("source_url"),
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
            effective_date=date.fromisoformat(payload["effective_date"]) if payload.get("effective_date") else None,
            status=Status(payload.get("status", Status.NEW.value)),
            owner=payload.get("owner"),
            priority=Priority(payload.get("priority", Priority.NORMAL.value)),
            action_required=payload.get("action_required"),
            review_status=ReviewStatus(payload.get("review_status", ReviewStatus.NOT_REQUIRED.value)),
            confidence=payload.get("confidence"),
            amount=Decimal(payload["amount"]) if payload.get("amount") is not None else None,
            currency=payload.get("currency", "USD"),
            title=payload["title"],
            summary=payload.get("summary"),
            metadata=deepcopy(payload.get("metadata", {})),
            idempotency_key=payload.get("idempotency_key"),
            schema_version=payload.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class AgentRunRecord:
    agent_name: str
    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: Status = Status.IN_PROGRESS
    records_found: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_flagged_for_review: int = 0
    error_message: str | None = None
    dry_run: bool = False
    external_services_used: tuple[SourceSystem, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.agent_name.strip() or not self.run_id.strip():
            raise ValueError("agent_name and run_id are required.")
        _validate_enum(self.status, Status, "status")
        _validate_aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _validate_aware(self.completed_at, "completed_at")
        for field_name in (
            "records_found",
            "records_created",
            "records_updated",
            "records_skipped",
            "records_flagged_for_review",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} cannot be negative.")
        for service in self.external_services_used:
            _validate_enum(service, SourceSystem, "external_services_used")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status.value,
            "records_found": self.records_found,
            "records_created": self.records_created,
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "records_flagged_for_review": self.records_flagged_for_review,
            "error_message": self.error_message,
            "dry_run": self.dry_run,
            "external_services_used": [service.value for service in self.external_services_used],
            "schema_version": self.schema_version,
        }


def _validate_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field_name} must be {enum_type.__name__}.")


def _validate_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
