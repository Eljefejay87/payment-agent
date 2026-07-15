from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from shared.logging import configure_logging

from .config import load_chargeback_settings, validate_chargeback_settings
from .google_sheets import GoogleChargebackSheet
from .models import ChargebackImportResult
from .service import ChargebackImportService


def log_import_result(result: ChargebackImportResult) -> None:
    """Log a non-sensitive per-record preview and aggregate import counts."""
    if result.dry_run:
        for record in result.preview_records:
            logging.info(
                "Chargeback preview record=%s client_name=%s duplicate=%s manual_review=%s available_fields=%s",
                record.record_number,
                record.client_name,
                record.duplicate,
                record.manual_review,
                record.available_fields,
            )
        for record in result.skipped_records:
            logging.info(
                "Chargeback preview record=%s skipped=%s",
                record.record_number,
                record.reason,
            )
    logging.info(
        "Chargeback result: source_rows=%s NDH=%s ICR=%s append=%s "
        "duplicates=%s manual_review=%s refunded_error_skipped=%s dry_run=%s",
        result.source_rows,
        result.ndh_records,
        result.icr_records,
        result.appended,
        result.duplicates,
        result.manual_review,
        result.skipped_refunded_error,
        result.dry_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import into the existing United Charge Back Tracker")
    parser.add_argument(
        "command",
        choices=["chargeback-import", "chargeback-verify-connection"],
    )
    parser.add_argument("--file", help="Path to a chargeback .csv or .xlsx report.")
    parser.add_argument("--source", choices=["NDH", "Jim"])
    parser.add_argument("--apply", action="store_true", help="Append new records to the existing Google Sheet.")
    parser.add_argument("--confirm", default="", help="Required confirmation phrase for --apply.")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()

    settings = load_chargeback_settings(args.env_file)
    configure_logging(settings.log_level)
    errors = validate_chargeback_settings(settings, require_credentials=True)
    if args.command == "chargeback-import" and not args.file:
        errors.append("--file is required for chargeback-import.")
    if args.apply and args.confirm != "APPEND_CHARGEBACKS":
        errors.append("--apply requires --confirm APPEND_CHARGEBACKS.")
    if errors:
        for error in errors:
            logging.error(error)
        return 2
    assert settings.service_account_file is not None
    sheet = GoogleChargebackSheet(
        settings.spreadsheet_id,
        settings.sheet_name,
        settings.service_account_file,
    )
    if args.command == "chargeback-verify-connection":
        try:
            verification = sheet.verify_connection()
        except RuntimeError as exc:
            logging.error("Chargeback Tracker connection verification failed: %s", exc)
            return 1
        logging.info(
            "Chargeback Tracker connection verified: spreadsheet=%s worksheet=%s can_append=%s",
            verification.spreadsheet_title,
            verification.worksheet_title,
            verification.can_append,
        )
        return 0

    assert args.file is not None
    result = ChargebackImportService(sheet).import_report(
        Path(args.file),
        args.source,
        apply=args.apply,
    )
    log_import_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
