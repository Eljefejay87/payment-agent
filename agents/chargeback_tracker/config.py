from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared.config import load_environment


PRODUCTION_SPREADSHEET_ID = "1i89CMRBpbi_hEi6GCGtYne7Y6q_e8GgJbREVET0njuU"
PRODUCTION_SHEET_NAME = "Sample_Chargeback_Tracker"


@dataclass(frozen=True)
class ChargebackSettings:
    spreadsheet_id: str
    sheet_name: str
    service_account_file: Path | None
    log_level: str


def load_chargeback_settings(env_file: str | None = None) -> ChargebackSettings:
    load_environment(env_file)
    credential_path = os.getenv("CHARGEBACK_GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    return ChargebackSettings(
        spreadsheet_id=os.getenv("CHARGEBACK_SPREADSHEET_ID", "").strip(),
        sheet_name=os.getenv("CHARGEBACK_SHEET_NAME", PRODUCTION_SHEET_NAME).strip(),
        service_account_file=Path(credential_path).expanduser() if credential_path else None,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def validate_chargeback_settings(
    settings: ChargebackSettings,
    require_credentials: bool,
) -> list[str]:
    errors: list[str] = []
    if not settings.spreadsheet_id:
        errors.append("CHARGEBACK_SPREADSHEET_ID is required.")
    elif settings.spreadsheet_id != PRODUCTION_SPREADSHEET_ID:
        errors.append("CHARGEBACK_SPREADSHEET_ID must identify the existing United Charge Back Tracker.")
    if not settings.sheet_name:
        errors.append("CHARGEBACK_SHEET_NAME is required.")
    elif settings.sheet_name != PRODUCTION_SHEET_NAME:
        errors.append("CHARGEBACK_SHEET_NAME must identify the existing production worksheet.")
    if require_credentials:
        if settings.service_account_file is None:
            errors.append("CHARGEBACK_GOOGLE_SERVICE_ACCOUNT_FILE is required for Google Sheet access.")
        elif not settings.service_account_file.is_file():
            errors.append("CHARGEBACK_GOOGLE_SERVICE_ACCOUNT_FILE does not exist.")
    return errors
