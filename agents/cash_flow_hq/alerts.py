from __future__ import annotations

import html
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
        today = today or date.today()
        foundation = self.cash_flow.get_existing_foundation()
        bills = bills_from_pages(
            self.cash_flow.notion.request(
                "POST",
                f"/data_sources/{foundation['data_source_id']}/query",
                json={"page_size": 100},
            ).get("results", [])
        )
        alert = build_cash_flow_alert(bills, today)
        if dry_run:
            LOGGER.info("DRY RUN Cash Flow HQ Teams alert to %s:\n%s", self.settings.cash_flow_teams_user, alert["text"])
        else:
            self.graph.post_direct_chat_message(self.settings.cash_flow_teams_user, alert["html"])
            LOGGER.info("Cash Flow HQ Teams alert sent directly to %s", self.settings.cash_flow_teams_user)
        return alert


def build_cash_flow_alert(bills: list[CashFlowBill], today: date) -> dict[str, Any]:
    due_today = [bill for bill in bills if bill.due_status == "Due Today" and bill.status != "Paid"]
    due_tomorrow = [bill for bill in bills if bill.due_status == "Due Tomorrow" and bill.status != "Paid"]
    past_due = [bill for bill in bills if bill.due_status.startswith("Past Due") and bill.status != "Paid"]
    needs_review = [bill for bill in bills if bill.status == "Needs Review"]
    due_this_week_total = sum(
        bill.amount or 0
        for bill in bills
        if bill.due_date and 0 <= (bill.due_date - today).days <= 7 and bill.status != "Paid"
    )
    lines = [
        "Cash Flow HQ Alert",
        f"Bills due this week total: {money(due_this_week_total)}",
        f"Due today: {len(due_today)}",
        f"Due tomorrow: {len(due_tomorrow)}",
        f"Past due: {len(past_due)}",
        f"Needs review: {len(needs_review)}",
    ]
    html_lines = ["<h2>Cash Flow HQ Alert</h2>", f"<p><strong>Bills due this week total:</strong> {money(due_this_week_total)}</p>"]
    html_lines.extend(section_html("Due Today", due_today))
    html_lines.extend(section_html("Due Tomorrow", due_tomorrow))
    html_lines.extend(section_html("Past Due", past_due))
    html_lines.extend(section_html("Needs Review", needs_review))
    return {"text": "\n".join(lines), "html": "".join(html_lines)}


def section_html(title: str, bills: list[CashFlowBill]) -> list[str]:
    if not bills:
        return [f"<p><strong>{html.escape(title)}:</strong> 0</p>"]
    items = "".join(f"<li>{html.escape(bill.vendor)} {html.escape(money(bill.amount))} {html.escape(bill.due_status)}</li>" for bill in bills)
    return [f"<p><strong>{html.escape(title)}:</strong></p><ul>{items}</ul>"]


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
