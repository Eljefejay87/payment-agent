from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from shared.integrations.microsoft_graph import GraphClient

from .config import CashFlowHQSettings
from .service import CashFlowHQService, plain_rich_text, plain_title

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CashFlowBill:
    vendor: str
    amount: float | None
    due_date: date | None
    due_status: str
    status: str
    notes: str = ""


class CashFlowTeamsAlerts:
    def __init__(
        self,
        settings: CashFlowHQSettings,
        cash_flow: CashFlowHQService,
        graph: GraphClient,
    ) -> None:
        self.settings = settings
        self.cash_flow = cash_flow
        self.graph = graph

    def send_alerts(self, today: date | None = None, dry_run: bool = False) -> dict[str, Any]:
        return self.send_morning_brief(today=today, dry_run=dry_run, force=True)

    def send_morning_brief(
        self,
        today: date | None = None,
        dry_run: bool = False,
        force: bool = False,
        record_sent: bool = True,
    ) -> dict[str, Any]:
        today = today or date.today()
        if not force and already_sent(self.settings.cash_flow_notification_state_path, today):
            LOGGER.info("Cash Flow HQ morning brief already sent for %s; skipping.", today.isoformat())
            return {"skipped": True, "reason": "already sent"}
        foundation = self.cash_flow.get_existing_foundation()
        bills = bills_from_pages(
            self.cash_flow.notion.request(
                "POST",
                f"/data_sources/{foundation['data_source_id']}/query",
                json={"page_size": 100},
            ).get("results", [])
        )
        alert = build_morning_brief(bills, today)
        if dry_run:
            LOGGER.info("DRY RUN Cash Flow HQ morning brief to %s:\n%s", self.settings.cash_flow_teams_user, alert["text"])
        else:
            self.graph.post_direct_chat_message(self.settings.cash_flow_teams_user, alert["html"])
            if record_sent:
                mark_sent(self.settings.cash_flow_notification_state_path, today)
            LOGGER.info("Cash Flow HQ morning brief sent directly to %s", self.settings.cash_flow_teams_user)
        return alert


def build_cash_flow_alert(bills: list[CashFlowBill], today: date) -> dict[str, Any]:
    return build_morning_brief(bills, today)


def build_morning_brief(bills: list[CashFlowBill], today: date) -> dict[str, Any]:
    unpaid = [bill for bill in bills if bill.status != "Paid"]
    due_today = [bill for bill in unpaid if "Today" in bill.due_status]
    due_tomorrow = [bill for bill in unpaid if "Tomorrow" in bill.due_status]
    past_due = [bill for bill in unpaid if "Past Due" in bill.due_status]
    needs_review = [bill for bill in bills if bill.status == "Needs Review"]
    due_this_week = [bill for bill in unpaid if bill.due_date and 0 <= (bill.due_date - today).days <= 7]
    upcoming = [bill for bill in bills if bill.status == "Upcoming"]
    priorities = top_priorities(unpaid, today)
    lines = [
        "💰 Cash Flow HQ Morning Brief",
        "",
        f"Bills Due Today: {len(due_today)} ({money(total(due_today))})",
        f"Bills Due Tomorrow: {len(due_tomorrow)} ({money(total(due_tomorrow))})",
        f"Bills Due This Week: {len(due_this_week)} ({money(total(due_this_week))})",
        f"Needs Review: {len(needs_review)}",
        f"Past Due: {len(past_due)}",
        f"Upcoming: {len(upcoming)}",
        "",
        "Top Priorities",
    ]
    if priorities:
        lines.extend(priority_text_lines(priorities))
    else:
        lines.extend(["", "✅ No action needed today."])
    html_lines = [
        "<h2>💰 Cash Flow HQ Morning Brief</h2>",
        f"<p><strong>Bills Due Today:</strong> {len(due_today)} ({html.escape(money(total(due_today)))})<br>",
        f"<strong>Bills Due Tomorrow:</strong> {len(due_tomorrow)} ({html.escape(money(total(due_tomorrow)))})<br>",
        f"<strong>Bills Due This Week:</strong> {len(due_this_week)} ({html.escape(money(total(due_this_week)))})<br>",
        f"<strong>Needs Review:</strong> {len(needs_review)}<br>",
        f"<strong>Past Due:</strong> {len(past_due)}<br>",
        f"<strong>Upcoming:</strong> {len(upcoming)}</p>",
        "<p><strong>Top Priorities</strong></p>",
    ]
    html_lines.append(priority_html(priorities) if priorities else "<p>✅ No action needed today.</p>")
    return {"text": "\n".join(lines), "html": "".join(html_lines)}


def top_priorities(bills: list[CashFlowBill], today: date) -> list[CashFlowBill]:
    priority_bills = [
        bill
        for bill in bills
        if bill.status == "Needs Review" or "Today" in bill.due_status or "Tomorrow" in bill.due_status or "Past Due" in bill.due_status
    ]
    return sorted(priority_bills, key=lambda bill: priority_key(bill, today))[:5]


def priority_key(bill: CashFlowBill, today: date) -> tuple[int, int, float]:
    if "Past Due" in bill.due_status:
        group = 0
    elif bill.status == "Needs Review":
        group = 1
    elif "Today" in bill.due_status:
        group = 2
    elif "Tomorrow" in bill.due_status:
        group = 3
    else:
        group = 4
    days = (bill.due_date - today).days if bill.due_date else 999
    return (group, days, -(bill.amount or 0))


def priority_text_lines(bills: list[CashFlowBill]) -> list[str]:
    lines: list[str] = []
    for bill in bills:
        lines.extend(["", f"• {bill.vendor}", priority_detail(bill), priority_status(bill)])
    return lines


def priority_html(bills: list[CashFlowBill]) -> str:
    items = "".join(
        "<li>"
        f"{html.escape(bill.vendor)}<br>"
        f"{html.escape(priority_detail(bill))}<br>"
        f"{html.escape(priority_status(bill))}"
        "</li>"
        for bill in bills
    )
    return f"<ul>{items}</ul>"


def priority_detail(bill: CashFlowBill) -> str:
    if bill.amount is not None:
        return money(bill.amount)
    if bill.status == "Needs Review":
        return "Needs Review"
    return ""


def priority_status(bill: CashFlowBill) -> str:
    if bill.status == "Needs Review":
        return first_review_reason(bill.notes) or bill.due_status or "Needs Review"
    return bill.due_status


def first_review_reason(notes: str) -> str:
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.startswith("•"):
            return stripped.lstrip("• ").strip()
    return ""


def total(bills: list[CashFlowBill]) -> float:
    return sum(bill.amount or 0 for bill in bills)


def bills_from_pages(pages: list[dict]) -> list[CashFlowBill]:
    return [bill_from_page(page) for page in pages]


def bill_from_page(page: dict) -> CashFlowBill:
    properties = page.get("properties", {})
    return CashFlowBill(
        vendor=plain_rich_text(properties.get("Vendor / Payee", {})) or plain_title(properties.get("Expense Name", {}).get("title", [])),
        amount=number_value(properties.get("Amount", {})),
        due_date=date_value(properties.get("Due Date", {})),
        due_status=formula_string(properties.get("Due Status", {})),
        status=select_name(properties.get("Status", {})),
        notes=plain_rich_text(properties.get("Notes", {})),
    )


def number_value(property_value: dict) -> float | None:
    value = property_value.get("number")
    return float(value) if isinstance(value, (int, float)) else None


def date_value(property_value: dict) -> date | None:
    start = (property_value.get("date") or {}).get("start")
    if not start:
        return None
    try:
        return date.fromisoformat(start[:10])
    except ValueError:
        return None


def formula_string(property_value: dict) -> str:
    formula = property_value.get("formula") or {}
    return (formula.get("string") or "") if formula.get("type") == "string" else ""


def select_name(property_value: dict) -> str:
    return (property_value.get("select") or {}).get("name") or ""


def money(value: float | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def already_sent(path, today: date) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return data.get("last_sent_date") == today.isoformat()


def mark_sent(path, today: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"last_sent_date": today.isoformat()}, handle)
