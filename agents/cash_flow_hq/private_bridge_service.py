from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from shared.data_layer.models import Priority, RecordType, ReviewStatus, SharedRecord, SourceSystem, Status
from shared.data_layer.repository import RecordFilters, SharedRecordRepository
from shared.data_layer.sqlite_repository import SQLiteSharedRecordRepository


# Statuses that represent non-actionable bills
EXCLUDED_STATUSES = {Status.PAID, Status.CANCELLED, Status.COMPLETED, Status.FAILED}


class CashFlowHqPrivateBridgeService:
    """Private bridge service for Cash Flow HQ bill search and payment operations."""
    
    def __init__(
        self,
        database_path: str,
        repository: SharedRecordRepository | None = None,
        planner: object | None = None,
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

        self.planner = planner
        self.now = now or (lambda: datetime.now(timezone.utc))
    
    def search(self, query: str) -> dict:
        """Search for actionable unpaid bills matching query (case-insensitive, deterministic ordering)."""
        if not query or not query.strip():
            return {"status": "ok", "matches": []}
        
        # Get all bills, filter to actionable only
        bills = self.repository.list(RecordFilters(record_type=RecordType.BILL))
        actionable_bills = [bill for bill in bills if bill.status not in EXCLUDED_STATUSES]
        
        # Case-insensitive search
        query_lower = query.strip().lower()
        matches = []
        
        for bill in actionable_bills:
            if query_lower in bill.title.lower():
                matches.append({
                    "record_ref": bill.id,
                    "bill_name": bill.title,
                    "amount": str(bill.amount) if bill.amount else "0.00",
                    "due_date": bill.effective_date.isoformat() if bill.effective_date else "",
                    "current_status": bill.status.value,
                })
        
        # Deterministic ordering: by due_date, then title
        matches.sort(key=lambda m: (m["due_date"] or "9999-12-31", m["bill_name"]))
        
        # Apply result limit
        matches = matches[:10]
        
        return {"status": "ok", "matches": matches}
    
    def mark_paid(self, record_ref: str) -> dict:
        """Mark a bill as paid. Raises KeyError if not found, ValueError if already paid."""
        # Get record - returns None if not found
        record = self.repository.get(record_ref)
        if record is None:
            raise KeyError("Bill not found.")
        
        # Check if already paid
        if record.status == Status.PAID:
            raise ValueError("Bill is already marked paid.")
        if self.planner is None:
            raise RuntimeError("Planner service is unavailable.")
        
        # Update status - this will raise KeyError if record disappears
        try:
            updated = self.repository.update_status(record_ref, Status.PAID)
        except KeyError:
            raise KeyError("Bill not found.")
        
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

    def create_incoming_weekly_remit(self, amount: str | Decimal | None, replace_existing: bool = False) -> dict:
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
        return {
            "status": "updated" if candidates else "created",
            "record": self._record_payload(saved),
        }

    def mark_incoming_weekly_remit_received(self, amount: str | Decimal | None = None) -> dict:
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
        if self.planner is None:
            raise RuntimeError("Planner service is unavailable.")
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
