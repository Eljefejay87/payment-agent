from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config import get_int, load_environment


@dataclass(frozen=True)
class Settings:
    log_level: str
    timezone: str
    mailbox_user_id: str
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    sender_email: str
    subject_contains: str
    lookback_hours: int


def load_settings(env_file: str | None = None) -> Settings:
    load_environment(env_file)
    return Settings(
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        mailbox_user_id=os.getenv("VOICEMAIL_MAILBOX_USER_ID", os.getenv("MAILBOX_USER_ID", "")),
        graph_tenant_id=os.getenv("MS_GRAPH_TENANT_ID", ""),
        graph_client_id=os.getenv("MS_GRAPH_CLIENT_ID", ""),
        graph_client_secret=os.getenv("MS_GRAPH_CLIENT_SECRET", ""),
        sender_email=os.getenv("VOICEMAIL_SENDER_EMAIL", ""),
        subject_contains=os.getenv("VOICEMAIL_SUBJECT_CONTAINS", "voicemail"),
        lookback_hours=get_int("VOICEMAIL_LOOKBACK_HOURS", 48),
    )


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    if not settings.mailbox_user_id:
        errors.append("VOICEMAIL_MAILBOX_USER_ID or MAILBOX_USER_ID is required.")
    if not settings.graph_tenant_id:
        errors.append("MS_GRAPH_TENANT_ID is required.")
    if not settings.graph_client_id:
        errors.append("MS_GRAPH_CLIENT_ID is required.")
    if not settings.graph_client_secret:
        errors.append("MS_GRAPH_CLIENT_SECRET is required.")
    if not settings.sender_email and not settings.subject_contains:
        errors.append("VOICEMAIL_SENDER_EMAIL or VOICEMAIL_SUBJECT_CONTAINS is required.")
    return errors
