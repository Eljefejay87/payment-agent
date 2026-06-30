from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config import get_int, load_environment


@dataclass(frozen=True)
class DashboardSettings:
    host: str
    port: int
    log_level: str


def load_dashboard_settings(env_file: str | None = None) -> DashboardSettings:
    load_environment(env_file)
    return DashboardSettings(
        host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
        port=get_int("DASHBOARD_PORT", 8080),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

