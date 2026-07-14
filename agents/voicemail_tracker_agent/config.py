from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.config import get_bool, get_int, load_environment


DEFAULT_LOCAL_DATA_DIR = Path.home() / "Library/Application Support/UCM/payment-agent"


@dataclass(frozen=True)
class Settings:
    dry_run: bool
    log_level: str
    timezone: str
    mailbox_user_id: str
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    sender_email: str
    subject_contains: str
    lookback_hours: int
    scan_interval_minutes: int
    summary_time: str
    run_startup_scan: bool
    status_path: Path = DEFAULT_LOCAL_DATA_DIR / "voicemail_status.json"
    runtime_state_path: Path = DEFAULT_LOCAL_DATA_DIR / "voicemail_runtime_state.json"
    health_path: Path = DEFAULT_LOCAL_DATA_DIR / "voicemail_health.json"


def load_settings(env_file: str | None = None) -> Settings:
    load_environment(env_file)
    data_dir = Path(
        os.getenv("VOICEMAIL_DATA_DIR", str(DEFAULT_LOCAL_DATA_DIR))
    ).expanduser()
    return Settings(
        dry_run=get_bool("DRY_RUN", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        mailbox_user_id=os.getenv("VOICEMAIL_MAILBOX_USER_ID", os.getenv("MAILBOX_USER_ID", "")),
        graph_tenant_id=os.getenv("MS_GRAPH_TENANT_ID", ""),
        graph_client_id=os.getenv("MS_GRAPH_CLIENT_ID", ""),
        graph_client_secret=os.getenv("MS_GRAPH_CLIENT_SECRET", ""),
        sender_email=os.getenv("VOICEMAIL_SENDER_EMAIL", ""),
        subject_contains=os.getenv("VOICEMAIL_SUBJECT_CONTAINS", "voicemail"),
        lookback_hours=get_int("VOICEMAIL_LOOKBACK_HOURS", 48),
        scan_interval_minutes=get_int("VOICEMAIL_SCAN_INTERVAL_MINUTES", 15),
        summary_time=os.getenv("VOICEMAIL_SUMMARY_TIME", "08:50"),
        run_startup_scan=get_bool("VOICEMAIL_RUN_STARTUP_SCAN", True),
        status_path=_path_env("VOICEMAIL_STATUS_PATH", data_dir / "voicemail_status.json"),
        runtime_state_path=_path_env(
            "VOICEMAIL_RUNTIME_STATE_PATH",
            data_dir / "voicemail_runtime_state.json",
        ),
        health_path=_path_env("VOICEMAIL_HEALTH_PATH", data_dir / "voicemail_health.json"),
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


def _path_env(name: str, default: Path) -> Path:
    return Path(os.getenv(name) or str(default)).expanduser()
