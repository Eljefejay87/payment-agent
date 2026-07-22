from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from shared.integrations.microsoft_graph import GraphClient as MicrosoftGraphClient

from .config import Settings
from .parser import is_audio_attachment, is_vaspian_voicemail

LOGGER = logging.getLogger(__name__)


class VoicemailGraphClient(MicrosoftGraphClient):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        super().__init__(
            tenant_id=settings.graph_tenant_id,
            client_id=settings.graph_client_id,
            client_secret=settings.graph_client_secret,
        )

    def find_voicemail_messages(self) -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(hours=self.settings.lookback_hours)
        filter_query = f"receivedDateTime ge {since.isoformat().replace('+00:00', 'Z')}"
        messages = self.list_user_messages(
            mailbox_user_id=self.settings.mailbox_user_id,
            filter_query=filter_query,
            select="id,internetMessageId,subject,receivedDateTime,from,body,hasAttachments",
            orderby="receivedDateTime desc",
        )
        candidates = [
            message
            for message in messages
            if is_vaspian_voicemail(
                message,
                sender_email=self.settings.sender_email,
                subject_contains=self.settings.subject_contains,
            )
        ]
        for message in candidates:
            if message.get("hasAttachments"):
                message["attachments"] = self.list_message_attachments(message["id"])
        return candidates

    def list_message_attachments(self, message_id: str) -> list[dict[str, Any]]:
        user = quote(self.settings.mailbox_user_id)
        message = quote(message_id, safe="")
        data = self.request(
            "GET",
            f"/users/{user}/messages/{message}/attachments?$select=id,name,contentType,size,isInline",
        )
        attachments = data.get("value", [])
        return [
            self.get_audio_attachment_content(message_id, attachment)
            if is_audio_attachment(
                attachment.get("name", ""),
                attachment.get("contentType", ""),
            )
            else attachment
            for attachment in attachments
        ]

    def get_audio_attachment_content(
        self,
        message_id: str,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        attachment_id = attachment.get("id")
        if not attachment_id:
            return attachment
        user = quote(self.settings.mailbox_user_id)
        message = quote(message_id, safe="")
        encoded_attachment = quote(str(attachment_id), safe="")
        try:
            detail = self.request(
                "GET",
                f"/users/{user}/messages/{message}/attachments/{encoded_attachment}",
            )
            merged = {**attachment, **detail}
            content = merged.get("contentBytes")
            merged["content_bytes"] = base64.b64decode(content) if content else None
            return merged
        except Exception as exc:
            LOGGER.warning(
                "Could not download voicemail audio attachment: %s",
                type(exc).__name__,
            )
            return {**attachment, "content_bytes": None}


GraphClient = VoicemailGraphClient
