from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.integrations.microsoft_graph import GraphClient as MicrosoftGraphClient

from .config import Settings

LOGGER = logging.getLogger(__name__)

APPROVED_CARD_PAYMENT_SUBJECT = re.compile(
    r"^Online (Debit|Credit|Credit or Debit) Card payment was approved .* Reference number:",
    re.IGNORECASE,
)
USAEPAY_APPROVED_SUBJECT = re.compile(
    r"^Merchant Receipt\\s*-\\s*Transaction Approved\\s*-\\s*Authcode\\s*#\\S+",
    re.IGNORECASE,
)
USAEPAY_SENDER = "noreply@usaepay.com"


def is_payment_subject(subject: str, configured_subject_contains: str) -> bool:
    normalized_subject = subject or ""
    configured = configured_subject_contains.strip().lower()
    if configured and configured in normalized_subject.lower():
        return True
    return bool(
        APPROVED_CARD_PAYMENT_SUBJECT.search(normalized_subject)
        or USAEPAY_APPROVED_SUBJECT.search(normalized_subject)
    )


def is_payment_sender(sender_email: str, configured_sender_email: str) -> bool:
    sender = (sender_email or "").strip().lower()
    configured = (configured_sender_email or "").strip().lower()
    return sender == USAEPAY_SENDER or bool(configured and sender == configured)


class PaymentGraphClient(MicrosoftGraphClient):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        super().__init__(
            tenant_id=settings.graph_tenant_id,
            client_id=settings.graph_client_id,
            client_secret=settings.graph_client_secret,
        )

    def find_payment_messages(self) -> list[dict[str, Any]]:
        messages = self.find_recent_message_headers(include_body=True)
        sender_filter = self.settings.sender_email.strip().lower()
        subject_filter = self.settings.subject_contains.strip().lower()
        return [
            message
            for message in messages
            if is_payment_subject(message.get("subject") or "", subject_filter)
            and is_payment_sender(self._sender_email(message), sender_filter)
        ]

    def find_recent_message_headers(self, include_body: bool = False) -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(hours=self.settings.lookback_hours)
        filter_query = f"receivedDateTime ge {since.isoformat().replace('+00:00', 'Z')}"
        select = "id,internetMessageId,subject,receivedDateTime,from"
        if include_body:
            select = f"{select},body"
        return self.list_user_mail_folder_messages(
            mailbox_user_id=self.settings.mailbox_user_id,
            folder_id="inbox",
            filter_query=filter_query,
            select=select,
            orderby="receivedDateTime desc",
        )

    def _sender_email(self, message: dict[str, Any]) -> str:
        return (
            message.get("from", {})
            .get("emailAddress", {})
            .get("address", "")
        )


GraphClient = PaymentGraphClient
