from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date

from .models import AgentRunRecord, RecordType, ReviewStatus, SharedRecord, SourceSystem, Status, utc_now


@dataclass(frozen=True)
class RecordFilters:
    record_type: RecordType | None = None
    source_system: SourceSystem | None = None
    status: Status | None = None
    review_status: ReviewStatus | None = None
    owner: str | None = None
    effective_date_from: date | None = None
    effective_date_to: date | None = None


class SharedRecordRepository(ABC):
    @abstractmethod
    def upsert(self, record: SharedRecord) -> SharedRecord: ...

    @abstractmethod
    def get(self, record_id: str) -> SharedRecord | None: ...

    @abstractmethod
    def get_by_source(self, source_system: SourceSystem, source_record_id: str) -> SharedRecord | None: ...

    @abstractmethod
    def get_by_idempotency_key(self, idempotency_key: str) -> SharedRecord | None: ...

    @abstractmethod
    def list(self, filters: RecordFilters | None = None) -> list[SharedRecord]: ...

    @abstractmethod
    def mark_reviewed(
        self,
        record_id: str,
        review_status: ReviewStatus = ReviewStatus.APPROVED,
        reviewer: str | None = None,
    ) -> SharedRecord: ...

    @abstractmethod
    def update_status(self, record_id: str, status: Status) -> SharedRecord: ...

    @abstractmethod
    def record_agent_run(self, run: AgentRunRecord) -> AgentRunRecord: ...

    @abstractmethod
    def list_agent_runs(self) -> list[AgentRunRecord]: ...


class InMemorySharedRecordRepository(SharedRecordRepository):
    def __init__(self) -> None:
        self._records: dict[str, SharedRecord] = {}
        self._source_index: dict[tuple[SourceSystem, str], str] = {}
        self._idempotency_index: dict[str, str] = {}
        self._agent_runs: dict[str, AgentRunRecord] = {}

    def upsert(self, record: SharedRecord) -> SharedRecord:
        existing = None
        if record.idempotency_key:
            existing = self.get_by_idempotency_key(record.idempotency_key)
        if existing is None:
            existing = self.get_by_source(record.source_system, record.source_record_id)
        if existing is not None and existing.id != record.id:
            record = replace(record, id=existing.id, created_at=existing.created_at)
        old = self._records.get(record.id)
        if old is not None:
            self._remove_indexes(old)
        self._records[record.id] = record
        self._source_index[(record.source_system, record.source_record_id)] = record.id
        if record.idempotency_key:
            self._idempotency_index[record.idempotency_key] = record.id
        return record

    def get(self, record_id: str) -> SharedRecord | None:
        return self._records.get(record_id)

    def get_by_source(self, source_system: SourceSystem, source_record_id: str) -> SharedRecord | None:
        record_id = self._source_index.get((source_system, source_record_id))
        return self.get(record_id) if record_id else None

    def get_by_idempotency_key(self, idempotency_key: str) -> SharedRecord | None:
        record_id = self._idempotency_index.get(idempotency_key)
        return self.get(record_id) if record_id else None

    def list(self, filters: RecordFilters | None = None) -> list[SharedRecord]:
        records = list(self._records.values())
        if filters is None:
            return records
        return [record for record in records if _matches(record, filters)]

    def mark_reviewed(
        self,
        record_id: str,
        review_status: ReviewStatus = ReviewStatus.APPROVED,
        reviewer: str | None = None,
    ) -> SharedRecord:
        record = self._required(record_id)
        metadata = deepcopy(record.metadata)
        metadata["reviewed_at"] = utc_now().isoformat()
        if reviewer:
            metadata["reviewed_by"] = reviewer
        return self.upsert(replace(record, review_status=review_status, metadata=metadata, updated_at=utc_now()))

    def update_status(self, record_id: str, status: Status) -> SharedRecord:
        record = self._required(record_id)
        return self.upsert(replace(record, status=status, updated_at=utc_now()))

    def record_agent_run(self, run: AgentRunRecord) -> AgentRunRecord:
        self._agent_runs[run.run_id] = run
        return run

    def get_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self._agent_runs.get(run_id)

    def list_agent_runs(self) -> list[AgentRunRecord]:
        return list(self._agent_runs.values())

    def _required(self, record_id: str) -> SharedRecord:
        record = self.get(record_id)
        if record is None:
            raise KeyError(f"Shared record not found: {record_id}")
        return record

    def _remove_indexes(self, record: SharedRecord) -> None:
        self._source_index.pop((record.source_system, record.source_record_id), None)
        if record.idempotency_key:
            self._idempotency_index.pop(record.idempotency_key, None)


def _matches(record: SharedRecord, filters: RecordFilters) -> bool:
    if filters.record_type is not None and record.record_type != filters.record_type:
        return False
    if filters.source_system is not None and record.source_system != filters.source_system:
        return False
    if filters.status is not None and record.status != filters.status:
        return False
    if filters.review_status is not None and record.review_status != filters.review_status:
        return False
    if filters.owner is not None and record.owner != filters.owner:
        return False
    if filters.effective_date_from and (record.effective_date is None or record.effective_date < filters.effective_date_from):
        return False
    if filters.effective_date_to and (record.effective_date is None or record.effective_date > filters.effective_date_to):
        return False
    return True
