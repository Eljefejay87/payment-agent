from __future__ import annotations

import re
from pathlib import Path

from agents.icr_remit_agent.parser import read_rows

from .models import ChargebackRecord, ParsedChargebackReport, SkippedChargebackRecord
from .ocr import extract_chargeback_rows


HEADER_ALIASES = {
    "Account ID": ("account id", "account number", "account #"),
    "Consumer Name": ("consumer name", "consumer", "debtor name", "customer name"),
    "Chargeback Date": ("chargeback date", "payment date", "transaction date"),
    "Amount": ("amount", "payment amount", "chargeback amount"),
    "UCM %": ("ucm %", "ucm percent", "ucm percentage"),
    "Due Client": ("due client", "ucm share", "ucm amount"),
    "Notes": ("notes", "note"),
}
REQUIRED_REVIEW_FIELDS = (
    "Account ID",
    "Consumer Name",
    "Chargeback Date",
    "Amount",
    "Due Client",
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
NOT_US_PATTERN = re.compile(r"\bNOT\s+US\b", re.IGNORECASE)
REFUNDED_OR_ERROR_PATTERNS = (
    re.compile(r"\brefunded\b", re.IGNORECASE),
    re.compile(r"\brefund(?:ing)?\s+(?:was\s+)?issued\b", re.IGNORECASE),
    re.compile(
        r"\b(?:ran|entered|processed|posted|charged|made)\s+(?:this\s+)?in\s+error\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:entry|charge|payment)\s+(?:was\s+)?(?:an?\s+)?error\b",
        re.IGNORECASE,
    ),
)


def parse_chargeback_report(
    path: Path,
    source_override: str | None = None,
) -> list[ChargebackRecord]:
    return list(parse_chargeback_report_detailed(path, source_override).records)


def parse_chargeback_report_detailed(
    path: Path,
    source_override: str | None = None,
) -> ParsedChargebackReport:
    if source_override is not None and source_override not in {"NDH", "Jim"}:
        raise ValueError("Chargeback source must be NDH or Jim.")
    if not path.is_file():
        raise ValueError(f"Chargeback report was not found: {path}")
    rows = extract_chargeback_rows(path) if path.suffix.lower() in IMAGE_SUFFIXES else read_rows(path)
    header_index, columns = _find_header(rows)
    records: list[ChargebackRecord] = []
    record_numbers: list[int] = []
    skipped_records: list[SkippedChargebackRecord] = []
    for record_number, row in enumerate(rows[header_index + 1 :], start=1):
        if not any(str(cell).strip() for cell in row):
            continue
        if _row_is_refunded_or_error(row):
            skipped_records.append(SkippedChargebackRecord(record_number=record_number))
            continue
        detected_client = "ICR" if _row_contains_not_us(row) else "NDH"
        client_name = _override_client_name(source_override) or detected_client
        values = {
            name: _remove_not_us_marker(_cell(row, index))
            for name, index in columns.items()
        }
        if not any(values.values()):
            continue
        missing = [name for name in REQUIRED_REVIEW_FIELDS if not values.get(name, "").strip()]
        incoming_notes = values.get("Notes", "").strip()
        notes = (
            incoming_notes
            if incoming_notes.startswith("Manual review required:")
            else ""
        )
        if missing:
            review_note = "Manual review required: missing " + ", ".join(missing)
            notes = f"{notes} | {review_note}" if notes else review_note
        records.append(
            ChargebackRecord(
                account_id=values.get("Account ID", ""),
                consumer_name=values.get("Consumer Name", ""),
                chargeback_date=values.get("Chargeback Date", ""),
                amount=values.get("Amount", ""),
                client_name=client_name,
                due_client=values.get("Due Client", ""),
                notes=notes,
                ucm_percent=values.get("UCM %", ""),
            )
        )
        record_numbers.append(record_number)
    return ParsedChargebackReport(
        records=tuple(records),
        record_numbers=tuple(record_numbers),
        skipped_records=tuple(skipped_records),
    )


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    for row_index, row in enumerate(rows):
        normalized = [_normalize_header(cell) for cell in row]
        columns: dict[str, int] = {}
        for canonical, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in normalized:
                    columns[canonical] = normalized.index(alias)
                    break
        if "Account ID" in columns or "Consumer Name" in columns:
            return row_index, columns
    raise ValueError("Chargeback report is missing a recognizable header row.")


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index].strip()


def _row_contains_not_us(row: list[str]) -> bool:
    return any(NOT_US_PATTERN.search(str(cell)) for cell in row)


def _row_is_refunded_or_error(row: list[str]) -> bool:
    return any(
        pattern.search(str(cell))
        for cell in row
        for pattern in REFUNDED_OR_ERROR_PATTERNS
    )


def _remove_not_us_marker(value: str) -> str:
    if not NOT_US_PATTERN.search(value):
        return value
    return " ".join(NOT_US_PATTERN.sub("", value).split())


def _override_client_name(source_override: str | None) -> str | None:
    if source_override == "Jim":
        return "ICR"
    return source_override
