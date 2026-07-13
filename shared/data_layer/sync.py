from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from agents.cash_flow_hq.config import CashFlowHQSettings
from agents.cash_flow_hq.service import CashFlowHQService
from agents.icr_remit_agent.database import ICRRemitDatabase
from agents.weekly_remit_agent.config import RemitSettings

from .adapters import normalize_cash_flow_notion_page, normalize_icr_remit
from .models import AgentRunRecord, ReviewStatus, SharedRecord, SourceSystem, Status, utc_now
from .repository import SharedRecordRepository


TERMINAL_REVIEW_STATUSES = {
    ReviewStatus.APPROVED,
    ReviewStatus.REJECTED,
    ReviewStatus.RESOLVED,
}
REVIEW_METADATA_FIELDS = {"reviewed_at", "reviewed_by", "review_action", "review_reason"}


@dataclass(frozen=True)
class SourceLoadResult:
    records: tuple[SharedRecord, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncPlanItem:
    action: str
    record: SharedRecord
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "id": self.record.id,
            "record_type": self.record.record_type.value,
            "source_system": self.record.source_system.value,
            "title": self.record.title,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SyncPlan:
    items: tuple[SyncPlanItem, ...]
    source_errors: tuple[str, ...] = ()

    @property
    def conflicts(self) -> tuple[SyncPlanItem, ...]:
        return tuple(item for item in self.items if item.action == "conflict")

    def to_dict(self, *, applied: bool = False) -> dict:
        counts = {"create": 0, "update": 0, "skip": 0, "conflict": 0, "error": len(self.source_errors)}
        for item in self.items:
            counts[item.action] += 1
        return {
            "mode": "apply" if applied else "dry-run",
            "counts": counts,
            "items": [item.to_dict() for item in self.items],
            "source_errors": list(self.source_errors),
        }


class SharedDataSyncService:
    """Plan and apply source-to-shared synchronization without writing source systems."""

    def __init__(self, repository: SharedRecordRepository) -> None:
        self._repository = repository

    def plan(self, incoming_records: Iterable[SharedRecord], *, source_errors: Iterable[str] = ()) -> SyncPlan:
        items = []
        seen_sources: set[tuple[str, str]] = set()
        for incoming in incoming_records:
            source_identity = (incoming.source_system.value, incoming.source_record_id)
            if source_identity in seen_sources:
                items.append(SyncPlanItem("conflict", incoming, "Duplicate source identity in sync input"))
                continue
            seen_sources.add(source_identity)
            existing = self._repository.get_by_source(incoming.source_system, incoming.source_record_id)
            if existing is None and incoming.idempotency_key:
                existing = self._repository.get_by_idempotency_key(incoming.idempotency_key)
            if existing is None:
                items.append(SyncPlanItem("create", incoming, "New normalized source record"))
                continue
            if _source_payload(existing) == _source_payload(incoming):
                items.append(SyncPlanItem("skip", existing, "No source-owned fields changed"))
                continue
            if existing.review_status in TERMINAL_REVIEW_STATUSES:
                items.append(
                    SyncPlanItem(
                        "conflict",
                        existing,
                        "Source changed after a terminal human review decision",
                    )
                )
                continue
            updated = replace(
                incoming,
                id=existing.id,
                created_at=existing.created_at,
                updated_at=utc_now(),
            )
            items.append(SyncPlanItem("update", updated, "Source-owned fields changed"))
        return SyncPlan(tuple(items), tuple(source_errors))

    def apply(self, plan: SyncPlan) -> dict:
        if plan.source_errors:
            raise RuntimeError("Sync apply blocked because one or more source records failed to load.")
        if plan.conflicts:
            raise RuntimeError("Sync apply blocked because reconciliation conflicts require review.")
        writes = [item.record for item in plan.items if item.action in {"create", "update"}]
        self._repository.upsert_many(writes)
        return plan.to_dict(applied=True)


class ScheduledSharedDataSync:
    """Run guarded source synchronization and persist operational run history."""

    def __init__(
        self,
        repository: SharedRecordRepository,
        cash_flow_settings: CashFlowHQSettings,
        remit_settings: RemitSettings,
    ) -> None:
        self.repository = repository
        self.cash_flow_settings = cash_flow_settings
        self.remit_settings = remit_settings

    def run_once(self, *, source: str = "all", limit: int = 100) -> dict:
        started_at = datetime.now(timezone.utc)
        run_id = str(uuid4())
        records = []
        errors = []
        counts = {"create": 0, "update": 0, "skip": 0, "conflict": 0, "error": 0}
        try:
            if source in {"cash-flow", "all"}:
                loaded = load_cash_flow_records(self.cash_flow_settings, limit=limit)
                records.extend(loaded.records)
                errors.extend(loaded.errors)
            if source in {"icr", "all"}:
                loaded = load_icr_records(self.remit_settings, limit=limit)
                records.extend(loaded.records)
                errors.extend(loaded.errors)
            plan = SharedDataSyncService(self.repository).plan(records, source_errors=errors)
            counts = plan.to_dict()["counts"]
            report = SharedDataSyncService(self.repository).apply(plan)
            status = Status.COMPLETED
            error_message = None
        except Exception as exc:
            status = Status.FAILED
            error_message = str(exc)[:500]
            if not counts["conflict"] and not counts["error"]:
                counts["error"] = 1
            report = {"mode": "apply", "counts": counts, "error": error_message}
        completed_at = datetime.now(timezone.utc)
        run = AgentRunRecord(
            agent_name="shared_data_sync",
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            records_found=len(records),
            records_created=counts["create"],
            records_updated=counts["update"],
            records_skipped=counts["skip"],
            records_flagged_for_review=counts["conflict"] + counts["error"],
            error_message=error_message,
            dry_run=False,
            external_services_used=_services_for_source(source),
        )
        self.repository.record_agent_run(run)
        report["run"] = run.to_dict()
        return report


def _services_for_source(source: str) -> tuple[SourceSystem, ...]:
    services = [SourceSystem.SQLITE]
    if source in {"cash-flow", "all"}:
        services.append(SourceSystem.NOTION)
    if source in {"icr", "all"}:
        services.append(SourceSystem.LOCAL_FILE)
    return tuple(services)


def load_cash_flow_records(settings: CashFlowHQSettings, *, limit: int | None = None) -> SourceLoadResult:
    if not settings.notion_api_key:
        raise RuntimeError("NOTION_API_KEY is required for Cash Flow HQ synchronization.")
    service = CashFlowHQService(settings)
    foundation = service.get_existing_foundation()
    pages = []
    cursor = ""
    while True:
        page_size = min(100, limit - len(pages)) if limit is not None else 100
        if page_size <= 0:
            break
        payload: dict[str, object] = {"page_size": page_size}
        if cursor:
            payload["start_cursor"] = cursor
        response = service.notion.request(
            "POST",
            f"/data_sources/{foundation['data_source_id']}/query",
            json=payload,
        )
        pages.extend(response.get("results", []))
        cursor = response.get("next_cursor") or ""
        if not response.get("has_more") or not cursor or (limit is not None and len(pages) >= limit):
            break
    return _normalize_pages(pages[:limit] if limit is not None else pages)


def load_icr_records(settings: RemitSettings, *, limit: int | None = None) -> SourceLoadResult:
    database = ICRRemitDatabase(settings.database_path)
    records = []
    errors = []
    for result in database.list_imports(limit=limit):
        try:
            records.append(normalize_icr_remit(result))
        except Exception as exc:
            errors.append(f"ICR {result.file_path.name}: {exc}")
    return SourceLoadResult(tuple(records), tuple(errors))


def _normalize_pages(pages: list[dict]) -> SourceLoadResult:
    records = []
    errors = []
    for page in pages:
        try:
            records.append(normalize_cash_flow_notion_page(page))
        except Exception as exc:
            page_id = str(page.get("id") or "unknown")
            errors.append(f"Cash Flow HQ page {page_id}: {exc}")
    return SourceLoadResult(tuple(records), tuple(errors))


def _source_payload(record: SharedRecord) -> dict:
    metadata = {
        key: value
        for key, value in record.metadata.items()
        if key not in REVIEW_METADATA_FIELDS
    }
    return {
        "record_type": record.record_type.value,
        "source_system": record.source_system.value,
        "source_record_id": record.source_record_id,
        "source_url": record.source_url,
        "effective_date": record.effective_date.isoformat() if record.effective_date else None,
        "status": record.status.value,
        "owner": record.owner,
        "priority": record.priority.value,
        "action_required": record.action_required,
        "confidence": record.confidence,
        "amount": str(record.amount) if record.amount is not None else None,
        "currency": record.currency,
        "title": record.title,
        "summary": record.summary,
        "metadata": metadata,
        "idempotency_key": record.idempotency_key,
        "schema_version": record.schema_version,
    }
