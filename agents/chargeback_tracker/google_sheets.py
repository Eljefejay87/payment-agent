from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import requests

from .models import SHEET_HEADERS, ChargebackRecord


@dataclass(frozen=True)
class ChargebackConnectionVerification:
    spreadsheet_title: str
    worksheet_title: str
    can_append: bool


class ChargebackSheet(Protocol):
    def read_values(self) -> list[list[str]]: ...

    def validate_structure(self) -> None: ...

    def append_records(self, records: list[ChargebackRecord]) -> None: ...


class GoogleChargebackSheet:
    """Narrow Google Sheets API client for the existing append-only tracker."""

    SCOPES = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    )

    def __init__(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        service_account_file: Path,
    ) -> None:
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError(
                "Google Sheets writes require the google-auth dependency from requirements.txt."
            ) from exc
        credentials = service_account.Credentials.from_service_account_file(
            str(service_account_file), scopes=self.SCOPES
        )
        self.session: requests.Session = AuthorizedSession(credentials)
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name

    @property
    def _base_url(self) -> str:
        return f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"

    def verify_connection(self) -> ChargebackConnectionVerification:
        """Verify the exact spreadsheet, worksheet, and edit capability without writes."""
        spreadsheet_response = self.session.get(
            self._base_url,
            params={
                "fields": (
                    "spreadsheetId,properties.title,"
                    "sheets.properties(sheetId,title,sheetType,gridProperties)"
                )
            },
            timeout=30,
        )
        _raise_for_status(spreadsheet_response, "open the production Chargeback Tracker")
        spreadsheet = spreadsheet_response.json()
        worksheet = next(
            (
                sheet.get("properties", {})
                for sheet in spreadsheet.get("sheets", [])
                if sheet.get("properties", {}).get("title") == self.sheet_name
            ),
            None,
        )
        if worksheet is None:
            raise RuntimeError(
                f"Production Chargeback Tracker worksheet was not found: {self.sheet_name}"
            )

        drive_response = self.session.get(
            f"https://www.googleapis.com/drive/v3/files/{self.spreadsheet_id}",
            params={
                "fields": (
                    "id,name,mimeType,trashed,"
                    "capabilities(canEdit,canModifyContent)"
                ),
                "supportsAllDrives": "true",
            },
            timeout=30,
        )
        _raise_for_status(drive_response, "verify Chargeback Tracker append access")
        drive_file = drive_response.json()
        capabilities = drive_file.get("capabilities", {})
        can_append = bool(
            capabilities.get("canEdit") and capabilities.get("canModifyContent")
        )
        if drive_file.get("trashed"):
            raise RuntimeError("Production Chargeback Tracker is in the trash.")
        if not can_append:
            raise RuntimeError(
                "The configured Google service account can open the Chargeback Tracker "
                "but does not have content-edit access. Share the existing spreadsheet "
                "with that service account as an editor."
            )
        return ChargebackConnectionVerification(
            spreadsheet_title=str(spreadsheet.get("properties", {}).get("title", "")),
            worksheet_title=str(worksheet.get("title", "")),
            can_append=True,
        )

    def read_values(self) -> list[list[str]]:
        range_name = quote(f"'{self.sheet_name}'!A:ZZ", safe="")
        response = self.session.get(
            f"{self._base_url}/values/{range_name}",
            params={"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"},
            timeout=30,
        )
        _raise_for_status(response, "read the Chargeback Tracker")
        return [[str(cell) for cell in row] for row in response.json().get("values", [])]

    def validate_structure(self) -> None:
        rows = self.read_values()
        if not rows:
            raise RuntimeError("Chargeback Tracker has no header row.")
        missing = [header for header in SHEET_HEADERS if header not in rows[0]]
        if missing:
            raise RuntimeError(
                "Chargeback Tracker structure is missing: " + ", ".join(missing)
            )

    def append_records(self, records: list[ChargebackRecord]) -> None:
        if not records:
            return
        rows = self.read_values()
        if not rows:
            raise RuntimeError("Chargeback Tracker has no header row.")
        headers = rows[0]
        missing = [header for header in SHEET_HEADERS if header not in headers]
        if missing:
            raise RuntimeError("Chargeback Tracker schema is missing: " + ", ".join(missing))
        values: list[list[str]] = []
        for record in records:
            mapping = record.as_mapping()
            values.append([mapping.get(header, "") for header in headers])
        end_column = column_letter(len(headers))
        range_name = quote(f"'{self.sheet_name}'!A:{end_column}", safe="")
        response = self.session.post(
            f"{self._base_url}/values/{range_name}:append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"majorDimension": "ROWS", "values": values},
            timeout=30,
        )
        _raise_for_status(response, "append chargeback records")

def column_letter(position: int) -> str:
    if position < 1:
        raise ValueError("Column position must be positive.")
    result = ""
    value = position
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _raise_for_status(response: requests.Response, action: str) -> None:
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not {action}: HTTP {response.status_code}") from exc
