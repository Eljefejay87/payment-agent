from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .microsoft_graph import GraphClient

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamsMessage:
    title: str
    text: str
    html: str


@dataclass(frozen=True)
class TeamsSettings:
    dry_run: bool
    teams_webhook_url: str
    teams_post_method: str
    teams_chat_id: str
    teams_team_id: str
    teams_channel_id: str


class TeamsNotifier:
    def __init__(self, settings: TeamsSettings, graph: "GraphClient | None" = None) -> None:
        self.settings = settings
        self.graph = graph

    def send(self, message: TeamsMessage) -> None:
        if self.settings.dry_run:
            LOGGER.info("DRY RUN Teams message: %s\n%s", message.title, message.text)
            return

        method = self.settings.teams_post_method
        if method == "webhook":
            self._send_webhook(message)
        elif method == "graph_chat":
            if not self.graph:
                raise RuntimeError("Graph client is required for graph_chat Teams posting.")
            self.graph.post_chat_message(self.settings.teams_chat_id, message.html)
            LOGGER.info("Teams message sent using Microsoft Graph chat")
        elif method == "graph_channel":
            if not self.graph:
                raise RuntimeError("Graph client is required for graph_channel Teams posting.")
            self.graph.post_channel_message(self.settings.teams_team_id, self.settings.teams_channel_id, message.html)
            LOGGER.info("Teams message sent using Microsoft Graph channel")
        else:
            raise ValueError(f"Unsupported TEAMS_POST_METHOD: {method}")

    def _send_webhook(self, message: TeamsMessage) -> None:
        import requests

        response = requests.post(
            self.settings.teams_webhook_url,
            json={"title": message.title, "text": message.text},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Teams webhook failed: {response.status_code} {response.text[:500]}")
        LOGGER.info("Teams message sent using webhook")
