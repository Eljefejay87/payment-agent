from __future__ import annotations

import csv
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

from .models import ICRRemitResult


REQUIRED_HEADERS = {
    "due_to_agency": "AgencyFee",
    "due_to_client": "ClientFee",
}


def parse_icr_remit_file(path: Path, broker: str = "ICR", contact: str = "Jim", today: date | None = None) -> ICRRemitResult:
    rows = read_rows(path)
    agency_index, client_index = find_required_columns(rows)
    due_to_agency = sum_decimal_column(rows, agency_index)
    due_to_client = sum_decimal_column(rows, client_index)
    remit_week = monday_of_week(today or date.today())
    return ICRRemitResult(
        broker=broker,
        contact=contact,
        remit_week=remit_week,
        week_ending=remit_week + timedelta(days=6),
        file_path=path,
        due_to_agency=due_to_agency,
        due_to_client=due_to_client,
        total_collected=due_to_agency + due_to_client,
        notes="Weekly ICR remit owed to Jim",
    )


def read_rows(path: Path) -> list[list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [[cell.strip() for cell in row] for row in csv.reader(handle)]
    if suffix == ".xlsx":
        return read_xlsx_rows(path)
    raise ValueError("ICR remit import supports .xlsx and .csv files.")


def read_xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        sheet_name = next(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        root = ElementTree.fromstring(archive.read(sheet_name))
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", namespace):
        values: dict[int, str] = {}
        for cell in row.findall("x:c", namespace):
            index = column_index(cell.get("r", "A1"))
            cell_type = cell.get("t")
            value = cell.find("x:v", namespace)
            inline = cell.find("x:is/x:t", namespace)
            if cell_type == "s" and value is not None:
                values[index] = shared_strings[int(value.text or "0")]
            elif inline is not None:
                values[index] = inline.text or ""
            elif value is not None:
                values[index] = value.text or ""
            else:
                values[index] = ""
        if values:
            rows.append([values.get(i, "") for i in range(max(values) + 1)])
    return rows


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return ["".join(text.text or "" for text in item.findall(".//x:t", namespace)) for item in root.findall("x:si", namespace)]


def column_index(reference: str) -> int:
    letters = re.match(r"([A-Z]+)", reference.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(1):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def find_required_columns(rows: list[list[str]]) -> tuple[int, int]:
    for row in rows:
        normalized = [cell.strip().lower() for cell in row]
        try:
            return (
                normalized.index(REQUIRED_HEADERS["due_to_agency"].lower()),
                normalized.index(REQUIRED_HEADERS["due_to_client"].lower()),
            )
        except ValueError:
            continue
    raise ValueError("ICR remit file is missing AgencyFee or ClientFee headers.")


def sum_decimal_column(rows: list[list[str]], index: int) -> Decimal:
    total = Decimal("0")
    for row in rows:
        if index >= len(row):
            continue
        value = parse_decimal(row[index])
        if value is not None:
            total += value
    return total.quantize(Decimal("0.01"))


def parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def monday_of_week(value: date) -> date:
    return value - timedelta(days=value.weekday())
