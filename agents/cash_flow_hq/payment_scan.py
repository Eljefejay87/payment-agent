from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .graph_client import CashFlowGraphClient
from .models import BillEmail, CashFlowBillRecord, PaymentConfirmation, VendorRule
from .parser import extract_amount, extract_invoice_number, normalize_text, vendor_from_message
from .service import CashFlowHQService

LOGGER = logging.getLogger(__name__)

PAYMENT_AMOUNT_PATTERN = re.compile(
    r"(?:payment|paid|charged|receipt|transaction|ach)\D{0,40}\$?\s*([0-9][0-9,]*\.\d{2})",
    re.IGNORECASE,
)


@dataclass
class PaymentMatch:
    confirmation: PaymentConfirmation
    bill: CashFlowBillRecord | None
    confidence: str
    reason: str
    payment_method: str = "Manual"


@dataclass
class PaymentScanResult:
    would_mark_paid: list[PaymentMatch] = field(default_factory=list)
    marked_paid: list[PaymentMatch] = field(default_factory=list)
    skipped: list[PaymentMatch] = field(default_factory=list)
    needs_review: list[PaymentMatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class CashFlowPaymentScanner:
    def __init__(self, cash_flow: CashFlowHQService, graph: CashFlowGraphClient) -> None:
        self.cash_flow = cash_flow
        self.graph = graph

    def scan(self, days: int, limit: int, dry_run: bool = False, debug: bool = False) -> PaymentScanResult:
        result = PaymentScanResult()
        foundation = self.cash_flow.get_existing_foundation()
        data_source_id = foundation["data_source_id"]
        if not dry_run:
            self.cash_flow.ensure_payment_confirmation_properties(data_source_id)
        vendor_rules_foundation = self.cash_flow.get_existing_vendor_rules_foundation()
        vendor_rules = (
            self.cash_flow.list_vendor_rules(vendor_rules_foundation["data_source_id"])
            if vendor_rules_foundation
            else []
        )
        bills = self.cash_flow.list_cash_flow_bills(data_source_id)
        messages = self.graph.find_payment_confirmation_messages(days=days, limit=limit)
        LOGGER.info("Cash Flow HQ found %s payment confirmation email(s).", len(messages))

        for message in messages:
            try:
                confirmation = parse_payment_confirmation(message)
                match = match_payment_confirmation(confirmation, bills, vendor_rules)
                if debug:
                    LOGGER.info(
                        "Payment candidate subject=%r vendor=%r amount=%s invoice=%s received=%s confidence=%s reason=%s",
                        confirmation.subject,
                        confirmation.vendor_payee,
                        confirmation.amount,
                        confirmation.invoice_number,
                        confirmation.received_date,
                        match.confidence,
                        match.reason,
                    )
                if match.confidence == "Skip":
                    result.skipped.append(match)
                    LOGGER.info("Skipped payment confirmation: %s. %s", confirmation.subject, match.reason)
                    continue
                if match.confidence != "High" or match.bill is None:
                    result.needs_review.append(match)
                    LOGGER.info("Needs Review - Payment Match: %s. %s", confirmation.subject, match.reason)
                    continue
                if dry_run:
                    result.would_mark_paid.append(match)
                    LOGGER.info(
                        "Would mark Paid Vendor=%s Amount=%s Matched record=%s Confidence=%s Reason=%s Payment Date=%s",
                        match.bill.vendor_payee,
                        match.bill.amount,
                        match.bill.expense_name,
                        match.confidence,
                        match.reason,
                        confirmation.received_date,
                    )
                    continue
                self.cash_flow.mark_bill_paid_from_confirmation(match.bill, confirmation, match.payment_method)
                result.marked_paid.append(match)
                LOGGER.info("Marked bill paid from Outlook confirmation: %s", match.bill.expense_name)
            except Exception as exc:
                result.errors.append(f"{message.subject}: {exc}")
                LOGGER.exception("Could not process payment confirmation email: %s", message.subject)
        return result


def parse_payment_confirmation(message: BillEmail) -> PaymentConfirmation:
    text = normalize_text(f"{message.subject}\n{message.sender_name} {message.sender_email}\n{message.body_text}")
    return PaymentConfirmation(
        vendor_payee=vendor_from_message(message),
        amount=extract_payment_amount(text),
        invoice_number=extract_invoice_number(text),
        received_date=message.received_at.date() if message.received_at else None,
        subject=message.subject,
        email_link=message.web_link,
        message_id=message.message_id,
        internet_message_id=message.internet_message_id,
        confidence="medium",
    )


def extract_payment_amount(text: str) -> Decimal | None:
    match = PAYMENT_AMOUNT_PATTERN.search(text)
    if match:
        return Decimal(match.group(1).replace(",", ""))
    return extract_amount(text)


def match_payment_confirmation(
    confirmation: PaymentConfirmation,
    bills: list[CashFlowBillRecord],
    vendor_rules: list[VendorRule] | None = None,
) -> PaymentMatch:
    duplicate = next((bill for bill in bills if already_paid_or_linked(bill, confirmation) and confirmation_matches_bill(confirmation, bill)), None)
    if duplicate:
        return PaymentMatch(confirmation, duplicate, "Skip", "Already Paid")
    open_bills = [bill for bill in bills if not already_paid_or_linked(bill, confirmation)]
    if confirmation.invoice_number:
        invoice_matches = [
            bill for bill in open_bills
            if bill.invoice_number and bill.invoice_number.lower() == confirmation.invoice_number.lower()
        ]
        if len(invoice_matches) == 1:
            bill = invoice_matches[0]
            return PaymentMatch(confirmation, bill, "High", "Matched by invoice number", payment_method_for_bill(bill, vendor_rules or []))
        if len(invoice_matches) > 1:
            return PaymentMatch(confirmation, None, "Low", "Needs Review because multiple matches")

    vendor_amount_matches = [
        bill for bill in open_bills
        if vendor_matches(confirmation.vendor_payee, bill.vendor_payee)
        and confirmation.amount is not None
        and bill.amount == confirmation.amount
        and date_proximity_matches(confirmation, bill)
    ]
    if len(vendor_amount_matches) == 1:
        bill = vendor_amount_matches[0]
        return PaymentMatch(confirmation, bill, "High", "Matched by amount/date", payment_method_for_bill(bill, vendor_rules or []))
    if len(vendor_amount_matches) > 1:
        return PaymentMatch(confirmation, None, "Low", "Needs Review because multiple matches")

    vendor_date_matches = [
        bill for bill in open_bills
        if vendor_matches(confirmation.vendor_payee, bill.vendor_payee)
        and date_proximity_matches(confirmation, bill)
    ]
    if len(vendor_date_matches) == 1 and confirmation.amount is not None:
        bill = vendor_date_matches[0]
        return PaymentMatch(confirmation, bill, "High", "Matched by vendor/date", payment_method_for_bill(bill, vendor_rules or []))
    if len(vendor_date_matches) > 1:
        return PaymentMatch(confirmation, None, "Low", "Needs Review because multiple matches")

    return PaymentMatch(confirmation, None, "Low", "Needs Review because no matching bill")


def already_paid_or_linked(bill: CashFlowBillRecord, confirmation: PaymentConfirmation) -> bool:
    return bool(
        bill.status == "Paid"
        or bill.payment_date
        or (bill.confirmation_link and bill.confirmation_link == confirmation.email_link)
    )


def confirmation_matches_bill(confirmation: PaymentConfirmation, bill: CashFlowBillRecord) -> bool:
    if bill.confirmation_link and bill.confirmation_link == confirmation.email_link:
        return True
    if confirmation.invoice_number and bill.invoice_number and bill.invoice_number.lower() == confirmation.invoice_number.lower():
        return True
    return bool(
        vendor_matches(confirmation.vendor_payee, bill.vendor_payee)
        and confirmation.amount is not None
        and bill.amount == confirmation.amount
        and date_proximity_matches(confirmation, bill)
    )


def vendor_matches(left: str, right: str) -> bool:
    left_norm = left.strip().lower()
    right_norm = right.strip().lower()
    return bool(left_norm and right_norm and (left_norm == right_norm or left_norm in right_norm or right_norm in left_norm))


def date_proximity_matches(confirmation: PaymentConfirmation, bill: CashFlowBillRecord) -> bool:
    if confirmation.received_date is None or bill.due_date is None:
        return False
    return abs((confirmation.received_date - bill.due_date).days) <= 21


def payment_method_for_bill(bill: CashFlowBillRecord, vendor_rules: list[VendorRule]) -> str:
    for rule in vendor_rules:
        if rule.active and (vendor_matches(rule.vendor_name, bill.vendor_payee) or vendor_matches(rule.match_text, bill.vendor_payee)):
            return "Auto Pay" if rule.auto_pay or rule.payment_type == "Auto Pay" else "Manual"
    return "Auto Pay" if bill.payment_type == "Auto Pay" else "Manual"
