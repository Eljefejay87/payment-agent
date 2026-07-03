from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config import get_int, load_environment


@dataclass(frozen=True)
class DashboardSettings:
    host: str
    port: int
    log_level: str
    manager_checklist_url: str
    manager_checklist_sheet_url: str


def load_dashboard_settings(env_file: str | None = None) -> DashboardSettings:
    load_environment(env_file)
    return DashboardSettings(
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=get_int("DASHBOARD_PORT", 8080),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        manager_checklist_url=os.getenv("MANAGER_CHECKLIST_URL", ""),
        manager_checklist_sheet_url=os.getenv(
            "MANAGER_CHECKLIST_SHEET_URL",
            "https://docs.google.com/spreadsheets/d/1jiKKhZmqTnVRiO9Mi8UicB9xda_MNwPQrDsPNaVOWq0/edit",
        ),
    )
