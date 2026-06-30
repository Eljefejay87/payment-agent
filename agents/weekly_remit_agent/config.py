from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.config import get_bool, get_int, load_environment


@dataclass(frozen=True)
class RemitSettings:
    dry_run: bool
    log_level: str
    database_path: Path
    timezone: str
    mailbox_user_id: str
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    teams_graph_tenant_id: str
    teams_graph_client_id: str
    teams_graph_client_secret: str
    teams_graph_token_cache_path: Path
    broker_name: str
    broker_email: str
    incoming_folder: Path
    sent_folder: Path
    failed_folder: Path
    duplicate_folder: Path
    remit_filename_contains: str
    liquidation_filename_contains: str
    allowed_extensions: tuple[str, ...]
    send_mode: str
    run_day: str
    send_deadline: str
    scan_interval_minutes: int
    send_owner_teams_update: bool
    owner_teams_chat_id: str


def load_remit_settings(env_file: str | None = None) -> RemitSettings:
    load_environment(env_file)
    return RemitSettings(
        dry_run=get_bool("DRY_RUN", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        database_path=Path(os.getenv("DATABASE_PATH", "payment_agent.sqlite3")),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        mailbox_user_id=os.getenv("MAILBOX_USER_ID", ""),
        graph_tenant_id=os.getenv("MS_GRAPH_TENANT_ID", ""),
        graph_client_id=os.getenv("MS_GRAPH_CLIENT_ID", ""),
        graph_client_secret=os.getenv("MS_GRAPH_CLIENT_SECRET", ""),
        teams_graph_tenant_id=os.getenv("TEAMS_GRAPH_TENANT_ID", ""),
        teams_graph_client_id=os.getenv("TEAMS_GRAPH_CLIENT_ID", ""),
        teams_graph_client_secret=os.getenv("TEAMS_GRAPH_CLIENT_SECRET", ""),
        teams_graph_token_cache_path=Path(os.getenv("TEAMS_GRAPH_TOKEN_CACHE_PATH", ".graph_teams_token_cache.bin")),
        broker_name=os.getenv("REMIT_BROKER_NAME", "ICR"),
        broker_email=os.getenv("REMIT_BROKER_EMAIL", ""),
        incoming_folder=Path(os.getenv("REMIT_INCOMING_FOLDER", "remits/incoming/ICR")),
        sent_folder=Path(os.getenv("REMIT_SENT_FOLDER", "remits/sent/ICR")),
        failed_folder=Path(os.getenv("REMIT_FAILED_FOLDER", "remits/failed/ICR")),
        duplicate_folder=Path(os.getenv("REMIT_DUPLICATE_FOLDER", "remits/duplicates/ICR")),
        remit_filename_contains=os.getenv("REMIT_REMIT_FILENAME_CONTAINS", "United Remit"),
        liquidation_filename_contains=os.getenv("REMIT_LIQUIDATION_FILENAME_CONTAINS", "United Liq"),
        allowed_extensions=_csv_tuple(os.getenv("REMIT_ALLOWED_EXTENSIONS", ".xlsx,.xls")),
        send_mode=os.getenv("REMIT_SEND_MODE", "send").lower(),
        run_day=os.getenv("REMIT_RUN_DAY", "monday").lower(),
        send_deadline=os.getenv("REMIT_SEND_DEADLINE", "15:00"),
        scan_interval_minutes=get_int("REMIT_SCAN_INTERVAL_MINUTES", 15),
        send_owner_teams_update=get_bool("REMIT_SEND_OWNER_TEAMS_UPDATE", True),
        owner_teams_chat_id=os.getenv("REMIT_OWNER_TEAMS_CHAT_ID", ""),
    )


def validate_remit_settings(settings: RemitSettings) -> list[str]:
    errors: list[str] = []
    if not settings.mailbox_user_id:
        errors.append("MAILBOX_USER_ID is required for Weekly Remit Agent email sending.")
    if not settings.graph_tenant_id:
        errors.append("MS_GRAPH_TENANT_ID is required for Weekly Remit Agent email sending.")
    if not settings.graph_client_id:
        errors.append("MS_GRAPH_CLIENT_ID is required for Weekly Remit Agent email sending.")
    if not settings.graph_client_secret:
        errors.append("MS_GRAPH_CLIENT_SECRET is required for Weekly Remit Agent email sending.")
    if not settings.broker_name:
        errors.append("REMIT_BROKER_NAME is required.")
    if not settings.broker_email:
        errors.append("REMIT_BROKER_EMAIL is required.")
    if settings.send_mode != "send":
        errors.append("REMIT_SEND_MODE must be send for Weekly Remit Agent V1.")
    if settings.send_owner_teams_update:
        if not settings.owner_teams_chat_id:
            errors.append("REMIT_OWNER_TEAMS_CHAT_ID is required when REMIT_SEND_OWNER_TEAMS_UPDATE=true.")
        if not settings.teams_graph_tenant_id:
            errors.append("TEAMS_GRAPH_TENANT_ID is required when owner Teams updates are enabled.")
        if not settings.teams_graph_client_id:
            errors.append("TEAMS_GRAPH_CLIENT_ID is required when owner Teams updates are enabled.")
        if not settings.teams_graph_client_secret:
            errors.append("TEAMS_GRAPH_CLIENT_SECRET is required when owner Teams updates are enabled.")
    return errors


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())
