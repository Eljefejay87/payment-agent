from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


OCR_HEADERS = (
    "Account ID",
    "Consumer Name",
    "Chargeback Date",
    "Amount",
    "UCM %",
    "Due Client",
    "Notes",
    "Record Text",
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
TABLE_ROW_PATTERN = re.compile(
    r"^\s*(?P<prefix>.+?)\s+"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
    r"(?P<amount>\(?-?\$?[\d,]+(?:\.\d{2})?\)?)\s+"
    r"(?P<ucm_percent>\d+(?:\.\d+)?%?)\s+"
    r"(?P<due_client>\(?-?\$?[\d,]+(?:\.\d{2})?\)?)"
    r"(?:\s+(?P<trailing>.*\S))?\s*$",
    re.IGNORECASE,
)
PARTIAL_TABLE_ROW_PATTERN = re.compile(
    r"^\s*(?P<prefix>.+?)\s+"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
    r"(?P<amount>\(?-?\$?[\d,]+(?:\.\d{2})?\)?)"
    r"(?:\s+(?P<ucm_percent>\d+(?:\.\d+)?%?))?"
    r"(?:\s+(?P<due_client>\(?-?\$?[\d,]+(?:\.\d{2})?\)?))?"
    r"(?:\s+(?P<trailing>.*\S))?\s*$",
    re.IGNORECASE,
)
NOT_US_PATTERN = re.compile(r"\bNOT\s+US\b", re.IGNORECASE)


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float


def extract_chargeback_rows(path: Path) -> list[list[str]]:
    """Extract legacy Chargeback Tracker rows from a screenshot using Tesseract."""
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Chargeback screenshot import supports .png, .jpg, and .jpeg files.")
    command = _ocr_command()
    if not shutil.which(command) and not Path(command).exists():
        raise RuntimeError(
            "Chargeback screenshot OCR requires Tesseract. Set CHARGEBACK_OCR_COMMAND "
            "to the installed executable."
        )
    try:
        result = subprocess.run(
            [command, str(path), "stdout", "--psm", "6", "tsv"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Chargeback screenshot OCR failed: {exc}") from exc
    return parse_ocr_lines(
        parse_tesseract_lines(result.stdout),
        min_confidence=_minimum_confidence(),
    )


def parse_tesseract_lines(tsv_text: str) -> list[OCRLine]:
    grouped: dict[tuple[str, str, str, str], list[tuple[str, float]]] = {}
    reader = csv.DictReader(tsv_text.splitlines(), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or "-1") / 100
        except ValueError:
            continue
        if confidence < 0:
            continue
        key = (
            row.get("page_num", ""),
            row.get("block_num", ""),
            row.get("par_num", ""),
            row.get("line_num", ""),
        )
        grouped.setdefault(key, []).append((text, confidence))
    return [
        OCRLine(
            text=" ".join(word for word, _ in words),
            confidence=sum(score for _, score in words) / len(words),
        )
        for words in grouped.values()
    ]


def parse_ocr_lines(
    lines: list[OCRLine],
    min_confidence: float = 0.80,
) -> list[list[str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    review_notes: list[str] = []
    inherited_account_id = ""

    for line in lines:
        table_row = _match_unlabeled_table_row(line.text)
        if table_row is not None:
            _finish_record(records, current, review_notes)
            current, review_notes = {}, []
            detected_account_id = table_row["Account ID"]
            if detected_account_id:
                inherited_account_id = detected_account_id
            table_row["Account ID"] = detected_account_id or inherited_account_id
            for field in (
                "Account ID",
                "Consumer Name",
                "Chargeback Date",
                "Amount",
                "UCM %",
                "Due Client",
            ):
                _set_ocr_value(
                    current,
                    review_notes,
                    field,
                    table_row[field],
                    line.confidence,
                    min_confidence,
                )
            if line.confidence >= min_confidence:
                current["Record Text"] = table_row["Record Text"]
            continue

        matched = _match_field(line.text)
        if matched is None:
            if current and line.confidence >= min_confidence:
                existing = current.get("Record Text", "")
                current["Record Text"] = " ".join(
                    part for part in (existing, line.text.strip()) if part
                )
            continue
        field, value = matched
        if field in {"Account ID", "Account Information"} and current:
            _finish_record(records, current, review_notes)
            current, review_notes = {}, []
        if field == "Chargeback Date" and current.get("Chargeback Date"):
            carried = {
                "Account ID": current.get("Account ID", ""),
                "Consumer Name": current.get("Consumer Name", ""),
            }
            _finish_record(records, current, review_notes)
            current, review_notes = carried, []

        if field == "Account Information":
            account_id, consumer_name = _split_account_information(value)
            _set_ocr_value(
                current,
                review_notes,
                "Account ID",
                account_id,
                line.confidence,
                min_confidence,
            )
            _set_ocr_value(
                current,
                review_notes,
                "Consumer Name",
                consumer_name,
                line.confidence,
                min_confidence,
            )
            continue
        _set_ocr_value(
            current,
            review_notes,
            field,
            value,
            line.confidence,
            min_confidence,
        )

    _finish_record(records, current, review_notes)
    rows = [list(OCR_HEADERS)]
    rows.extend([[record.get(header, "") for header in OCR_HEADERS] for record in records])
    return rows


def _match_field(text: str) -> tuple[str, str] | None:
    patterns = (
        ("Account Information", r"account\s+information"),
        ("Account ID", r"account\s+(?:id|number|#)"),
        ("Consumer Name", r"consumer(?:\s+name)?"),
        ("Chargeback Date", r"chargeback\s+date"),
        ("UCM %", r"ucm\s+(?:%|percent|percentage)"),
        ("Due Client", r"due\s+client"),
        ("Amount", r"(?:chargeback\s+)?amount"),
    )
    for field, label in patterns:
        match = re.match(rf"^\s*{label}\s*:?\s+(.+?)\s*$", text, re.IGNORECASE)
        if match:
            return field, match.group(1).strip()
    return None


def _match_unlabeled_table_row(text: str) -> dict[str, str] | None:
    match = TABLE_ROW_PATTERN.match(text) or PARTIAL_TABLE_ROW_PATTERN.match(text)
    if match is None:
        return None
    account_id, consumer_name = _split_table_prefix(match.group("prefix"))
    return {
        "Account ID": account_id,
        "Consumer Name": consumer_name,
        "Chargeback Date": match.group("date"),
        "Amount": match.group("amount"),
        "UCM %": match.group("ucm_percent") or "",
        "Due Client": match.group("due_client") or "",
        "Record Text": text.strip(),
    }


def _split_table_prefix(prefix: str) -> tuple[str, str]:
    parts = prefix.split()
    if not parts or not any(character.isdigit() for character in parts[0]):
        return "", prefix.strip()
    account_id = parts[0]
    consumer_name = " ".join(parts[1:]).strip()
    if NOT_US_PATTERN.search(prefix):
        account_id = f"{account_id} NOT US"
        consumer_name = " ".join(NOT_US_PATTERN.sub("", consumer_name).split())
    return account_id, consumer_name


def _split_account_information(value: str) -> tuple[str, str]:
    separated = re.split(r"\s+(?:-|\|)\s+", value, maxsplit=1)
    if len(separated) == 2:
        return separated[0].strip(), separated[1].strip()
    parts = value.split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) == 2 else "")


def _set_ocr_value(
    current: dict[str, str],
    review_notes: list[str],
    field: str,
    value: str,
    confidence: float,
    min_confidence: float,
) -> None:
    if confidence < min_confidence:
        current[field] = ""
        review_notes.append(f"low-confidence {field}")
        return
    current[field] = value.strip()


def _finish_record(
    records: list[dict[str, str]],
    current: dict[str, str],
    review_notes: list[str],
) -> None:
    if not current and not review_notes:
        return
    record = dict(current)
    if review_notes:
        record["Notes"] = "Manual review required: " + ", ".join(review_notes)
    records.append(record)


def _ocr_command() -> str:
    configured = os.getenv("CHARGEBACK_OCR_COMMAND") or os.getenv("OPS_OCR_COMMAND")
    if configured:
        return configured
    for command in ("tesseract", "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if shutil.which(command) or Path(command).exists():
            return command
    return "tesseract"


def _minimum_confidence() -> float:
    configured = os.getenv("CHARGEBACK_OCR_MIN_CONFIDENCE", "0.80")
    try:
        value = float(configured)
    except ValueError as exc:
        raise ValueError("CHARGEBACK_OCR_MIN_CONFIDENCE must be a number.") from exc
    if not 0 <= value <= 1:
        raise ValueError("CHARGEBACK_OCR_MIN_CONFIDENCE must be between 0 and 1.")
    return value
