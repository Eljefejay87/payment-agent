from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .graph_client import CashFlowGraphClient
from .models import CashFlowBillRecord
from .payment_scan import CashFlowPaymentScanner, PaymentMatch
from .service import CashFlowHQService


@dataclass(frozen=True)
class ReviewReport:
    bills_needing_review: list[CashFlowBillRecord]
    payment_matches_needing_review: list[PaymentMatch]
    ignored_payment_matches: list[PaymentMatch]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bills_needing_review": [bill_to_dict(bill) for bill in self.bills_needing_review],
            "payment_matches_needing_review": [
                payment_match_to_dict(match) for match in self.payment_matches_needing_review
            ],
            "ignored_payment_matches": [
                payment_match_to_dict(match) for match in self.ignored_payment_matches
            ],
        }


def build_review_report(
    service: CashFlowHQService,
    graph: CashFlowGraphClient | None = None,
    days: int = 7,
    limit: int = 50,
) -> ReviewReport:
    foundation = service.get_existing_foundation()
    bills = service.list_cash_flow_bills(foundation["data_source_id"])
    bill_reviews = [bill for bill in bills if bill_needs_review(bill)]
    payment_reviews: list[PaymentMatch] = []
    ignored_reviews: list[PaymentMatch] = []
    ignored_ids = load_ignored_email_ids(service.settings.cash_flow_review_state_path)

    if graph is not None:
        scan = CashFlowPaymentScanner(service, graph).scan(days=days, limit=limit, dry_run=True)
        for match in scan.needs_review:
            if match.confirmation.message_id in ignored_ids or match.confirmation.internet_message_id in ignored_ids:
                ignored_reviews.append(match)
            else:
                payment_reviews.append(match)

    return ReviewReport(bill_reviews, payment_reviews, ignored_reviews)


def bill_needs_review(bill: CashFlowBillRecord) -> bool:
    action = bill.action_required or ""
    due_status = bill.due_status or ""
    return bool(
        bill.status == "Needs Review"
        or action in {"Needs Review", "Past Due", "Pay Now"}
        or due_status.startswith("🔴 Past Due")
        or due_status.startswith("Past Due")
    )


def ignore_email(state_path: Path, message_id: str) -> set[str]:
    ignored = load_ignored_email_ids(state_path)
    ignored.add(message_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"ignored_email_ids": sorted(ignored)}, indent=2), encoding="utf-8")
    return ignored


def load_ignored_email_ids(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {str(item) for item in data.get("ignored_email_ids", []) if item}


def bill_to_dict(bill: CashFlowBillRecord) -> dict[str, Any]:
    return {
        "page_id": bill.page_id,
        "vendor": bill.vendor_payee,
        "expense_name": bill.expense_name,
        "amount": str(bill.amount) if bill.amount is not None else None,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
        "status": bill.status,
        "due_status": bill.due_status,
        "action_required": bill.action_required,
        "category": bill.category,
        "notes": bill.notes,
    }


def payment_match_to_dict(match: PaymentMatch) -> dict[str, Any]:
    return {
        "subject": match.confirmation.subject,
        "message_id": match.confirmation.message_id,
        "internet_message_id": match.confirmation.internet_message_id,
        "vendor": match.confirmation.vendor_payee,
        "amount": str(match.confirmation.amount) if match.confirmation.amount is not None else None,
        "received_date": match.confirmation.received_date.isoformat() if match.confirmation.received_date else None,
        "confidence": match.confidence,
        "reason": match.reason,
        "matched_bill_page_id": match.bill.page_id if match.bill else None,
    }


def format_review_report(report: ReviewReport) -> str:
    lines = ["Cash Flow HQ Review Queue", ""]
    lines.append(f"Bills needing review: {len(report.bills_needing_review)}")
    for bill in report.bills_needing_review[:25]:
        amount = f"${bill.amount}" if bill.amount is not None else "No amount"
        due = bill.due_status or (bill.due_date.isoformat() if bill.due_date else "No due date")
        lines.append(f"- {bill.vendor_payee} | {amount} | {due} | {bill.action_required or bill.status} | {bill.page_id}")
        if bill.notes:
            lines.append(f"  {bill.notes.splitlines()[0]}")
    lines.append("")
    lines.append(f"Payment matches needing review: {len(report.payment_matches_needing_review)}")
    for match in report.payment_matches_needing_review[:25]:
        amount = f"${match.confirmation.amount}" if match.confirmation.amount is not None else "No amount"
        lines.append(
            f"- {match.confirmation.vendor_payee} | {amount} | {match.reason} | {match.confirmation.message_id}"
        )
    if report.ignored_payment_matches:
        lines.append("")
        lines.append(f"Ignored payment matches hidden: {len(report.ignored_payment_matches)}")
    return "\n".join(lines)
