from __future__ import annotations

from dataclasses import dataclass


SHEET_HEADERS = (
    "Account ID",
    "Consumer Name",
    "Collector Name",
    "Chargeback Date",
    "Amount",
    "Client Name",
    "Due Client",
    "Bonus Paid",
    "Date Recon w/ Agent",
    "Date Recon w/ Client",
    "Notes",
)


@dataclass(frozen=True)
class ChargebackRecord:
    account_id: str
    consumer_name: str
    chargeback_date: str
    amount: str
    client_name: str
    due_client: str
    notes: str = ""
    ucm_percent: str = ""

    def as_mapping(self) -> dict[str, str]:
        return {
            "Account ID": self.account_id,
            "Consumer Name": self.consumer_name,
            "Collector Name": "",
            "Chargeback Date": self.chargeback_date,
            "Amount": self.amount,
            "Client Name": self.client_name,
            "Due Client": self.due_client,
            "Bonus Paid": "",
            "Date Recon w/ Agent": "",
            "Date Recon w/ Client": "",
            "Notes": self.notes,
        }


@dataclass(frozen=True)
class ChargebackPreviewRecord:
    record_number: int
    client_name: str
    duplicate: bool
    manual_review: bool
    available_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkippedChargebackRecord:
    record_number: int
    reason: str = "refunded_or_error"


@dataclass(frozen=True)
class ParsedChargebackReport:
    records: tuple[ChargebackRecord, ...]
    record_numbers: tuple[int, ...]
    skipped_records: tuple[SkippedChargebackRecord, ...]


@dataclass(frozen=True)
class ChargebackImportResult:
    source_rows: int
    appended: int
    ndh_records: int
    icr_records: int
    duplicates: int
    manual_review: int
    skipped_refunded_error: int
    dry_run: bool
    preview_records: tuple[ChargebackPreviewRecord, ...]
    skipped_records: tuple[SkippedChargebackRecord, ...]
