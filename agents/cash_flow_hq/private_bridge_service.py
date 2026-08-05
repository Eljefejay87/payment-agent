from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from agents.cash_flow_hq.config import load_cash_flow_settings
from agents.cash_flow_hq.service import CashFlowHQService, cash_flow_bill_from_page
from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerService, active_business_week
from agents.weekly_remit_agent.config import load_remit_settings
from shared.data_layer.models import RecordType, Status
from shared.data_layer.repository import RecordFilters, SharedRecordRepository
from shared.data_layer.sqlite_repository import SQLiteSharedRecordRepository


EXCLUDED_STATUSES = {Status.PAID, Status.CANCELLED, Status.COMPLETED, Status.FAILED}
INCOMING_WEEKLY_REMIT_VENDOR = "NDH"
INCOMING_WEEKLY_REMIT_CATEGORY = "Broker Remit"
INCOMING_WEEKLY_REMIT_SOURCE = "Manual"
INCOMING_WEEKLY_REMIT_NOTE = "Partner supplied expected deposit"


class CashFlowHqPrivateBridgeService:
    """Private bridge service for Cash Flow HQ bill search and payment operations."""

    def __init__(
        self,
        database_path: str,
        repository: SharedRecordRepository | None = None,
        planner: WeeklyCashPlannerService | None = None,
        cash_flow: CashFlowHQService | None = None,
    ) -> None:
        if repository is not None:
            self.repository = repository
        else:
            self.repository = SQLiteSharedRecordRepository(database_path)
            self.repository.initialize()

        self.cash_flow = cash_flow or CashFlowHQService(load_cash_flow_settings())

        if planner is not None:
            self.planner = planner
        else:
            settings = load_cash_flow_settings()
            remit_settings = load_remit_settings()
            self.planner = WeeklyCashPlannerService(
                settings.cash_flow_planner_database_path,
                remit_settings.database_path,
            )

    def search(self, query: str) -> dict:
        if not query or not query.strip():
            return {"status": "ok", "matches": []}

        bills = self.repository.list(RecordFilters(record_type=RecordType.BILL))
        actionable_bills = [bill for bill in bills if bill.status not in EXCLUDED_STATUSES]

        query_lower = query.strip().lower()
        matches = []

        for bill in actionable_bills:
            if query_lower in bill.title.lower():
                matches.append(
                    {
                        "record_ref": bill.id,
                        "bill_name": bill.title,
                        "amount": str(bill.amount) if bill.amount else "0.00",
                        "due_date": bill.effective_date.isoformat() if bill.effective_date else "",
                        "current_status": bill.status.value,
                    }
                )

        matches.sort(key=lambda m: (m["due_date"] or "9999-12-31", m["bill_name"]))
        matches = matches[:10]

        return {"status": "ok", "matches": matches}

    def mark_paid(self, record_ref: str) -> dict:
        record = self.repository.get(record_ref)
        if record is None:
            raise KeyError("Bill not found.")

        if record.status == Status.PAID:
            raise ValueError("Bill is already marked paid.")

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

    def planner_summary(self) -> dict:
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

        snapshot = self.planner.jason_snapshot(bill_dicts)
        operating_cash = _parse_money(snapshot.get("operating_cash", "$0.00"))

        current_week_bills = snapshot.get("bills_due_before_next_remit", [])
        current_week_total = sum(bill.get("amount", Decimal("0")) for bill in current_week_bills)

        overdue_total = sum(bill.amount for bill in bills if bill.status == Status.PAST_DUE and bill.amount)
        projected_ending = operating_cash - current_week_total

        return {
            "operating_cash": _format_money(operating_cash),
            "current_week_obligations": _format_money(current_week_total),
            "overdue_items_requiring_review": _format_money(overdue_total),
            "projected_ending_cash": _format_money(projected_ending),
        }

    def create_incoming_weekly_remit(
        self,
        amount: Decimal,
        *,
        replace_existing: bool = False,
        today: date | None = None,
    ) -> dict:
        week_start = active_business_week(today)
        title = _incoming_weekly_remit_title(week_start)
        foundation = self.cash_flow.get_existing_foundation()
        existing = self._find_incoming_weekly_remit(foundation["data_source_id"], title)
        if len(existing) > 1:
            return {"status": "conflict", "week_start": week_start.isoformat(), "title": title}

        due_date = week_start + timedelta(days=2)
        if existing:
            bill = existing[0]
            if not replace_existing:
                return {
                    "status": "duplicate",
                    "week_start": week_start.isoformat(),
                    "title": title,
                    "record": _incoming_weekly_remit_record(bill),
                }
            if (bill.status or "").strip().casefold() == "paid":
                return {
                    "status": "already_paid",
                    "week_start": week_start.isoformat(),
                    "title": title,
                    "record": _incoming_weekly_remit_record(bill),
                }
            self.cash_flow.update_bill_fields(
                bill.page_id,
                expense_name=title,
                vendor_payee=INCOMING_WEEKLY_REMIT_VENDOR,
                amount=amount,
                due_date_value=due_date,
                status="Upcoming",
                category=INCOMING_WEEKLY_REMIT_CATEGORY,
                payment_type="Manual",
                notes=INCOMING_WEEKLY_REMIT_NOTE,
            )
            refreshed = self._find_incoming_weekly_remit(foundation["data_source_id"], title)
            return {
                "status": "updated",
                "week_start": week_start.isoformat(),
                "title": title,
                "record": _incoming_weekly_remit_record(refreshed[0] if refreshed else bill),
            }

        payload = self.cash_flow.create_manual_expense_payload(
            expense_name=title,
            amount=float(amount),
            due_date=due_date.isoformat(),
            vendor_payee=INCOMING_WEEKLY_REMIT_VENDOR,
            category=INCOMING_WEEKLY_REMIT_CATEGORY,
            source=INCOMING_WEEKLY_REMIT_SOURCE,
        )
        payload["Notes"] = {
            "rich_text": [{"type": "text", "text": {"content": INCOMING_WEEKLY_REMIT_NOTE}}]
        }
        page = self.cash_flow.notion.request(
            "POST",
            "/pages",
            json={"parent": {"data_source_id": foundation["data_source_id"]}, "properties": payload},
        )
        created = self._find_incoming_weekly_remit(foundation["data_source_id"], title)
        bill = created[0] if created else cash_flow_bill_from_page(page)
        return {
            "status": "created",
            "week_start": week_start.isoformat(),
            "title": title,
            "record": _incoming_weekly_remit_record(bill),
        }

    def mark_incoming_weekly_remit_received(
        self,
        amount: Decimal | None = None,
        *,
        today: date | None = None,
    ) -> dict:
        week_start = active_business_week(today)
        title = _incoming_weekly_remit_title(week_start)
        foundation = self.cash_flow.get_existing_foundation()
        existing = self._find_incoming_weekly_remit(foundation["data_source_id"], title)
        if len(existing) > 1:
            return {"status": "conflict", "week_start": week_start.isoformat(), "title": title}
        if not existing:
            return {"status": "not_found", "week_start": week_start.isoformat(), "title": title}

        bill = existing[0]
        if (bill.status or "").strip().casefold() == "paid":
            return {
                "status": "already_paid",
                "week_start": week_start.isoformat(),
                "title": title,
                "record": _incoming_weekly_remit_record(bill),
            }

        if amount is not None:
            self.cash_flow.update_bill_fields(
                bill.page_id,
                amount=amount,
                notes=_received_notes(bill.notes, bill.amount, amount),
            )

        self.cash_flow.ensure_payment_confirmation_properties(foundation["data_source_id"])
        self.cash_flow.mark_bill_paid_manually(
            bill.page_id,
            today or date.today(),
            payment_method="Manual",
            confirmation_subject="Telegram deposit received",
        )
        refreshed = self._find_incoming_weekly_remit(foundation["data_source_id"], title)
        return {
            "status": "paid",
            "week_start": week_start.isoformat(),
            "title": title,
            "record": _incoming_weekly_remit_record(refreshed[0] if refreshed else bill),
        }

    def _find_incoming_weekly_remit(self, data_source_id: str, title: str) -> list:
        return [
            bill
            for bill in self.cash_flow.list_cash_flow_bills(data_source_id)
            if (bill.expense_name or "").strip() == title
        ]


def _parse_money(value: str) -> Decimal:
    try:
        cleaned = value.strip().replace("$", "").replace(",", "")
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def _format_money(value: Decimal) -> str:
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
