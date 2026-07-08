from __future__ import annotations

import logging
import time
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
NOTION_ROOT = "https://api.notion.com/v1"


class NotionClient:
    def __init__(self, api_key: str, notion_version: str) -> None:
        self.api_key = api_key
        self.notion_version = notion_version

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("https://") else f"{NOTION_ROOT}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["Notion-Version"] = self.notion_version
        headers.setdefault("Accept", "application/json")
        headers.setdefault("Content-Type", "application/json")

        for attempt in range(3):
            response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
                LOGGER.warning("Notion request throttled or unavailable; retrying in %.1f seconds.", delay)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Notion {method} {url} failed: {response.status_code} {response.text[:500]}")
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        raise RuntimeError(f"Notion {method} {url} failed after retries.")
