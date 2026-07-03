from __future__ import annotations

import json
import logging
from dataclasses import asdict

from .config import Settings
from .graph_client import GraphClient
from .parser import parse_voicemail_message

LOGGER = logging.getLogger(__name__)


class VoicemailTrackerAgent:
    def __init__(self, settings: Settings, graph: GraphClient | None = None) -> None:
        self.settings = settings
        self.graph = graph or GraphClient(settings)

    def scan_once(self) -> list[dict]:
        messages = self.graph.find_voicemail_messages()
        LOGGER.info("Found %s Vaspian voicemail email(s)", len(messages))
        records = [
            asdict(parse_voicemail_message(message, self.settings.timezone))
            for message in messages
        ]
        for record in records:
            LOGGER.info(
                "Voicemail detected: %s %s from %s duration=%s source=%s",
                record["date_received"],
                record["time_received"],
                record["phone_number"] or "unknown phone",
                record["duration"] or "unknown duration",
                record["source_email_id"],
            )
        return records

    def scan_sample(self, sample_messages: list[dict]) -> list[dict]:
        records = [
            asdict(parse_voicemail_message(message, self.settings.timezone))
            for message in sample_messages
        ]
        print(json.dumps(records, indent=2))
        return records
