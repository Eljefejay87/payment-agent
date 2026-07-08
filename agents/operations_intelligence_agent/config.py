from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from shared.config import get_bool, get_int, load_environment


@dataclass(frozen=True)
class OperationsSettings:
    dry_run: bool
    log_level: str
    database_path: Path
    timezone: str
    scan_interval_minutes: int
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    teams_graph_token_cache_path: Path
    leadership_chat_id: str
    lookback_hours: int
    daily_scan_start: str
    daily_scan_end: str
    screenshots_dir: Path
    reports_dir: Path
    ocr_command: str
    ocr_min_confidence: float
    ocr_debug: bool
    post_summary_to_teams: bool
    low_quality_action: str
    collector_codes: tuple[str, ...]


def load_operations_settings(env_file: str | None = None) -> OperationsSettings:
    load_environment(env_file)
    return OperationsSettings(
        dry_run=get_bool("DRY_RUN", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        database_path=Path(os.getenv("DATABASE_PATH", "payment_agent.sqlite3")),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        scan_interval_minutes=get_int("OPS_SCAN_INTERVAL_MINUTES", 10),
        graph_tenant_id=os.getenv("TEAMS_GRAPH_TENANT_ID") or os.getenv("MS_GRAPH_TENANT_ID", ""),
        graph_client_id=os.getenv("TEAMS_GRAPH_CLIENT_ID") or os.getenv("MS_GRAPH_CLIENT_ID", ""),
        graph_client_secret=os.getenv("TEAMS_GRAPH_CLIENT_SECRET") or os.getenv("MS_GRAPH_CLIENT_SECRET", ""),
        teams_graph_token_cache_path=Path(os.getenv("TEAMS_GRAPH_TOKEN_CACHE_PATH", ".graph_teams_token_cache.bin")),
        leadership_chat_id=os.getenv("OPS_LEADERSHIP_CHAT_ID") or os.getenv("TEAMS_CHAT_ID", ""),
        lookback_hours=get_int("OPS_LOOKBACK_HOURS", 30),
        daily_scan_start=os.getenv("OPS_DAILY_SCAN_START", "17:00"),
        daily_scan_end=os.getenv("OPS_DAILY_SCAN_END", "18:15"),
        screenshots_dir=Path(os.getenv("OPS_SCREENSHOTS_DIR", "screenshots/operations-intelligence")),
        reports_dir=Path(os.getenv("OPS_REPORTS_DIR", "reports/operations-intelligence")),
        ocr_command=_ocr_command(os.getenv("OPS_OCR_COMMAND")),
        ocr_min_confidence=float(os.getenv("OPS_OCR_MIN_CONFIDENCE", "0.72")),
        ocr_debug=get_bool("OPS_OCR_DEBUG", False),
        post_summary_to_teams=get_bool("OPS_POST_SUMMARY_TO_TEAMS", True),
        low_quality_action=os.getenv("OPS_LOW_QUALITY_ACTION", "alert").lower(),
        collector_codes=_csv_tuple(os.getenv("OPS_COLLECTOR_CODES", "CSOLO,VMAR,KMAD,UNITED HOUSE")),
    )


def validate_operations_settings(settings: OperationsSettings, *, offline_image_mode: bool = False) -> list[str]:
    errors: list[str] = []
    if not offline_image_mode:
        if not settings.leadership_chat_id:
            errors.append("OPS_LEADERSHIP_CHAT_ID is required to read the Teams leadership chat.")
        if not settings.graph_tenant_id:
            errors.append("TEAMS_GRAPH_TENANT_ID or MS_GRAPH_TENANT_ID is required.")
        if not settings.graph_client_id:
            errors.append("TEAMS_GRAPH_CLIENT_ID or MS_GRAPH_CLIENT_ID is required.")
    if settings.scan_interval_minutes < 1:
        errors.append("OPS_SCAN_INTERVAL_MINUTES must be at least 1.")
    if not 0 <= settings.ocr_min_confidence <= 1:
        errors.append("OPS_OCR_MIN_CONFIDENCE must be between 0 and 1.")
    if settings.low_quality_action not in {"alert", "skip"}:
        errors.append("OPS_LOW_QUALITY_ACTION must be alert or skip.")
    return errors


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(item.strip().upper() for item in value.split(",") if item.strip())


def _ocr_command(configured: str | None) -> str:
    if configured:
        return configured
    for command in ("tesseract", "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if shutil.which(command) or Path(command).exists():
            return command
    return "tesseract"
