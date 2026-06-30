from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .models import ParsedPayment


LABELS = {
    "account": "account_number",
    "type": "payment_type",
    "note": "note",
    "payments date": "payment_date",
    "payment date": "payment_date",
    "payment amount": "payment_amount",
    "amount": "payment_amount",
}


def parse_payment_email(body_text: str) -> ParsedPayment:
    fields: dict[str, str] = {}
    normalized = body_text.replace("\xa0", " ")
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]

    for line in lines:
        match = re.match(r"^([A-Za-z ]{2,30})\s*[:\-]\s*(.+)$", line)
        if not match:
            continue
        raw_label = re.sub(r"\s+", " ", match.group(1).strip().lower())
        key = LABELS.get(raw_label)
        if key:
            fields[key] = match.group(2).strip()
        elif raw_label == "payments":
            payment_date, payment_amount = parse_payments_line(match.group(2).strip())
            fields.setdefault("payment_date", payment_date)
            fields.setdefault("payment_amount", payment_amount)

    # Fallback for compact/plain-text emails where labels and values may be separated by spaces.
    for label, key in LABELS.items():
        if key in fields:
            continue
        pattern = rf"{re.escape(label)}\s*[:\-]?\s+(.+?)(?=\n[A-Za-z ]{{2,30}}\s*[:\-]|\Z)"
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            fields[key] = " ".join(match.group(1).split())

    account = fields.get("account_number")
    amount = fields.get("payment_amount")
    if not account:
        raise ValueError("Payment email is missing Account.")
    if not amount:
        raise ValueError("Payment email is missing Payment amount.")

    return ParsedPayment(
        account_number=account,
        payment_type=fields.get("payment_type"),
        note=fields.get("note"),
        payment_date=fields.get("payment_date"),
        payment_amount_cents=money_to_cents(amount),
    )


def money_to_cents(value: str) -> int:
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if cleaned in {"", ".", "-", "-."}:
        raise ValueError(f"Invalid payment amount: {value!r}")
    try:
        dollars = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid payment amount: {value!r}") from exc
    cents = (dollars * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def parse_payments_line(value: str) -> tuple[str, str]:
    match = re.match(
        r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\s+(?P<amount>\$?\s*-?[\d,]+(?:\.\d{2})?)\s*$",
        value,
    )
    if not match:
        raise ValueError(f"Invalid Payments line: {value!r}")
    return match.group("date"), match.group("amount")


def cents_to_currency(cents: int) -> str:
    amount = Decimal(cents) / Decimal(100)
    return f"${amount:,.2f}"
