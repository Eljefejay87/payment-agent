from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from agents.cash_flow_hq.models import BillCandidate
from agents.icr_remit_agent.models import ICRRemitResult

from .idempotency import cash_flow_idempotency_key, generate_idempotency_key, icr_remit_idempotency_key
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


def normalize_cash_flow_notion_page(page: dict) -> SharedRecord:
    """Normalize an existing Cash Flow HQ Notion page without modifying Notion."""
    properties = page.get("properties") or {}
    page_id = str(page.get("id") or "").strip()
    if not page_id:
        raise ValueError("Cash Flow HQ page id is required.")
    vendor = _notion_text(properties.get("Vendor / Payee"))
    title = _notion_title(properties.get("Expense Name")) or vendor or "Cash Flow HQ bill"
    amount_value = (properties.get("Amount") or {}).get("number")
    amount = Decimal(str(amount_value)) if amount_value is not None else None
    due_date = _notion_date(properties.get("Due Date"))
    existing_status = _notion_select(properties.get("Status"))
    due_status = _notion_formula_string(properties.get("Due Status"))
    action_required = _normalize_action_required(
        _notion_formula_string(properties.get("Action Required"))
    )
    status = CASH_FLOW_STATUS_MAP.get(existing_status.casefold(), Status.NEW)
    if status not in {Status.PAID, Status.CANCELLED}:
        if due_status.casefold().startswith("past due"):
            status = Status.PAST_DUE
        elif due_status.casefold().startswith("due today"):
            status = Status.DUE
    review_status = (
        ReviewStatus.PENDING
        if status == Status.NEEDS_REVIEW or bool(action_required)
        else ReviewStatus.NOT_REQUIRED
    )
    payment_type = _notion_select(properties.get("Payment Type"))
    metadata = {
        "vendor": vendor or None,
        "category": _notion_select(properties.get("Category")) or None,
        "payment_method": payment_type or None,
        "autopay": payment_type.casefold() in {"auto pay", "autopay"},
        "existing_status": existing_status or None,
        "due_status": due_status or None,
        "notion_page_id": page_id,
        "source": _notion_select(properties.get("Source")) or None,
    }
    created_at = _notion_datetime(page.get("created_time"))
    updated_at = _notion_datetime(page.get("last_edited_time"))
    key = generate_idempotency_key("cash_flow_notion", page_id)
    return SharedRecord(
        id=_stable_record_id("cash_flow_notion", key),
        record_type=RecordType.BILL,
        source_system=SourceSystem.NOTION,
        source_record_id=page_id,
        source_url=page.get("url"),
        created_at=created_at,
        updated_at=updated_at,
        effective_date=due_date,
        status=status,
        priority=Priority.HIGH if review_status == ReviewStatus.PENDING else Priority.NORMAL,
        action_required=action_required,
        review_status=review_status,
        amount=amount,
        title=title,
        summary=_notion_text(properties.get("Notes")) or None,
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


def _notion_title(prop: dict | None) -> str:
    return _notion_rich_text((prop or {}).get("title") or [])


def _notion_text(prop: dict | None) -> str:
    return _notion_rich_text((prop or {}).get("rich_text") or [])


def _notion_rich_text(items: list[dict]) -> str:
    return "".join(str(item.get("plain_text") or "") for item in items).strip()


def _notion_select(prop: dict | None) -> str:
    return str(((prop or {}).get("select") or {}).get("name") or "").strip()


def _notion_formula_string(prop: dict | None) -> str:
    formula = (prop or {}).get("formula") or {}
    return str(formula.get("string") or "").strip() if formula.get("type") == "string" else ""


def _normalize_action_required(value: str) -> str | None:
    cleaned = value.strip()
    normalized = cleaned.casefold()
    if normalized in {"", "no", "none", "n/a", "not required", "no action", "false", "0", "-", "—"}:
        return None
    if normalized in {"yes", "true", "1"}:
        return "Action required"
    return cleaned


def _notion_date(prop: dict | None) -> date | None:
    value = ((prop or {}).get("date") or {}).get("start")
    return date.fromisoformat(str(value)[:10]) if value else None


def _notion_datetime(value: object) -> datetime:
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed
    return datetime.now(timezone.utc)
