from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .google_sheets import ChargebackSheet
from .models import (
    ChargebackImportResult,
    ChargebackPreviewRecord,
    ChargebackRecord,
)
from .parser import parse_chargeback_report_detailed

LOGGER = logging.getLogger(__name__)

LEGACY_HEADER_ALIASES = {
    "Account Number": ("Account Number", "Account ID"),
    "Payment Date": ("Payment Date", "Chargeback Date"),
    "Payment Amount": ("Payment Amount", "Amount"),
}


class ChargebackImportService:
    def __init__(self, sheet: ChargebackSheet) -> None:
        self.sheet = sheet

    def import_report(
        self,
        path: Path,
        source_override: str | None = None,
        apply: bool = False,
    ) -> ChargebackImportResult:
        parsed = parse_chargeback_report_detailed(path, source_override)
        records = parsed.records
        existing_rows = self.sheet.read_values()
        existing_keys = existing_duplicate_keys(existing_rows)
        accepted: list[ChargebackRecord] = []
        preview_records: list[ChargebackPreviewRecord] = []
        duplicate_count = 0
        for record_number, record in zip(parsed.record_numbers, records):
            key = duplicate_key(
                record.account_id,
                record.chargeback_date,
                record.amount,
            )
            is_duplicate = key is not None and key in existing_keys
            needs_manual_review = "Manual review required:" in record.notes
            available_fields = tuple(
                name
                for name, value in (
                    ("Account ID", record.account_id),
                    ("Consumer Name", record.consumer_name),
                    ("Chargeback Date", record.chargeback_date),
                    ("Amount", record.amount),
                    ("Due Client", record.due_client),
                )
                if value.strip()
            )
            preview_records.append(
                ChargebackPreviewRecord(
                    record_number=record_number,
                    client_name=record.client_name,
                    duplicate=is_duplicate,
                    manual_review=needs_manual_review,
                    available_fields=available_fields,
                )
            )
            if needs_manual_review:
                continue
            if is_duplicate:
                duplicate_count += 1
                continue
            accepted.append(record)
            if key is not None:
                existing_keys.add(key)
        if apply and accepted:
            self.sheet.validate_structure()
            self.sheet.append_records(accepted)
        ndh_records = sum(record.client_name == "NDH" for record in records)
        icr_records = sum(record.client_name == "ICR" for record in records)
        manual_review = sum(item.manual_review for item in preview_records)
        LOGGER.info(
            "Chargeback import source_override=%s rows=%s ndh=%s icr=%s append=%s "
            "duplicates=%s manual_review=%s skipped_refunded_error=%s dry_run=%s",
            source_override or "row-detection",
            len(records),
            ndh_records,
            icr_records,
            len(accepted),
            duplicate_count,
            manual_review,
            len(parsed.skipped_records),
            not apply,
        )
        return ChargebackImportResult(
            source_rows=len(records) + len(parsed.skipped_records),
            appended=len(accepted),
            ndh_records=ndh_records,
            icr_records=icr_records,
            duplicates=duplicate_count,
            manual_review=manual_review,
            skipped_refunded_error=len(parsed.skipped_records),
            dry_run=not apply,
            preview_records=tuple(preview_records),
            skipped_records=parsed.skipped_records,
        )


def existing_duplicate_keys(rows: list[list[str]]) -> set[tuple[str, str, str]]:
    if not rows:
        return set()
    headers = rows[0]
    indexes = {
        name: _header_indexes(headers, aliases)
        for name, aliases in LEGACY_HEADER_ALIASES.items()
    }
    keys: set[tuple[str, str, str]] = set()
    for row in rows[1:]:
        key = duplicate_key(
            _row_first_value(row, indexes["Account Number"]),
            _row_first_value(row, indexes["Payment Date"]),
            _row_first_value(row, indexes["Payment Amount"]),
        )
        if key is not None:
            keys.add(key)
    return keys


def duplicate_key(account_number: str, payment_date: str, payment_amount: str) -> tuple[str, str, str] | None:
    if not account_number.strip() or not payment_date.strip() or not payment_amount.strip():
        return None
    return (
        re.sub(r"\s+", "", account_number).casefold(),
        _normalize_date(payment_date),
        _normalize_amount(payment_amount),
    )


def build_append_row(record: ChargebackRecord, headers: list[str]) -> list[str]:
    mapping = record.as_mapping()
    return [mapping.get(header, "") for header in headers]


def _header_indexes(headers: list[str], aliases: tuple[str, ...]) -> list[int]:
    normalized = {header.strip().casefold(): index for index, header in enumerate(headers)}
    return [normalized[alias.casefold()] for alias in aliases if alias.casefold() in normalized]


def _row_first_value(row: list[str], indexes: list[int]) -> str:
    for index in indexes:
        if index < len(row) and str(row[index]).strip():
            return str(row[index])
    return ""


def _normalize_date(value: str) -> str:
    cleaned = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            continue
    return cleaned.casefold()


def _normalize_amount(value: str) -> str:
    cleaned = value.strip().replace("$", "").replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return format(Decimal(cleaned).normalize(), "f")
    except InvalidOperation:
        return cleaned.casefold()
