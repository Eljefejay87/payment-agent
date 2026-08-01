from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from shared.data_layer.models import RecordType, Status
from shared.data_layer.repository import RecordFilters, SharedRecordRepository
from shared.data_layer.sqlite_repository import SQLiteSharedRecordRepository
from agents.cash_flow_hq.config import load_cash_flow_settings
from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerService
from agents.weekly_remit_agent.config import load_remit_settings


# Statuses that represent non-actionable bills
EXCLUDED_STATUSES = {Status.PAID, Status.CANCELLED, Status.COMPLETED, Status.FAILED}


class StaleCashFlowRecordError(ValueError):
    """Raised when a confirmed bill no longer has the status the user reviewed."""


class CashFlowHqPrivateBridgeService:
    """Private bridge service for Cash Flow HQ bill search and payment operations."""
    
    def __init__(
        self,
        database_path: str,
        repository: SharedRecordRepository | None = None,
        planner: WeeklyCashPlannerService | None = None,
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
