from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from shared.utils.text import html_to_text

from .models import AttachmentMetadata, BillCandidate, BillEmail
from .pdf_text import extract_text_from_pdf_bytes

BILL_TERMS = [
    "invoice",
    "bill",
    "statement",
    "amount due",
    "payment due",
    "due date",
    "autopay",
    "automatic payment",
    "subscription",
    "renewal",
    "receipt",
    "payment reminder",
]

AMOUNT_PATTERNS = [
    re.compile(
        r"(?:amount due|total due|balance due|invoice total|payment amount|scheduled payment)\D{0,40}\$?\s*([0-9][0-9,]*\.\d{2})",
        re.IGNORECASE,
    ),
]

DATE_PATTERNS = [
    re.compile(
        r"(?:due date|payment due|due by|amount due by|pay by|autopay scheduled for|"
        r"scheduled payment date|invoice due|balance due|please pay by|renewal date)\D{0,35}"
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due date|payment due|due by|amount due by|pay by|autopay scheduled for|"
        r"scheduled payment date|invoice due|balance due|please pay by|renewal date)\D{0,35}"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due date|payment due|due by|amount due by|pay by|autopay scheduled for|"
        r"scheduled payment date|invoice due|balance due|please pay by|renewal date)\D{0,35}"
        r"(\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    ),
]

CATEGORY_KEYWORDS = [
    ("Software", ("software", "subscription", "saas", "license", "hosting")),
    ("Utilities", ("utility", "electric", "gas", "water", "internet", "phone")),
    ("Insurance", ("insurance", "premium", "policy")),
    ("Rent", ("rent", "lease")),
    ("Loan Payment", ("loan", "financing")),
    ("Taxes", ("tax", "irs")),
    ("Office Expense", ("office", "supplies")),
]

INVOICE_NUMBER_PATTERNS = [
    re.compile(
        r"(?:invoice|inv|statement|receipt)\s*(?:number|no|id)\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{2,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:invoice|inv|statement|receipt)\s*#\s*([A-Z0-9][A-Z0-9-]{2,})",
        re.IGNORECASE,
    ),
]
FALLBACK_DOLLAR_PATTERN = re.compile(r"\$\s*([0-9][0-9,]*\.\d{2})")
UNRELATED_NUMBER_CONTEXT = ("invoice", "inv", "account", "acct", "phone", "tel", "previous balance")


@dataclass(frozen=True)
class ExtractionResult:
    value: object | None
    reasons: tuple[str, ...] = ()
    source: str = ""


def is_bill_related(message: BillEmail) -> bool:
    haystack = f"{message.subject}\n{message.body_text}\n{attachment_metadata_text(message.attachments)}".lower()
    return any(term in haystack for term in BILL_TERMS)


def parse_bill_candidate(message: BillEmail) -> BillCandidate:
    email_text = normalize_text(
        f"Subject: {message.subject}\n"
        f"Sender: {message.sender_name} {message.sender_email}\n"
        f"Body: {message.body_text}\n"
        f"Attachments: {attachment_metadata_text(message.attachments)}"
    )
    pdf_text, pdf_reason = pdf_text_from_attachments(message.attachments)
    pdf_text = normalize_text(pdf_text)
    amount_result = merge_extraction_results(
        extract_amount_result(email_text, "subject/body/attachment metadata"),
        extract_amount_result(pdf_text, "pdf") if pdf_text else ExtractionResult(None),
    )
    date_result = merge_extraction_results(
        extract_due_date_result(email_text, "subject/body/attachment metadata"),
        extract_due_date_result(pdf_text, "pdf") if pdf_text else ExtractionResult(None),
    )
    amount = amount_result.value
    due_date = date_result.value
    payment_type = extract_payment_type(email_text) or extract_payment_type(pdf_text)
    vendor = vendor_from_message(message)
    pdf_vendor = extract_vendor_from_pdf_text(pdf_text)
    if vendor in {message.sender_name, vendor_from_email_domain(message.sender_email)} and pdf_vendor:
        vendor = pdf_vendor
    category = extract_category(email_text) or extract_category(pdf_text)
    invoice_number = extract_invoice_number(email_text) or extract_invoice_number(pdf_text)
    review_reasons = amount_result.reasons + date_result.reasons
    if review_reasons and pdf_reason:
        review_reasons = review_reasons + (pdf_reason,)
    status = "Upcoming" if amount is not None and due_date is not None and not review_reasons else "Needs Review"
    confidence = "high" if status == "Upcoming" else "low"
    notes = build_notes(message, amount, due_date, invoice_number, confidence, review_reasons)
    field_sources = {
        "vendor": "pdf" if pdf_vendor and vendor == pdf_vendor else (
            "sender" if vendor in {message.sender_name, vendor_from_email_domain(message.sender_email)} else "pdf"
        ),
        "amount": amount_result.source,
        "due_date": date_result.source,
        "invoice_number": "pdf" if invoice_number and pdf_text and invoice_number in pdf_text else (
            "subject/body/attachment metadata" if invoice_number else ""
        ),
        "payment_type": "subject/body/attachment metadata" if payment_type else "",
        "category": "subject/body/attachment metadata" if category else "",
        "frequency": "",
    }

    return BillCandidate(
        vendor_payee=vendor,
        expense_name=clean_subject(message.subject) or vendor or "Bill email needs review",
        amount=amount,
        due_date=due_date,
        invoice_number=invoice_number,
        payment_type=payment_type,
        category=category,
        frequency=None,
        email_link=message.web_link,
        notes=notes,
        status=status,
        confidence=confidence,
        review_reasons=review_reasons,
        field_sources=field_sources,
        message_id=message.message_id,
        internet_message_id=message.internet_message_id,
    )


def message_from_graph(raw: dict) -> BillEmail:
    sender = raw.get("from", {}).get("emailAddress", {})
    body = raw.get("body", {})
    received_at = parse_received_at(raw.get("receivedDateTime", ""))
    content = body.get("content", "") or raw.get("bodyPreview", "") or ""
    body_source = "body" if body.get("content") else "bodyPreview"
    return BillEmail(
        message_id=raw.get("id", ""),
        internet_message_id=raw.get("internetMessageId", ""),
        subject=raw.get("subject", "") or "",
        sender_name=sender.get("name", "") or "",
        sender_email=sender.get("address", "") or "",
        received_at=received_at,
        body_text=body_to_text(content, body.get("contentType", "")),
        body_source=body_source,
        web_link=raw.get("webLink", "") or "",
        attachments=tuple(attachment_from_graph(item) for item in raw.get("attachments", []) or []),
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or " ").strip()


def extract_amount(text: str) -> Decimal | None:
    return extract_amount_result(text).value


def extract_amount_result(text: str, source_label: str = "subject/body/attachment metadata") -> ExtractionResult:
    amounts: list[Decimal] = []
    source = ""
    for pattern in AMOUNT_PATTERNS:
        matches = list(pattern.finditer(text))
        if matches:
            source = source_label
        amounts.extend(decimal_from_matches(matches))
    unique = unique_decimals(amounts)
    if len(unique) == 1:
        return ExtractionResult(unique[0], source=source)
    if len(unique) > 1:
        return ExtractionResult(None, ("multiple possible amounts",))

    fallback = [
        amount
        for match, amount in fallback_amount_matches(text)
        if not has_unrelated_number_context(text, match.start())
    ]
    unique_fallback = unique_decimals(fallback)
    if len(unique_fallback) == 1:
        return ExtractionResult(unique_fallback[0], source=f"fallback dollar amount in {source_label}")
    if len(unique_fallback) > 1:
        return ExtractionResult(None, ("multiple possible amounts",))
    return ExtractionResult(None, ("missing amount",))


def extract_due_date(text: str) -> date | None:
    return extract_due_date_result(text).value


def extract_due_date_result(text: str, source_label: str = "subject/body/attachment metadata") -> ExtractionResult:
    dates: list[date] = []
    source = ""
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = parse_date_value(match.group(1))
            if parsed:
                dates.append(parsed)
                source = source_label
    unique = sorted(set(dates))
    if len(unique) == 1:
        return ExtractionResult(unique[0], source=source)
    if len(unique) > 1:
        return ExtractionResult(None, ("multiple possible due dates",))
    return ExtractionResult(None, ("missing due date",))


def parse_date_value(value: str) -> date | None:
    normalized = value.replace(".", "").strip()
    formats = (
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%Y-%m-%d",
    )
    for date_format in formats:
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    return None


def extract_payment_type(text: str) -> str | None:
    lowered = text.lower()
    if "autopay" in lowered or "automatic payment" in lowered or "auto pay" in lowered:
        return "Auto Pay"
    if "manual payment" in lowered or "pay manually" in lowered:
        return "Manual"
    return None


def extract_category(text: str) -> str | None:
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None


def vendor_from_message(message: BillEmail) -> str:
    if message.sender_name:
        return message.sender_name.strip()
    if "@" not in message.sender_email:
        return message.sender_email.strip()
    domain = message.sender_email.split("@", 1)[1].split(".", 1)[0]
    return domain.replace("-", " ").replace("_", " ").title()


def extract_invoice_number(text: str) -> str | None:
    for pattern in INVOICE_NUMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip().rstrip(".:,;")
    return None


def clean_subject(subject: str) -> str:
    cleaned = re.sub(r"^(re|fw|fwd):\s*", "", subject or "", flags=re.IGNORECASE).strip()
    return cleaned[:120]


def build_notes(
    message: BillEmail,
    amount: Decimal | None,
    due_date: date | None,
    invoice_number: str | None,
    confidence: str,
    review_reasons: tuple[str, ...],
) -> str:
    return format_business_notes(review_reasons)


def format_business_notes(review_reasons: tuple[str, ...]) -> str:
    if not review_reasons:
        return "Imported from Outlook\n✓ Ready for payment"
    lines = ["Imported from Outlook", "", "Needs Review:"]
    lines.extend(f"• {display_review_reason(reason)}" for reason in review_reasons)
    return "\n".join(lines)[:1800]


def display_review_reason(reason: str) -> str:
    if reason == "missing due date":
        return "Missing due date"
    if reason == "missing amount":
        return "Missing amount"
    return reason


def parse_received_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def decimal_from_matches(matches) -> list[Decimal]:
    values: list[Decimal] = []
    for match in matches:
        try:
            values.append(Decimal(match.group(1).replace(",", "")).quantize(Decimal("0.01")))
        except (InvalidOperation, IndexError):
            continue
    return values


def unique_decimals(values: list[Decimal]) -> list[Decimal]:
    return sorted(set(values))


def fallback_amount_matches(text: str) -> list[tuple[re.Match, Decimal]]:
    matches: list[tuple[re.Match, Decimal]] = []
    for match in FALLBACK_DOLLAR_PATTERN.finditer(text):
        try:
            matches.append((match, Decimal(match.group(1).replace(",", "")).quantize(Decimal("0.01"))))
        except InvalidOperation:
            continue
    return matches


def has_unrelated_number_context(text: str, start: int) -> bool:
    context = text[max(0, start - 40): start].lower()
    return any(term in context for term in UNRELATED_NUMBER_CONTEXT)


def body_to_text(content: str, content_type: str) -> str:
    if (content_type or "").lower() == "html":
        return html_to_text(content)
    return html_to_text(content) if "<" in content and ">" in content else content


def attachment_from_graph(raw: dict) -> AttachmentMetadata:
    return AttachmentMetadata(
        name=raw.get("name", "") or raw.get("id", ""),
        content_type=raw.get("contentType", "") or "",
        size=raw.get("size"),
        is_inline=bool(raw.get("isInline", False)),
        content_bytes=raw.get("content_bytes"),
    )


def attachment_metadata_text(attachments: tuple[AttachmentMetadata, ...]) -> str:
    return " ".join(
        f"{attachment.name} {attachment.content_type}"
        for attachment in attachments
        if attachment.name or attachment.content_type
    )


def vendor_from_email_domain(sender_email: str) -> str:
    if "@" not in sender_email:
        return sender_email.strip()
    domain = sender_email.split("@", 1)[1].split(".", 1)[0]
    return domain.replace("-", " ").replace("_", " ").title()


def merge_extraction_results(email_result: ExtractionResult, pdf_result: ExtractionResult) -> ExtractionResult:
    if email_result.value is not None and not email_result.reasons:
        return email_result
    if pdf_result.value is not None and not pdf_result.reasons:
        return pdf_result
    return email_result


def pdf_text_from_attachments(attachments: tuple[AttachmentMetadata, ...]) -> tuple[str, str | None]:
    pdf_attachments = [attachment for attachment in attachments if is_pdf_attachment(attachment)]
    if not pdf_attachments:
        return "", None
    texts: list[str] = []
    parse_failed = False
    for attachment in pdf_attachments:
        if not attachment.content_bytes:
            parse_failed = True
            continue
        try:
            text = extract_text_from_pdf_bytes(attachment.content_bytes)
        except Exception:
            parse_failed = True
            continue
        if text.strip():
            texts.append(text.strip())
        else:
            parse_failed = True
    if texts:
        return "\n".join(texts), None
    if parse_failed:
        return "", "PDF not parseable"
    return "", None


def is_pdf_attachment(attachment: AttachmentMetadata) -> bool:
    return "pdf" in attachment.content_type.lower() or attachment.name.lower().endswith(".pdf")


def extract_vendor_from_pdf_text(text: str) -> str | None:
    match = re.search(
        r"(?:vendor|payee|remit to)\s*[:#-]?\s*"
        r"([A-Z][A-Za-z0-9 &./-]{2,80}?)(?=\s+(?:invoice|amount|total|balance|due date|payment due)|$)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip().rstrip(".:,;")
