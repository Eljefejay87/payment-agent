from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.config import get_bool, get_int, load_environment


@dataclass(frozen=True)
class Settings:
    dry_run: bool
    log_level: str
    database_path: Path
    timezone: str
    email_provider: str
    mailbox_user_id: str
    sender_email: str
    subject_contains: str
    scan_interval_minutes: int
    lookback_hours: int
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    daily_report_time: str
    report_mode: str
    teams_webhook_url: str
    teams_post_method: str
    teams_graph_tenant_id: str
    teams_graph_client_id: str
    teams_graph_client_secret: str
    teams_graph_token_cache_path: Path
    teams_chat_id: str
    teams_team_id: str
    teams_channel_id: str
    save_email_html: bool
    email_snapshot_dir: Path

    @property
    def realtime_enabled(self) -> bool:
        return self.report_mode in {"realtime", "both"}

    @property
    def daily_enabled(self) -> bool:
        return self.report_mode in {"daily", "both"}


def load_settings(env_file: str | None = None) -> Settings:
    load_environment(env_file)
    return Settings(
        dry_run=get_bool("DRY_RUN", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        database_path=Path(os.getenv("DATABASE_PATH", "payment_agent.sqlite3")),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        email_provider=os.getenv("EMAIL_PROVIDER", "microsoft365").lower(),
        mailbox_user_id=os.getenv("MAILBOX_USER_ID", ""),
        sender_email=os.getenv("SENDER_EMAIL", ""),
        subject_contains=os.getenv("SUBJECT_CONTAINS", "Online Payment -"),
        scan_interval_minutes=get_int("SCAN_INTERVAL_MINUTES", 15),
        lookback_hours=get_int("LOOKBACK_HOURS", 48),
        graph_tenant_id=os.getenv("MS_GRAPH_TENANT_ID", ""),
        graph_client_id=os.getenv("MS_GRAPH_CLIENT_ID", ""),
        graph_client_secret=os.getenv("MS_GRAPH_CLIENT_SECRET", ""),
        daily_report_time=os.getenv("DAILY_REPORT_TIME", "17:00"),
        report_mode=os.getenv("REPORT_MODE", "daily").lower(),
        teams_webhook_url=os.getenv("TEAMS_WEBHOOK_URL", ""),
        teams_post_method=os.getenv("TEAMS_POST_METHOD", "webhook").lower(),
        teams_graph_tenant_id=os.getenv("TEAMS_GRAPH_TENANT_ID", ""),
        teams_graph_client_id=os.getenv("TEAMS_GRAPH_CLIENT_ID", ""),
        teams_graph_client_secret=os.getenv("TEAMS_GRAPH_CLIENT_SECRET", ""),
        teams_graph_token_cache_path=Path(os.getenv("TEAMS_GRAPH_TOKEN_CACHE_PATH", ".graph_teams_token_cache.bin")),
        teams_chat_id=os.getenv("TEAMS_CHAT_ID", ""),
        teams_team_id=os.getenv("TEAMS_TEAM_ID", ""),
        teams_channel_id=os.getenv("TEAMS_CHANNEL_ID", ""),
        save_email_html=get_bool("SAVE_EMAIL_HTML", False),
        email_snapshot_dir=Path(os.getenv("EMAIL_SNAPSHOT_DIR", "email_snapshots")),
    )


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    if settings.email_provider != "microsoft365":
        errors.append("EMAIL_PROVIDER currently supports only 'microsoft365'.")
    if not settings.mailbox_user_id:
        errors.append("MAILBOX_USER_ID is required.")
    if not settings.sender_email:
        errors.append("SENDER_EMAIL is required.")
    if not settings.graph_tenant_id:
        errors.append("MS_GRAPH_TENANT_ID is required.")
    if not settings.graph_client_id:
        errors.append("MS_GRAPH_CLIENT_ID is required.")
    if not settings.graph_client_secret:
        errors.append("MS_GRAPH_CLIENT_SECRET is required.")
    if settings.teams_post_method == "webhook" and not settings.dry_run:
        if not settings.teams_webhook_url:
            errors.append("TEAMS_WEBHOOK_URL is required when TEAMS_POST_METHOD=webhook and DRY_RUN=false.")
    if settings.teams_post_method == "graph_chat" and not settings.teams_chat_id:
        errors.append("TEAMS_CHAT_ID is required when TEAMS_POST_METHOD=graph_chat.")
    if settings.teams_post_method == "graph_chat":
        if not settings.teams_graph_tenant_id:
            errors.append("TEAMS_GRAPH_TENANT_ID is required when TEAMS_POST_METHOD=graph_chat.")
        if not settings.teams_graph_client_id:
            errors.append("TEAMS_GRAPH_CLIENT_ID is required when TEAMS_POST_METHOD=graph_chat.")
        if not settings.teams_graph_client_secret:
            errors.append("TEAMS_GRAPH_CLIENT_SECRET is required when TEAMS_POST_METHOD=graph_chat.")
    if settings.teams_post_method == "graph_channel":
        if not settings.teams_team_id or not settings.teams_channel_id:
            errors.append("TEAMS_TEAM_ID and TEAMS_CHANNEL_ID are required when TEAMS_POST_METHOD=graph_channel.")
    if settings.report_mode not in {"daily", "realtime", "both"}:
        errors.append("REPORT_MODE must be daily, realtime, or both.")
    return errors
