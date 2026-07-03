from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from shared.integrations.microsoft_graph import GRAPH_ROOT, GraphClient

from .config import OperationsSettings
from .models import TeamsImage

LOGGER = logging.getLogger(__name__)

CHAT_SCOPES = ["Chat.Read", "ChatMessage.Send"]
MAX_MESSAGE_PAGES = 5
HISTORY_MESSAGE_PAGES = 80


class OperationsGraphClient(GraphClient):
    def __init__(self, settings: OperationsSettings) -> None:
        super().__init__(
            tenant_id=settings.graph_tenant_id,
            client_id=settings.graph_client_id,
            client_secret=settings.graph_client_secret,
            delegated_token_cache_path=settings.teams_graph_token_cache_path,
        )
        self.settings = settings

    def find_recent_images(self) -> list[TeamsImage]:
        since = datetime.now(timezone.utc) - timedelta(hours=self.settings.lookback_hours)
        return self.find_images_since(since, max_pages=MAX_MESSAGE_PAGES)

    def find_images_for_days(self, days: int) -> list[TeamsImage]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return self.find_images_since(since, max_pages=HISTORY_MESSAGE_PAGES)

    def find_images_since(self, since: datetime, *, max_pages: int) -> list[TeamsImage]:
        path = f"/chats/{quote(self.settings.leadership_chat_id)}/messages?$top=50"
        images: list[TeamsImage] = []
        pages_read = 0
        while path and pages_read < max_pages:
            data = self.delegated_request("GET", path, scopes=CHAT_SCOPES)
            pages_read += 1
            oldest_message_in_page = ""
            for message in data.get("value", []):
                created_at = message.get("createdDateTime") or ""
                oldest_message_in_page = created_at or oldest_message_in_page
                if not self._is_recent(created_at, since):
                    continue
                images.extend(self._images_from_message(message))
            if oldest_message_in_page and not self._is_recent(oldest_message_in_page, since):
                break
            path = data.get("@odata.nextLink", "")
        return images

    def _is_recent(self, created_at: str, since: datetime) -> bool:
        if not created_at:
            return False
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed >= since

    def _images_from_message(self, message: dict[str, Any]) -> list[TeamsImage]:
        message_id = message.get("id") or ""
        created_at = message.get("createdDateTime") or ""
        if not message_id:
            return []
        images: list[TeamsImage] = []
        images.extend(self._hosted_content_images(message_id, created_at))
        images.extend(self._attachment_images(message, created_at))
        return images

    def _hosted_content_images(self, message_id: str, created_at: str) -> list[TeamsImage]:
        path = f"/chats/{quote(self.settings.leadership_chat_id)}/messages/{quote(message_id)}/hostedContents"
        try:
            data = self.delegated_request("GET", path, scopes=CHAT_SCOPES)
        except Exception:
            LOGGER.exception("Could not read hosted content for Teams message %s", message_id)
            return []
        images: list[TeamsImage] = []
        for item in data.get("value", []):
            content_type = item.get("contentType") or ""
            image_id = item.get("id") or ""
            content = item.get("contentBytes")
            raw = base64.b64decode(content) if content else self._download_bytes(f"{path}/{quote(image_id)}/$value")
            detected_content_type = content_type or _detect_image_content_type(raw)
            if not detected_content_type.startswith("image/"):
                continue
            images.append(
                TeamsImage(
                    message_id=message_id,
                    image_id=image_id,
                    created_at=created_at,
                    file_name=f"{message_id}-{image_id}.{_extension(detected_content_type)}",
                    content_type=detected_content_type,
                    content=raw,
                )
            )
        return images

    def _attachment_images(self, message: dict[str, Any], created_at: str) -> list[TeamsImage]:
        images: list[TeamsImage] = []
        for attachment in message.get("attachments", []) or []:
            content_type = attachment.get("contentType") or ""
            name = attachment.get("name") or attachment.get("id") or "teams-image"
            if not (content_type.startswith("image/") or _looks_like_image(name)):
                continue
            content_url = attachment.get("contentUrl")
            content = attachment.get("content")
            if content:
                raw = base64.b64decode(content)
            elif content_url:
                raw = self._download_url_bytes(content_url)
            else:
                continue
            images.append(
                TeamsImage(
                    message_id=message.get("id") or "",
                    image_id=attachment.get("id") or name,
                    created_at=created_at,
                    file_name=name,
                    content_type=content_type or "image/png",
                    content=raw,
                )
            )
        return images

    def _download_bytes(self, path: str) -> bytes:
        url = path if path.startswith("https://") else f"{GRAPH_ROOT}{path}"
        response = self._requests.get(
            url,
            headers={"Authorization": f"Bearer {self.delegated_token(CHAT_SCOPES)}"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Graph download failed: {response.status_code} {response.text[:500]}")
        return response.content

    def _download_url_bytes(self, url: str) -> bytes:
        response = self._requests.get(
            url,
            headers={"Authorization": f"Bearer {self.delegated_token(CHAT_SCOPES)}"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Teams attachment download failed: {response.status_code} {response.text[:500]}")
        return response.content


def save_delegated_token(settings: OperationsSettings) -> None:
    OperationsGraphClient(settings).delegated_token(CHAT_SCOPES)


def _extension(content_type: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }.get(content_type.lower(), "png")


def _looks_like_image(name: str) -> bool:
    return Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _detect_image_content_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"
