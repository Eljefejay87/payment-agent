from __future__ import annotations

import re
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

USAEPAY_LABELS = (
    "Card Holder",
    "Date",
    "Reference #",
    "Authorization #",
    "Invoice",
    "PO #",
    "Merchant",
    "Type",
    "Source",
)


def parse_payment_email(body_text: str) -> ParsedPayment:
    normalized = body_text.replace("\xa0", " ")
    if "receipt of payment" in normalized.lower():
        return parse_usaepay_receipt(normalized)

    fields: dict[str, str] = {}
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


def parse_usaepay_receipt(body_text: str) -> ParsedPayment:
    fields = _extract_usaepay_fields(body_text)
    amount_match = re.search(
        r"\bTotal\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{2})?)",
        body_text,
        flags=re.IGNORECASE,
    )
    if not amount_match:
        raise ValueError("USAePay receipt is missing Total.")

    account = fields.get("invoice") or fields.get("po #")
    if not account:
        raise ValueError("USAePay receipt is missing Invoice/PO #.")

    reference = fields.get("reference #")
    note_parts: list[str] = []
    if reference:
        note_parts.append(f"USAePay Ref {reference}")
    if fields.get("card holder"):
        note_parts.append(f"Card Holder {fields['card holder']}")
    if fields.get("authorization #"):
        note_parts.append(f"Auth {fields['authorization #']}")
    if fields.get("merchant"):
        note_parts.append(f"Merchant {fields['merchant']}")

    return ParsedPayment(
        account_number=account,
        payment_type=fields.get("type") or "Credit Card",
        note=" | ".join(note_parts) or "USAePay approved payment",
        payment_date=_normalize_usaepay_date(fields.get("date")),
        payment_amount_cents=money_to_cents(amount_match.group(1)),
    )


def _extract_usaepay_fields(body_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    lines = [re.sub(r"\s+", " ", line).strip() for line in body_text.splitlines() if line.strip()]

    for index, line in enumerate(lines):
        lowered = line.lower()
        for label in USAEPAY_LABELS:
            label_lower = label.lower()
            if lowered == label_lower and index + 1 < len(lines):
                fields.setdefault(label_lower, lines[index + 1].strip())
                break
            match = re.match(rf"^{re.escape(label)}\s*[:\-]?\s+(.+)$", line, flags=re.IGNORECASE)
            if match:
                fields.setdefault(label_lower, match.group(1).strip())
                break

    # HTML-to-text conversion can collapse table labels/values into a single line.
    flattened = " ".join(lines)
    labels_pattern = "|".join(re.escape(label) for label in USAEPAY_LABELS)
    for label in USAEPAY_LABELS:
        key = label.lower()
        if key in fields:
            continue
        match = re.search(
            rf"\b{re.escape(label)}\b\s*[:\-]?\s*(.+?)(?=\s+(?:{labels_pattern})\b|\Z)",
            flattened,
            flags=re.IGNORECASE,
        )
        if match:
            value = " ".join(match.group(1).split()).strip()
            if value:
                fields[key] = value

    return fields


def _normalize_usaepay_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{2,4})", value)
    if not match:
        return value
    year = int(match.group("year"))
    if year < 100:
        year += 2000
    return f"{int(match.group('month')):02d}/{int(match.group('day')):02d}/{year}"


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
