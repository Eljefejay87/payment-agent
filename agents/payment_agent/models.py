from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ParsedPayment:
    account_number: str
    payment_type: str | None
    note: str | None
    payment_date: str | None
    payment_amount_cents: int


@dataclass(frozen=True)
class PaymentRecord:
    message_id: str
    account_number: str
    payment_type: str | None
    note: str | None
    payment_date: str | None
    payment_amount_cents: int
    email_received_at: str
    email_subject: str
    sender_email: str | None
    snapshot_path: str | None = None

    @property
    def amount(self) -> Decimal:
        return Decimal(self.payment_amount_cents) / Decimal(100)
