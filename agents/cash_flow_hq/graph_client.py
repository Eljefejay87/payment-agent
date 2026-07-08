from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from shared.integrations.microsoft_graph import GRAPH_ROOT
from shared.integrations.microsoft_graph import GraphClient as MicrosoftGraphClient

from .config import CashFlowHQSettings
from .parser import BILL_TERMS, is_bill_related, message_from_graph
from .models import BillEmail


class CashFlowGraphClient(MicrosoftGraphClient):
    def __init__(self, settings: CashFlowHQSettings) -> None:
        self.settings = settings
        super().__init__(
            tenant_id=settings.graph_tenant_id,
            client_id=settings.graph_client_id,
            client_secret=settings.graph_client_secret,
        )

    def find_bill_messages(self, days: int, limit: int) -> list[BillEmail]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        filter_query = f"receivedDateTime ge {since.isoformat().replace('+00:00', 'Z')}"
        messages = self.list_limited_inbox_messages(
            filter_query=filter_query,
            limit=limit,
        )
        candidates: list[BillEmail] = []
        for raw in messages:
            message = message_from_graph(raw)
            if is_bill_related(message):
                full = self.get_message_detail(raw.get("id", ""))
                if full.get("hasAttachments"):
                    full["attachments"] = self.list_message_attachments(raw.get("id", ""))
                message = message_from_graph(full)
                candidates.append(message)
        return candidates

    def get_message_detail(self, message_id: str) -> dict:
        if not message_id:
            return {}
        user = quote(self.settings.mailbox_user_id)
        message = quote(message_id, safe="")
        select = quote(
            "id,internetMessageId,subject,receivedDateTime,from,body,bodyPreview,webLink,hasAttachments",
            safe=",",
        )
        return self.request(
            "GET",
            f"/users/{user}/messages/{message}?$select={select}",
        )

    def list_message_attachments(self, message_id: str) -> list[dict]:
        if not message_id:
            return []
        user = quote(self.settings.mailbox_user_id)
        message = quote(message_id, safe="")
        data = self.request(
            "GET",
            f"/users/{user}/messages/{message}/attachments?$select=id,name,contentType,size,isInline",
        )
        attachments = data.get("value", [])
        return [
            self.get_pdf_attachment_content(message_id, attachment)
            if is_pdf_attachment(attachment)
            else attachment
            for attachment in attachments
        ]

    def get_pdf_attachment_content(self, message_id: str, attachment: dict) -> dict:
        attachment_id = attachment.get("id")
        if not message_id or not attachment_id:
            return attachment
        user = quote(self.settings.mailbox_user_id)
        message = quote(message_id, safe="")
        encoded_attachment = quote(attachment_id, safe="")
        detail = self.request(
            "GET",
            f"/users/{user}/messages/{message}/attachments/{encoded_attachment}",
        )
        merged = {**attachment, **detail}
        content = merged.get("contentBytes")
        if content:
            try:
                merged["content_bytes"] = base64.b64decode(content)
            except Exception:
                merged["content_bytes"] = None
        return merged

    def list_limited_inbox_messages(self, filter_query: str, limit: int) -> list[dict]:
        user = quote(self.settings.mailbox_user_id)
        encoded_filter = quote(filter_query, safe="=$'(), ")
        select = quote("id,internetMessageId,subject,receivedDateTime,from,bodyPreview,webLink,hasAttachments", safe=",")
        orderby = quote("receivedDateTime desc", safe=" ")
        top = min(max(limit, 1), 50)
        path = (
            f"/users/{user}/mailFolders/inbox/messages?$top={top}&$select={select}"
            f"&$filter={encoded_filter}&$orderby={orderby}"
        )
        messages: list[dict] = []
        while path and len(messages) < limit:
            data = self.request("GET", path)
            messages.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink", "")
            path = next_link.replace(GRAPH_ROOT, "", 1) if next_link.startswith(GRAPH_ROOT) else next_link
        return messages[:limit]


def bill_search_terms() -> list[str]:
    return list(BILL_TERMS)


def is_pdf_attachment(attachment: dict) -> bool:
    content_type = (attachment.get("contentType") or "").lower()
    name = (attachment.get("name") or "").lower()
    return "pdf" in content_type or name.endswith(".pdf")
