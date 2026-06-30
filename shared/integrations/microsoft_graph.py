from __future__ import annotations

import logging
import base64
import mimetypes
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import quote

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
LOGGER = logging.getLogger(__name__)


class GraphClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        delegated_token_cache_path: Path | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.delegated_token_cache_path = delegated_token_cache_path
        self._token: str | None = None
        self._mail_folder_cache: dict[tuple[str, str], str] = {}

    @cached_property
    def _msal(self):
        import msal

        return msal

    @cached_property
    def _requests(self):
        import requests

        return requests

    def token(self) -> str:
        if self._token:
            return self._token
        app = self._msal.ConfidentialClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"Could not get Microsoft Graph token: {result.get('error_description', result)}")
        self._token = result["access_token"]
        return self._token

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("https://") else f"{GRAPH_ROOT}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token()}"
        headers.setdefault("Accept", "application/json")
        response = self._requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"Graph {method} {url} failed: {response.status_code} {response.text[:500]}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def delegated_request(self, method: str, path: str, scopes: list[str], **kwargs: Any) -> Any:
        url = path if path.startswith("https://") else f"{GRAPH_ROOT}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.delegated_token(scopes)}"
        headers.setdefault("Accept", "application/json")
        response = self._requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"Graph delegated {method} {url} failed: {response.status_code} {response.text[:500]}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def delegated_token(self, scopes: list[str]) -> str:
        cache = self._msal.SerializableTokenCache()
        cache_path = self.delegated_token_cache_path
        if cache_path and cache_path.exists():
            cache.deserialize(cache_path.read_text())

        app = self._msal.PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=cache,
        )
        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(scopes, account=accounts[0])
        if not result:
            flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise RuntimeError(f"Could not create Microsoft Graph device flow: {flow}")
            print(flow["message"])
            result = app.acquire_token_by_device_flow(flow)

        if cache.has_state_changed and cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True) if cache_path.parent != Path(".") else None
            cache_path.write_text(cache.serialize())
            cache_path.chmod(0o600)

        if "access_token" not in result:
            raise RuntimeError(f"Could not get delegated Microsoft Graph token: {result.get('error_description', result)}")
        return result["access_token"]

    def list_user_messages(
        self,
        mailbox_user_id: str,
        filter_query: str,
        select: str = "id,internetMessageId,subject,receivedDateTime,from,body",
        orderby: str = "receivedDateTime asc",
        top: int = 50,
    ) -> list[dict[str, Any]]:
        user = quote(mailbox_user_id)
        encoded_filter = quote(filter_query, safe="=$'(), ")
        path = (
            f"/users/{user}/messages?$top={top}&$select={select}"
            f"&$filter={encoded_filter}&$orderby={quote(orderby, safe=' ')}"
        )

        messages: list[dict[str, Any]] = []
        while path:
            LOGGER.debug("Fetching Graph messages page")
            data = self.request("GET", path)
            messages.extend(data.get("value", []))
            path = data.get("@odata.nextLink", "")
        return messages

    def list_user_mail_folder_messages(
        self,
        mailbox_user_id: str,
        folder_id: str,
        filter_query: str,
        select: str = "id,internetMessageId,subject,receivedDateTime,from,body",
        orderby: str = "receivedDateTime asc",
        top: int = 50,
    ) -> list[dict[str, Any]]:
        user = quote(mailbox_user_id)
        folder = quote(folder_id, safe="")
        encoded_filter = quote(filter_query, safe="=$'(), ")
        path = (
            f"/users/{user}/mailFolders/{folder}/messages?$top={top}&$select={select}"
            f"&$filter={encoded_filter}&$orderby={quote(orderby, safe=' ')}"
        )

        messages: list[dict[str, Any]] = []
        while path:
            LOGGER.debug("Fetching Graph mail folder messages page")
            data = self.request("GET", path)
            messages.extend(data.get("value", []))
            path = data.get("@odata.nextLink", "")
        return messages

    def mark_user_message_read(self, mailbox_user_id: str, message_id: str) -> None:
        user = quote(mailbox_user_id)
        message = quote(message_id, safe="")
        self.request(
            "PATCH",
            f"/users/{user}/messages/{message}",
            json={"isRead": True},
            headers={"Content-Type": "application/json"},
        )
        LOGGER.info("Email marked read")

    def move_user_message(self, mailbox_user_id: str, message_id: str, folder_name: str) -> None:
        user = quote(mailbox_user_id)
        message = quote(message_id, safe="")
        folder_id = self.get_or_create_user_mail_folder(mailbox_user_id, folder_name)
        self.request(
            "POST",
            f"/users/{user}/messages/{message}/move",
            json={"destinationId": folder_id},
            headers={"Content-Type": "application/json"},
        )
        LOGGER.info("Email moved to %s", folder_name)

    def get_or_create_user_mail_folder(self, mailbox_user_id: str, folder_name: str) -> str:
        cache_key = (mailbox_user_id.lower(), folder_name.lower())
        if cache_key in self._mail_folder_cache:
            return self._mail_folder_cache[cache_key]

        existing = self._find_user_mail_folder(mailbox_user_id, folder_name)
        if existing:
            self._mail_folder_cache[cache_key] = existing
            return existing

        user = quote(mailbox_user_id)
        created = self.request(
            "POST",
            f"/users/{user}/mailFolders",
            json={"displayName": folder_name},
            headers={"Content-Type": "application/json"},
        )
        folder_id = created["id"]
        self._mail_folder_cache[cache_key] = folder_id
        LOGGER.info("Created mail folder %s", folder_name)
        return folder_id

    def _find_user_mail_folder(self, mailbox_user_id: str, folder_name: str) -> str | None:
        user = quote(mailbox_user_id)
        path = f"/users/{user}/mailFolders?$top=100&$select=id,displayName"
        while path:
            data = self.request("GET", path)
            for folder in data.get("value", []):
                if (folder.get("displayName") or "").lower() == folder_name.lower():
                    return folder.get("id")
            path = data.get("@odata.nextLink", "")
        return None

    def post_chat_message(self, chat_id: str, html_content: str) -> None:
        path = f"/chats/{quote(chat_id)}/messages"
        self.delegated_request(
            "POST",
            path,
            scopes=["ChatMessage.Send"],
            json={"body": {"contentType": "html", "content": html_content}},
            headers={"Content-Type": "application/json"},
        )
        LOGGER.info("Microsoft Graph chat message sent successfully")

    def list_recent_chats(self) -> list[dict[str, Any]]:
        data = self.delegated_request(
            "GET",
            "/me/chats?$top=20&$expand=members&$orderby=lastMessagePreview/createdDateTime%20desc",
            scopes=["Chat.ReadBasic"],
        )
        return data.get("value", [])

    def post_channel_message(self, team_id: str, channel_id: str, html_content: str) -> None:
        path = f"/teams/{quote(team_id)}/channels/{quote(channel_id)}/messages"
        self.request(
            "POST",
            path,
            json={"body": {"contentType": "html", "content": html_content}},
            headers={"Content-Type": "application/json"},
        )
        LOGGER.info("Microsoft Graph channel message sent successfully")

    def send_user_mail(
        self,
        mailbox_user_id: str,
        to_recipients: list[str],
        subject: str,
        html_content: str,
        attachments: list[Path] | None = None,
        cc_recipients: list[str] | None = None,
        bcc_recipients: list[str] | None = None,
    ) -> None:
        user = quote(mailbox_user_id)
        message: dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_content,
            },
            "toRecipients": self._email_recipients(to_recipients),
        }
        if cc_recipients:
            message["ccRecipients"] = self._email_recipients(cc_recipients)
        if bcc_recipients:
            message["bccRecipients"] = self._email_recipients(bcc_recipients)
        if attachments:
            message["attachments"] = [
                self._file_attachment(path)
                for path in attachments
            ]

        self.request(
            "POST",
            f"/users/{user}/sendMail",
            json={"message": message, "saveToSentItems": True},
            headers={"Content-Type": "application/json"},
        )
        LOGGER.info("Microsoft Graph email sent successfully")

    def _email_recipients(self, addresses: list[str]) -> list[dict[str, Any]]:
        return [
            {"emailAddress": {"address": address.strip()}}
            for address in addresses
            if address.strip()
        ]

    def _file_attachment(self, path: Path) -> dict[str, Any]:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": path.name,
            "contentType": content_type,
            "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
