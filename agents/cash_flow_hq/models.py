from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class AttachmentMetadata:
    name: str
    content_type: str
    size: int | None = None
    is_inline: bool = False
    content_bytes: bytes | None = None


@dataclass(frozen=True)
class BillEmail:
    message_id: str
    internet_message_id: str
    subject: str
    sender_name: str
    sender_email: str
    received_at: datetime | None
    body_text: str
    body_source: str
    web_link: str
    attachments: tuple[AttachmentMetadata, ...] = ()


@dataclass(frozen=True)
class BillCandidate:
    vendor_payee: str
    expense_name: str
    amount: Decimal | None
    due_date: date | None
    invoice_number: str | None
    payment_type: str | None
    category: str | None
    frequency: str | None
    email_link: str
    notes: str
    status: str
    confidence: str
    review_reasons: tuple[str, ...]
    field_sources: dict[str, str]
    message_id: str
    internet_message_id: str

    @property
    def has_duplicate_key(self) -> bool:
        return bool(self.vendor_payee and self.amount is not None and self.due_date is not None)

    @property
    def review_reason_text(self) -> str:
        return "; ".join(self.review_reasons)


@dataclass(frozen=True)
class VendorRule:
    vendor_name: str
    match_text: str
    category: str | None
    frequency: str | None
    due_day: int | None
    payment_type: str | None
    default_status: str
    active: bool
    display_name: str | None = None
    notes: str = ""
