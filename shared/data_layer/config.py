from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config import get_bool, get_int, load_environment


@dataclass(frozen=True)
class SharedDataSyncSettings:
    enabled: bool
    interval_minutes: int
    source: str
    limit: int
    run_at_start: bool


def load_shared_data_sync_settings(env_file: str | None = None) -> SharedDataSyncSettings:
    load_environment(env_file)
    return SharedDataSyncSettings(
        enabled=get_bool("SHARED_DATA_SYNC_ENABLED", True),
        interval_minutes=get_int("SHARED_DATA_SYNC_INTERVAL_MINUTES", 60),
        source=os.getenv("SHARED_DATA_SYNC_SOURCE", "all").strip().lower(),
        limit=get_int("SHARED_DATA_SYNC_LIMIT", 100),
        run_at_start=get_bool("SHARED_DATA_SYNC_RUN_AT_START", True),
    )


def validate_shared_data_sync_settings(settings: SharedDataSyncSettings) -> list[str]:
    errors = []
    if settings.interval_minutes <= 0:
        errors.append("SHARED_DATA_SYNC_INTERVAL_MINUTES must be greater than zero.")
    if settings.limit <= 0:
        errors.append("SHARED_DATA_SYNC_LIMIT must be greater than zero.")
    if settings.source not in {"cash-flow", "icr", "all"}:
        errors.append("SHARED_DATA_SYNC_SOURCE must be cash-flow, icr, or all.")
    return errors
