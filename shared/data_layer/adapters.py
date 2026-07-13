from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from agents.cash_flow_hq.models import BillCandidate
from agents.icr_remit_agent.models import ICRRemitResult

from .idempotency import cash_flow_idempotency_key, icr_remit_idempotency_key
from .models import Priority, RecordType, ReviewStatus, SharedRecord, SourceSystem, Status


CASH_FLOW_STATUS_MAP = {
    "upcoming": Status.UPCOMING,
    "needs review": Status.NEEDS_REVIEW,
    "paid": Status.PAID,
    "past due": Status.PAST_DUE,
}

ICR_STATUS_MAP = {
    "pending": Status.NEW,
    "completed": Status.COMPLETED,
    "failed": Status.FAILED,
}


def normalize_cash_flow_bill(
    candidate: BillCandidate,
    *,
    notion_page_id: str | None = None,
    duplicate_key: str | None = None,
) -> SharedRecord:
    source_record_id = candidate.internet_message_id or candidate.message_id
    key = cash_flow_idempotency_key(
        candidate.vendor_payee,
        candidate.amount,
        candidate.due_date,
        source_record_id,
    )
    status = CASH_FLOW_STATUS_MAP.get(candidate.status.strip().casefold(), Status.NEW)
    review_status = ReviewStatus.PENDING if status == Status.NEEDS_REVIEW else ReviewStatus.NOT_REQUIRED
    payment_type = (candidate.payment_type or "").strip()
    metadata = {
        "vendor": candidate.vendor_payee,
        "invoice_number": candidate.invoice_number,
        "category": candidate.category,
        "payment_method": payment_type or None,
        "autopay": payment_type.casefold() in {"auto pay", "autopay"},
        "existing_status": candidate.status,
        "review_reasons": list(candidate.review_reasons),
        "field_sources": dict(candidate.field_sources),
        "message_id": candidate.message_id,
        "internet_message_id": candidate.internet_message_id,
        "notion_page_id": notion_page_id,
        "duplicate_key": duplicate_key or key,
        "original_confidence": candidate.confidence,
    }
    return SharedRecord(
        id=_stable_record_id("cash_flow_hq", key),
        record_type=RecordType.BILL,
        source_system=SourceSystem.OUTLOOK,
        source_record_id=source_record_id,
        source_url=candidate.email_link or None,
        effective_date=candidate.due_date,
        status=status,
        priority=Priority.HIGH if status == Status.NEEDS_REVIEW else Priority.NORMAL,
        action_required=candidate.review_reason_text or None,
        review_status=review_status,
        confidence=_numeric_confidence(candidate.confidence),
        amount=candidate.amount,
        title=candidate.expense_name,
        summary=candidate.notes or None,
        metadata=metadata,
        idempotency_key=key,
    )


def normalize_icr_remit(
    result: ICRRemitResult,
    *,
    production_record_url: str | None = None,
    outlook_draft_reference: str | None = None,
    duplicate_key: str | None = None,
) -> SharedRecord:
    source_file = Path(result.file_path)
    source_record_id = f"{result.broker}:{result.remit_week.isoformat()}:{source_file.name}"
    key = icr_remit_idempotency_key(
        f"{result.broker}:{result.contact}",
        result.remit_week,
        result.total_collected,
        source_file.name,
    )
    metadata = {
        "broker": result.broker,
        "contact": result.contact,
        "due_to_agency": str(result.due_to_agency),
        "due_to_client": str(result.due_to_client),
        "total_collected": str(result.total_collected),
        "remit_week": result.remit_week.isoformat(),
        "week_ending": result.week_ending.isoformat(),
        "source_file": str(source_file),
        "production_record_url": production_record_url,
        "outlook_draft_reference": outlook_draft_reference,
        "duplicate_key": duplicate_key or f"{result.broker}|{result.remit_week.isoformat()}|{source_file.name}",
        "existing_status": result.status,
    }
    return SharedRecord(
        id=_stable_record_id("icr_remit", key),
        record_type=RecordType.REMIT,
        source_system=SourceSystem.LOCAL_FILE,
        source_record_id=source_record_id,
        source_url=production_record_url,
        effective_date=result.remit_week,
        status=ICR_STATUS_MAP.get(result.status.strip().casefold(), Status.NEW),
        owner=result.contact or None,
        amount=result.due_to_client,
        title=f"{result.broker} Weekly Remit - {result.remit_week.isoformat()}",
        summary=result.notes or None,
        metadata=metadata,
        idempotency_key=key,
    )


def _stable_record_id(namespace: str, idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"ucm:{namespace}:{idempotency_key}"))


def _numeric_confidence(value: str) -> float | None:
    try:
        confidence = Decimal(value.strip().rstrip("%"))
    except (InvalidOperation, AttributeError):
        return None
    if confidence > 1:
        confidence /= Decimal("100")
    numeric = float(confidence)
    return numeric if 0 <= numeric <= 1 else None
