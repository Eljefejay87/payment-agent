"""Compatibility contracts for normalized UCM operational records."""

from .adapters import normalize_cash_flow_bill, normalize_icr_remit
from .idempotency import (
    cash_flow_idempotency_key,
    generate_idempotency_key,
    icr_remit_idempotency_key,
)
from .models import (
    AgentRunRecord,
    Priority,
    RecordType,
    ReviewAuditEvent,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    Status,
)
from .repository import InMemorySharedRecordRepository, RecordFilters, SharedRecordRepository
from .sqlite_repository import SQLiteSharedRecordRepository

__all__ = [
    "AgentRunRecord",
    "InMemorySharedRecordRepository",
    "Priority",
    "RecordFilters",
    "RecordType",
    "ReviewAuditEvent",
    "ReviewStatus",
    "SharedRecord",
    "SharedRecordRepository",
    "SQLiteSharedRecordRepository",
    "SourceSystem",
    "Status",
    "cash_flow_idempotency_key",
    "generate_idempotency_key",
    "icr_remit_idempotency_key",
    "normalize_cash_flow_bill",
    "normalize_icr_remit",
]
