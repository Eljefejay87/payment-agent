from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from agents.cash_flow_hq.service import CashFlowHQService, cash_flow_bill_from_page
from shared.data_layer.models import Priority, RecordType, ReviewStatus, SharedRecord, SourceSystem, Status
from shared.data_layer.repository import RecordFilters, SharedRecordRepository
from shared.data_layer.sqlite_repository import SQLiteSharedRecordRepository
from agents.cash_flow_hq.config import load_cash_flow_settings
from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerService, active_business_week
from agents.weekly_remit_agent.config import load_remit_settings


# Statuses that represent non-actionable bills
EXCLUDED_STATUSES = {Status.PAID, Status.CANCELLED, Status.COMPLETED, Status.FAILED}
MAX_BILL_LIST_ITEMS = 50
INCOMING_WEEKLY_REMIT_VENDOR = "NDH"
INCOMING_WEEKLY_REMIT_CATEGORY = "Broker Remit"
INCOMING_WEEKLY_REMIT_SOURCE = "Manual"
INCOMING_WEEKLY_REMIT_NOTE = "Partner supplied expected deposit"


class StaleCashFlowRecordError(ValueError):
    """Raised when a confirmed bill no longer has the status the user reviewed."""


class CashFlowHqPrivateBridgeService:
    """Private bridge service for Cash Flow HQ bill search and payment operations."""
    
    def __init__(
        self,
        database_path: str,
        repository: SharedRecordRepository | None = None,
        planner: WeeklyCashPlannerService | None = None,
        cash_flow: CashFlowHQService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Initialize the bridge service.
        
        Args:
            database_path: Path to shared data SQLite database (used if repository is None)
            repository: Optional repository for dependency injection (testing)
            planner: Optional planner service for dependency injection (testing)
        """
        if repository is not None:
            self.repository = repository
        else:
            self.repository = SQLiteSharedRecordRepository(database_path)
            self.repository.initialize()

        self.cash_flow = cash_flow or CashFlowHQService(load_cash_flow_settings())
        
        if planner is not None:
            self.planner = planner
        else:
            # Load configuration using existing patterns
            settings = load_cash_flow_settings()
            remit_settings = load_remit_settings()
            self.planner = WeeklyCashPlannerService(
                settings.cash_flow_planner_database_path,
                remit_settings.database_path,
            )
        self.now = now or (lambda: datetime.now(timezone.utc))
    
    def search(self, query: str) -> dict:
        """Read-only bill lookup that ranks invoice, vendor, and amount matches without invoking mutation logic."""
        if not query or not query.strip():
            return {"status": "ok", "matches": [], "answer": "No matching bill was found."}

        query_text = str(query).strip()
        query_lower = query_text.lower()
        status_hint = _infer_status_hint(query_text)
        explicit_lookup = any(
            marker in query_lower
            for marker in [
                "show me", "find", "search bills for", "search for", "what is the status of", "status of",
                "is the", "did we pay", "do we still owe", "still due", "still unpaid",
            ]
        )
        query_terms = _extract_query_terms(query_text)
        short_broad_term = bool(query_terms) and len(query_terms) == 1 and len(query_terms[0]) <= 3

        if status_hint == "paid":
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status == Status.PAID
            ]
        elif status_hint == "unpaid":
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status not in EXCLUDED_STATUSES and bill.status != Status.PAID
            ]
        elif status_hint == "due":
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status in {Status.DUE, Status.PAST_DUE, Status.UPCOMING}
            ]
        elif status_hint == "upcoming":
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status == Status.UPCOMING
            ]
        elif status_hint == "needs_review":
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status == Status.NEEDS_REVIEW
            ]
        elif status_hint or explicit_lookup or short_broad_term:
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status not in {Status.CANCELLED, Status.COMPLETED, Status.FAILED}
            ]
        else:
            bills = [
                bill for bill in self.repository.list(RecordFilters(record_type=RecordType.BILL))
                if bill.status not in EXCLUDED_STATUSES
            ]
        if not bills:
            return {"status": "ok", "matches": [], "answer": "No matching bill was found."}

        invoice_values = _extract_invoice_queries(query_text)
        amount_values = _extract_money_queries(query_text)

        scored = []
        for bill in bills:
            bill_title = bill.title or ""
            bill_title_norm = _normalize_text(bill_title)
            bill_invoice = _normalize_text(_bill_invoice_number(bill))
            score = 0
            did_match_identifier = False

            if invoice_values:
                for invoice_value in invoice_values:
                    if invoice_value and invoice_value == bill_invoice:
                        score += 20000
                        did_match_identifier = True
                    elif invoice_value and bill_invoice and (invoice_value in bill_invoice or bill_invoice in invoice_value):
                        score += 12000
                        did_match_identifier = True

            if query_terms:
                for term in query_terms:
                    term_norm = _normalize_text(term)
                    if not term_norm:
                        continue
                    if term_norm == bill_title_norm:
                        score += 15000
                        did_match_identifier = True
                    elif term_norm in bill_title_norm or bill_title_norm in term_norm:
                        score += 6000
                        did_match_identifier = True

            if amount_values:
                for amount_info in amount_values:
                    amount_value = amount_info["value"]
                    if bill.amount is None:
                        continue
                    if _normalize_decimal(bill.amount) == amount_value:
                        score += 12000 if not amount_info["approximate"] else 7000
                        did_match_identifier = True
                    elif amount_info["approximate"] and _is_approximate_amount(bill.amount, amount_value):
                        score += 6000
                        did_match_identifier = True

            if status_hint and did_match_identifier:
                if status_hint == "paid" and bill.status == Status.PAID:
                    score += 2500
                elif status_hint == "unpaid" and bill.status not in {Status.PAID, Status.CANCELLED, Status.COMPLETED, Status.FAILED}:
                    score += 2500
                elif status_hint == "due" and bill.status in {Status.DUE, Status.PAST_DUE, Status.UPCOMING}:
                    score += 2500
                elif status_hint == "upcoming" and bill.status == Status.UPCOMING:
                    score += 2500
                elif status_hint == "needs_review" and bill.status == Status.NEEDS_REVIEW:
                    score += 2500

            if score > 0:
                scored.append((score, bill))

        if not scored:
            return {"status": "ok", "matches": [], "answer": "No matching bill was found."}

        scored.sort(key=lambda item: (-item[0], item[1].effective_date.isoformat() if item[1].effective_date else "9999-12-31", item[1].title.lower()))
        top_matches = [_public_bill_match(bill) for _, bill in scored[:10]]

        if len(top_matches) == 1:
            return {"status": "ok", "matches": top_matches, "answer": _answer_for_single_bill(query_text, scored[0][1])}

        return {"status": "ok", "matches": top_matches, "answer": _answer_for_multiple_bills(query_text, top_matches)}

    def list_bills(self, scope: str) -> dict:
        """Return a sanitized, read-only list of bills for a supported Cash Flow HQ scope."""
        normalized_scope = _normalize_scope(scope)
        if normalized_scope is None:
            raise ValueError("Unsupported bill list scope.")

        bills = self.repository.list(RecordFilters(record_type=RecordType.BILL))
        if normalized_scope == "current_week":
            rows = self._current_week_bill_rows(bills)
        elif normalized_scope == "review":
            rows = [
                _public_bill_from_record(bill) for bill in bills
                if bill.status in {Status.PAST_DUE, Status.NEEDS_REVIEW}
            ]
        elif normalized_scope == "upcoming":
            rows = [
                _public_bill_from_record(bill) for bill in bills
                if bill.status in {Status.UPCOMING, Status.DUE}
            ]
        else:
            rows = [
                _public_bill_from_record(bill) for bill in bills
                if bill.status not in EXCLUDED_STATUSES
            ]

        rows.sort(key=lambda item: (item["due_date"] or "9999-12-31", item["bill_name"]))
        total_count = len(rows)
        return {
            "status": "ok",
            "scope": normalized_scope,
            "bills": rows[:MAX_BILL_LIST_ITEMS],
            "total_count": total_count,
            "truncated": total_count > MAX_BILL_LIST_ITEMS,
        }

    def _current_week_bill_rows(self, bills) -> list[dict]:
        bill_dicts = [
            {
                "status": bill.status.value,
                "due_date": bill.effective_date,
                "amount": bill.amount or Decimal("0"),
                "title": bill.title,
            }
            for bill in bills
        ]
        snapshot = self.planner.jason_snapshot(bill_dicts)
        return [_public_bill_from_planner_row(bill) for bill in snapshot.get("bills_due_before_next_remit", [])]
    
    def mark_paid(self, record_ref: str, expected_status: str) -> dict:
        """Atomically mark a bill paid only if its confirmed status is unchanged."""
        # Get record - returns None if not found
        record = self.repository.get(record_ref)
        if record is None:
            raise KeyError("Bill not found.")

        try:
            confirmed_status = Status(str(expected_status).strip().lower())
        except ValueError as error:
            raise StaleCashFlowRecordError("Bill status confirmation is invalid.") from error

        if record.record_type != RecordType.BILL or record.status in EXCLUDED_STATUSES or record.status != confirmed_status:
            raise StaleCashFlowRecordError("Bill status changed after confirmation.")
        
        # Update status - this will raise KeyError if record disappears
        try:
            updated = self.repository.update_status_if_current(record_ref, confirmed_status, Status.PAID)
        except KeyError:
            raise KeyError("Bill not found.")
        except RuntimeError as error:
            raise StaleCashFlowRecordError("Bill status changed after confirmation.") from error
        
        return {
            "status": "ok",
            "updated": {
                "record_ref": updated.id,
                "bill_name": updated.title,
                "amount": str(updated.amount) if updated.amount else "0.00",
                "due_date": updated.effective_date.isoformat() if updated.effective_date else "",
                "current_status": updated.status.value,
            },
            "planner_summary": self.planner_summary(),
        }
    
    def planner_summary(self) -> dict:
        """
        Return planner summary with calculated financial metrics.
        
        Calculation:
        - operating_cash: from planner (weekly_remit - operating_deficit - jim_remit)
        - current_week_obligations: sum of bills due before next remit
        - overdue_items_requiring_review: sum of past due bills
        - projected_ending_cash: operating_cash - current_week_obligations
        """
        # Get all bills for planner calculation
        bills = self.repository.list(RecordFilters(record_type=RecordType.BILL))
        bill_dicts = [
            {
                "status": bill.status.value,
                "due_date": bill.effective_date,
                "amount": bill.amount or Decimal("0"),
                "title": bill.title,
            }
            for bill in bills
        ]
        
        # Get planner snapshot with bills
        snapshot = self.planner.jason_snapshot(bill_dicts)
        
        # Parse operating cash from formatted string
        operating_cash = _parse_money(snapshot.get("operating_cash", "$0.00"))
        
        # Calculate current week obligations (sum of bills due before next remit)
        current_week_bills = snapshot.get("bills_due_before_next_remit", [])
        current_week_total = sum(
            bill.get("amount", Decimal("0")) for bill in current_week_bills
        )
        
        # Calculate overdue items requiring review (sum of past due bills)
        overdue_total = sum(
            bill.amount for bill in bills
            if bill.status == Status.PAST_DUE and bill.amount
        )
        
        # Calculate projected ending cash: operating_cash - current_week_obligations
        projected_ending = operating_cash - current_week_total
        
        return {
            "operating_cash": _format_money(operating_cash),
            "current_week_obligations": _format_money(current_week_total),
            "overdue_items_requiring_review": _format_money(overdue_total),
            "projected_ending_cash": _format_money(projected_ending),
        }

    def create_incoming_weekly_remit(
        self,
        amount: str | Decimal | None,
        *,
        replace_existing: bool = False,
    ) -> dict:
        week_start = self._current_week_start()
        candidates = self._incoming_weekly_remit_candidates(week_start)
        if len(candidates) > 1:
            return {"status": "conflict", "matches": [self._record_payload(record) for record in candidates]}
        if candidates and candidates[0].status == Status.PAID:
            return {"status": "already_paid", "record": self._record_payload(candidates[0])}
        if candidates and not replace_existing:
            return {"status": "duplicate", "record": self._record_payload(candidates[0])}

        record = self._incoming_weekly_remit_record(week_start, amount, existing=candidates[0] if candidates else None)
        saved = self.repository.upsert(record)
        persisted = self.repository.get(saved.id)
        if persisted is None:
            raise RuntimeError("Incoming weekly remit was not persisted.")
        return {
            "status": "updated" if candidates else "created",
            "record": self._record_payload(persisted),
        }

    def mark_incoming_weekly_remit_received(
        self,
        amount: str | Decimal | None = None,
    ) -> dict:
        week_start = self._current_week_start()
        candidates = self._incoming_weekly_remit_candidates(week_start)
        if not candidates:
            return {"status": "not_found"}
        if len(candidates) > 1:
            return {"status": "conflict", "matches": [self._record_payload(record) for record in candidates]}

        record = candidates[0]
        if record.status == Status.PAID:
            return {"status": "already_paid", "record": self._record_payload(record)}

        updated_amount = self._parse_amount(amount) if amount is not None else record.amount
        updated = self.repository.upsert(
            record.__class__(
                id=record.id,
                record_type=record.record_type,
                source_system=record.source_system,
                source_record_id=record.source_record_id,
                title=record.title,
                source_url=record.source_url,
                created_at=record.created_at,
                updated_at=self.now(),
                effective_date=record.effective_date,
                status=Status.PAID,
                owner=record.owner,
                priority=record.priority,
                action_required=record.action_required,
                review_status=record.review_status,
                confidence=record.confidence,
                amount=updated_amount,
                currency=record.currency,
                summary=record.summary,
                metadata=record.metadata,
                idempotency_key=record.idempotency_key,
                schema_version=record.schema_version,
            )
        )
        return {"status": "paid", "record": self._record_payload(updated)}

    def _current_week_start(self) -> date:
        today = self.now().date()
        return today - timedelta(days=today.weekday())

    def _incoming_weekly_remit_candidates(self, week_start: date) -> list:
        title = self._incoming_weekly_remit_title(week_start)
        bills = self.repository.list(RecordFilters(record_type=RecordType.BILL))
        return [
            bill
            for bill in bills
            if bill.title == title and bill.effective_date == week_start
        ]

    def _incoming_weekly_remit_record(self, week_start: date, amount: str | Decimal | None, existing=None):
        record_id = existing.id if existing is not None else self._incoming_weekly_remit_record_id(week_start)
        amount_value = self._parse_amount(amount) if amount is not None else existing.amount if existing is not None else None
        return SharedRecord(
            id=record_id,
            record_type=RecordType.BILL,
            source_system=existing.source_system if existing is not None else SourceSystem.SQLITE,
            source_record_id=self._incoming_weekly_remit_source_record_id(week_start),
            title=self._incoming_weekly_remit_title(week_start),
            source_url=existing.source_url if existing is not None else None,
            created_at=existing.created_at if existing is not None else self.now(),
            updated_at=self.now(),
            effective_date=week_start,
            status=Status.UPCOMING,
            owner=existing.owner if existing is not None else None,
            priority=existing.priority if existing is not None else Priority.NORMAL,
            action_required=existing.action_required if existing is not None else None,
            review_status=existing.review_status if existing is not None else ReviewStatus.NOT_REQUIRED,
            confidence=existing.confidence if existing is not None else None,
            amount=amount_value,
            currency=existing.currency if existing is not None else "USD",
            summary=existing.summary if existing is not None else None,
            metadata={**(existing.metadata if existing is not None else {}), "bridge": "cash_flow_hq_private", "week_start": week_start.isoformat()},
            idempotency_key=self._incoming_weekly_remit_source_record_id(week_start),
            schema_version=existing.schema_version if existing is not None else 1,
        )

    @staticmethod
    def _parse_amount(amount: str | Decimal | None) -> Decimal | None:
        if amount is None:
            return None
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    @staticmethod
    def _incoming_weekly_remit_source_record_id(week_start: date) -> str:
        return f"incoming-weekly-remit:{week_start.isoformat()}"

    @staticmethod
    def _incoming_weekly_remit_record_id(week_start: date) -> str:
        return f"incoming-weekly-remit-{week_start.isoformat()}"

    @staticmethod
    def _incoming_weekly_remit_title(week_start: date) -> str:
        return f"Incoming Weekly Remit - {week_start.isoformat()}"

    @staticmethod
    def _record_payload(record) -> dict:
        return {
            "record_ref": record.id,
            "bill_name": record.title,
            "amount": str(record.amount) if record.amount is not None else "0.00",
            "due_date": record.effective_date.isoformat() if record.effective_date else "",
            "current_status": record.status.value,
        }


def _parse_money(value: str) -> Decimal:
    """Parse a formatted money string like '$1,234.56' to Decimal."""
    try:
        cleaned = value.strip().replace("$", "").replace(",", "")
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def _format_money(value: Decimal) -> str:
    """Format a Decimal as a money string like '$1,234.56'."""
    return f"${value:,.2f}"


def _incoming_weekly_remit_title(week_start: date) -> str:
    return f"Incoming Weekly Remit - {week_start.isoformat()}"


def _incoming_weekly_remit_record(bill) -> dict:
    return {
        "page_id": bill.page_id,
        "bill_name": bill.expense_name,
        "amount": str(bill.amount) if bill.amount is not None else "",
        "due_date": bill.due_date.isoformat() if bill.due_date else "",
        "status": bill.status or "",
    }


def _received_notes(existing_notes: str | None, expected_amount: Decimal | None, actual_amount: Decimal) -> str:
    base = (existing_notes or INCOMING_WEEKLY_REMIT_NOTE).strip() or INCOMING_WEEKLY_REMIT_NOTE
    if expected_amount is None or expected_amount == actual_amount:
        return base
    adjustment = f"Adjustment: expected ${expected_amount:,.2f}; received ${actual_amount:,.2f}."
    if adjustment in base:
        return base
    return f"{base}\n{adjustment}"[:1800]


def _normalize_scope(scope: str) -> str | None:
    value = str(scope or "").strip().lower().replace("-", "_")
    aliases = {
        "current": "current_week",
        "current_week": "current_week",
        "current_week_obligations": "current_week",
        "this_week": "current_week",
        "review": "review",
        "needs_review": "review",
        "bills_needing_review": "review",
        "upcoming": "upcoming",
        "unpaid": "unpaid",
    }
    return aliases.get(value)


def _normalize_text(value: str | None) -> str:
    text = (value or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _extract_query_terms(query: str) -> list[str]:
    normalized = query.lower()
    for token in [
        "show me", "find", "search bills for", "search for", "show", "list", "what is the status of",
        "status of", "did we pay", "do we still owe", "is the", "still due", "still unpaid",
        "touch", "paid", "pay", "owe", "invoice", "bills", "bill", "the", "a", "an",
    ]:
        normalized = re.sub(rf"\b{re.escape(token)}\b", " ", normalized)
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", normalized)
    terms = [part.strip() for part in cleaned.split() if part.strip()]
    return [term for term in terms if len(term) > 1]


def _infer_status_hint(query: str) -> str | None:
    text = query.lower()
    if any(fragment in text for fragment in ["did we pay", "already paid", "paid", "mark paid"]):
        return "paid"
    if any(fragment in text for fragment in ["still unpaid", "unpaid", "not paid", "still owe", "owe"]):
        return "unpaid"
    if any(fragment in text for fragment in ["still due", "due date", "is the .* due", "due soon"]):
        return "due"
    if "upcoming" in text:
        return "upcoming"
    if "needs review" in text or "needs_review" in text or "review" in text:
        return "needs_review"
    if "cancelled" in text:
        return "cancelled"
    if "completed" in text:
        return "completed"
    if "failed" in text:
        return "failed"
    return None


def _extract_search_terms(query: str) -> list[str]:
    normalized = query.lower()
    for token in ["show me", "find", "search bills for", "search for", "look for", "is the", "is ", "did we pay", "did we", "what is the status of", "status of", "still due", "still unpaid", "do we still owe", "what bills", "list", "show upcoming", "show unpaid", "bill", "invoice", "invoices", "bills", "the"]:
        normalized = normalized.replace(token, " ")
    cleaned = re.sub(r"[^a-z0-9$\.\s]+", " ", normalized)
    terms = [part for part in cleaned.split() if part.strip()]
    return terms


def _extract_invoice_queries(query: str) -> list[str]:
    matches = []
    for pattern in [
        r"\binvoice\s*(?:no\.?|number|#|id)?\s*[:#-]?\s*([a-z0-9-]+)",
        r"\binv\s*(?:no\.?|number|#|id)?\s*[:#-]?\s*([a-z0-9-]+)",
    ]:
        for match in re.finditer(pattern, query, flags=re.IGNORECASE):
            value = match.group(1).strip()
            if value and len(value) >= 3:
                matches.append(_normalize_text(value))
    return matches


def _extract_money_queries(query: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    normalized = query.lower()
    if "invoice" in normalized and re.search(r"\b(?:invoice|inv)\b", normalized):
        for match in re.finditer(r"\$?\s*(\d+(?:\.\d{1,2})?)", query):
            value = Decimal(match.group(1))
            if match.start() >= (normalized.find('invoice') if 'invoice' in normalized else 0):
                continue
            results.append({"value": _normalize_decimal(value), "approximate": False})
        return results

    approximate_markers = ["about", "around", "approximately", "roughly", "close to", "almost"]
    for marker in approximate_markers:
        if marker in normalized:
            for match in re.finditer(r"\$?\s*(\d+(?:\.\d{1,2})?)", query):
                value = Decimal(match.group(1))
                results.append({"value": _normalize_decimal(value), "approximate": True})
            return results

    for match in re.finditer(r"\$?\s*(\d+(?:\.\d{1,2})?)", query):
        value = Decimal(match.group(1))
        results.append({"value": _normalize_decimal(value), "approximate": False})
    return results


def _normalize_decimal(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _is_approximate_amount(actual: Decimal | None, expected: Decimal) -> bool:
    if actual is None:
        return False
    delta = abs(_normalize_decimal(actual) - expected)
    return delta <= Decimal("1.00")


def _bill_invoice_number(bill) -> str:
    metadata = getattr(bill, "metadata", {}) or {}
    invoice_value = metadata.get("invoice_number") or metadata.get("invoice") or metadata.get("invoice_id")
    if invoice_value:
        return str(invoice_value)
    return ""


def _public_bill_match(bill) -> dict:
    return {
        "record_ref": bill.id,
        "bill_name": bill.title,
        "amount": str(bill.amount) if bill.amount else "0.00",
        "due_date": bill.effective_date.isoformat() if bill.effective_date else "",
        "current_status": bill.status.value,
    }


def _answer_for_single_bill(query: str, bill) -> str:
    due_date = bill.effective_date.isoformat() if bill.effective_date else "no due date recorded"
    status = bill.status.value
    amount = f"${bill.amount:,.2f}" if bill.amount else "$0.00"
    lower_query = query.lower()

    if "paid" in lower_query or "pay" in lower_query:
        if bill.status == Status.PAID:
            return f"Yes — {bill.title} is marked as paid. Amount: {amount}. Due date: {due_date}. Stored status: {status}."
        return f"No — {bill.title} is not marked as paid. Amount: {amount}. Due date: {due_date}. Stored status: {status}."

    if any(token in lower_query for token in ["unpaid", "owe", "still owe", "not paid"]):
        if bill.status == Status.PAID:
            return f"No — {bill.title} is already paid. Amount: {amount}. Due date: {due_date}. Stored status: {status}."
        return f"Yes — {bill.title} is still unpaid. Amount: {amount}. Due date: {due_date}. Stored status: {status}."

    if any(token in lower_query for token in ["still due", "due", "due date"]):
        if bill.status == Status.PAID:
            return f"No — {bill.title} is marked paid, so it is not due. Amount: {amount}. Due date: {due_date}. Stored status: {status}."
        conflict_note = ""
        if bill.status == Status.UPCOMING and bill.effective_date and bill.effective_date < date.today():
            conflict_note = f" The record shows a status/date conflict: stored status is {status} with due date {due_date}. I’m reporting the stored values exactly."
        return f"{bill.title} is currently stored as {status} and due {due_date}. Stored status: {status}." + conflict_note

    if "status" in lower_query or "invoice" in lower_query:
        conflict_note = ""
        if bill.status == Status.UPCOMING and bill.effective_date and bill.effective_date < date.today():
            conflict_note = f" The record shows a status/date conflict: stored status is {status} with due date {due_date}. I’m reporting the stored values exactly."
        return f"{bill.title} is {status}. Amount: {amount}. Due date: {due_date}.{conflict_note}"

    return f"I found {bill.title}. Amount: {amount}. Due date: {due_date}. Status: {status}."


def _answer_for_multiple_bills(query: str, matches: list[dict]) -> str:
    query_text = query.strip()
    readable = "; ".join(
        f"{item['bill_name']} (${float(item['amount']):,.2f}, due {item['due_date']}, {item['current_status']})"
        for item in matches
    )
    return f"I found multiple matching bills: {readable}. Which one do you mean?"


def _public_bill_from_record(bill) -> dict:
    return {
        "bill_name": bill.title,
        "amount": str(bill.amount) if bill.amount else "0.00",
        "due_date": bill.effective_date.isoformat() if bill.effective_date else "",
        "status": bill.status.value,
    }


def _public_bill_from_planner_row(row: dict) -> dict:
    due_date = row.get("due_date")
    if hasattr(due_date, "isoformat"):
        due_date = due_date.isoformat()
    amount = row.get("amount", Decimal("0"))
    return {
        "bill_name": str(row.get("title") or row.get("bill_name") or ""),
        "amount": str(amount),
        "due_date": str(due_date or ""),
        "status": str(row.get("status") or ""),
    }
