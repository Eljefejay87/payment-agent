from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from agents.chargeback_tracker.config import (
    ChargebackSettings,
    PRODUCTION_SHEET_NAME,
    PRODUCTION_SPREADSHEET_ID,
    validate_chargeback_settings,
)
from agents.chargeback_tracker.google_sheets import GoogleChargebackSheet
from agents.chargeback_tracker.main import log_import_result
from agents.chargeback_tracker.models import SHEET_HEADERS, ChargebackRecord
from agents.chargeback_tracker.ocr import OCRLine, parse_ocr_lines
from agents.chargeback_tracker.parser import parse_chargeback_report
from agents.chargeback_tracker.service import (
    ChargebackImportService,
    build_append_row,
    duplicate_key,
)


LEGACY_HEADERS = [
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
]


class FakeChargebackSheet:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = [list(row) for row in rows]
        self.validation_calls = 0
        self.appended: list[ChargebackRecord] = []

    def read_values(self) -> list[list[str]]:
        return [list(row) for row in self.rows]

    def validate_structure(self) -> None:
        self.validation_calls += 1
        missing = [header for header in SHEET_HEADERS if header not in self.rows[0]]
        if missing:
            raise RuntimeError("missing structure")

    def append_records(self, records: list[ChargebackRecord]) -> None:
        self.appended.extend(records)
        self.rows.extend(build_append_row(record, self.rows[0]) for record in records)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class ReadOnlyVerificationSession:
    def __init__(self, can_edit: bool = True) -> None:
        self.can_edit = can_edit
        self.get_calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None, timeout: int = 30) -> FakeResponse:
        self.get_calls.append((url, params))
        if "/values/" in url:
            return FakeResponse({"values": [LEGACY_HEADERS]})
        if "sheets.googleapis.com" in url:
            return FakeResponse(
                {
                    "spreadsheetId": PRODUCTION_SPREADSHEET_ID,
                    "properties": {"title": "United Charge Back Tracker"},
                    "sheets": [{"properties": {"title": PRODUCTION_SHEET_NAME}}],
                }
            )
        return FakeResponse(
            {
                "id": PRODUCTION_SPREADSHEET_ID,
                "name": "United Charge Back Tracker",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "trashed": False,
                "capabilities": {
                    "canEdit": self.can_edit,
                    "canModifyContent": self.can_edit,
                },
            }
        )

    def post(self, *args, **kwargs):
        raise AssertionError("Connection verification must not write data.")

    def put(self, *args, **kwargs):
        raise AssertionError("Connection verification must not write data.")


class ChargebackParserTests(unittest.TestCase):
    def _parse(
        self,
        rows: str,
        source_override: str | None = None,
    ) -> list[ChargebackRecord]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "chargebacks.csv"
            path.write_text(
                "Account ID,Consumer Name,Chargeback Date,Amount,Due Client,Notes\n"
                + rows
            )
            return parse_chargeback_report(path, source_override)

    def test_normal_record_defaults_to_ndh(self) -> None:
        record = self._parse(
            "A-1,Person One,7/10/2026,100.00,38.00,normal\n"
        )[0]

        self.assertEqual(record.client_name, "NDH")

    def test_not_us_record_is_classified_as_jim(self) -> None:
        record = self._parse(
            "A-2 NOT US,Person Two,7/11/2026,100.00,38.00,review\n"
        )[0]

        self.assertEqual(record.client_name, "ICR")

    def test_mixed_report_classifies_each_record_independently(self) -> None:
        records = self._parse(
            "A-1,Person One,7/10/2026,100.00,38.00,normal\n"
            "A-2 NOT US,Person Two,7/11/2026,200.00,76.00,review\n"
        )

        self.assertEqual([record.client_name for record in records], ["NDH", "ICR"])

    def test_not_us_matching_is_case_insensitive(self) -> None:
        record = self._parse(
            "A-3 not us,Person Three,7/12/2026,100.00,38.00,review\n"
        )[0]

        self.assertEqual(record.client_name, "ICR")

    def test_not_us_matching_tolerates_extra_spaces(self) -> None:
        record = self._parse(
            "A-4   NOT     US   ,Person Four,7/13/2026,100.00,38.00,review\n"
        )[0]

        self.assertEqual(record.client_name, "ICR")
        self.assertEqual(record.account_id, "A-4")

    def test_not_us_marker_is_excluded_from_all_imported_fields(self) -> None:
        record = self._parse(
            "A-5 NOT US,Person NOT US Five,7/14/2026,100.00,38.00,"
            "Review NOT US\n"
        )[0]

        self.assertEqual(record.account_id, "A-5")
        self.assertEqual(record.consumer_name, "Person Five")
        self.assertEqual(record.notes, "")
        self.assertNotIn("NOT US", " ".join(record.as_mapping().values()).upper())

    def test_explicit_source_override_replaces_row_detection(self) -> None:
        record = self._parse(
            "A-6 NOT US,Person Six,7/15/2026,100.00,38.00,review\n",
            source_override="NDH",
        )[0]

        self.assertEqual(record.client_name, "NDH")
        self.assertEqual(record.account_id, "A-6")

    def test_preserves_report_money_text_and_does_not_calculate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ndh.csv"
            path.write_text(
                'Account ID,Consumer Name,Chargeback Date,Amount,Due Client,Notes\n'
                'A-1,Person One,7/10/2026,"$1,200.00","$456.00",reported\n'
            )

            record = parse_chargeback_report(path)[0]

            self.assertEqual(record.amount, "$1,200.00")
            self.assertEqual(record.due_client, "$456.00")
            self.assertEqual(record.client_name, "NDH")

    def test_missing_information_is_flagged_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jim.csv"
            path.write_text(
                "Account ID,Consumer Name,Chargeback Date,Amount,Due Client\n"
                "A-2,Person Two,,100.00,\n"
            )

            record = parse_chargeback_report(path)[0]

            self.assertEqual(record.chargeback_date, "")
            self.assertEqual(record.due_client, "")
            self.assertIn(
                "Manual review required: missing Chargeback Date, Due Client",
                record.notes,
            )

    def test_screenshot_formats_can_produce_multiple_chargeback_rows(self) -> None:
        extracted = [
            ["Account ID", "Consumer Name", "Chargeback Date", "Amount", "Due Client"],
            ["A-7", "Same Consumer", "7/1/2026", "$100.00", "$62.00"],
            ["A-7", "Same Consumer", "7/15/2026", "$100.00", "$62.00"],
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            for suffix in (".png", ".jpg", ".jpeg"):
                path = Path(temp_dir) / f"chargebacks{suffix}"
                path.write_bytes(b"not-read-in-unit-test")
                with patch(
                    "agents.chargeback_tracker.parser.extract_chargeback_rows",
                    return_value=extracted,
                ):
                    records = parse_chargeback_report(path)

                self.assertEqual(len(records), 2)
                self.assertEqual(
                    [record.chargeback_date for record in records],
                    ["7/1/2026", "7/15/2026"],
                )
                self.assertEqual(
                    [record.consumer_name for record in records],
                    ["Same Consumer", "Same Consumer"],
                )

    def test_low_confidence_ocr_value_is_blank_and_flagged(self) -> None:
        rows = parse_ocr_lines(
            [
                OCRLine("Account ID A-8", 0.95),
                OCRLine("Consumer Name Person Eight", 0.95),
                OCRLine("Chargeback Date 7/20/2026", 0.40),
                OCRLine("Amount $100.00", 0.95),
                OCRLine("Due Client $62.00", 0.95),
            ]
        )

        self.assertEqual(rows[1][2], "")
        notes_index = rows[0].index("Notes")
        self.assertIn("low-confidence Chargeback Date", rows[1][notes_index])

    def test_unlabeled_table_rows_inherit_account_and_preserve_values(self) -> None:
        rows = parse_ocr_lines(
            [
                OCRLine("UNITED", 0.96),
                OCRLine("4478575 KRYSTIN MCINTOSH 3/25/26 $64.04 30 $44.82", 0.89),
                OCRLine("KRYSTIN MCINTOSH 4/9/26 $64.04 30 $44.82", 0.88),
                OCRLine("KRYSTIN MCINTOSH 4/24/26 $64.04 30 $44.82", 0.88),
            ]
        )

        account_index = rows[0].index("Account ID")
        date_index = rows[0].index("Chargeback Date")
        percent_index = rows[0].index("UCM %")
        self.assertEqual([row[account_index] for row in rows[1:]], ["4478575"] * 3)
        self.assertEqual(
            [row[date_index] for row in rows[1:]],
            ["3/25/26", "4/9/26", "4/24/26"],
        )
        self.assertEqual([row[percent_index] for row in rows[1:]], ["30"] * 3)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partner.png"
            path.write_bytes(b"not-read-in-unit-test")
            with patch(
                "agents.chargeback_tracker.parser.extract_chargeback_rows",
                return_value=rows,
            ):
                records = parse_chargeback_report(path)

        self.assertEqual(len(records), 3)
        self.assertEqual([record.client_name for record in records], ["NDH"] * 3)
        self.assertEqual([record.amount for record in records], ["$64.04"] * 3)
        self.assertEqual([record.ucm_percent for record in records], ["30"] * 3)
        self.assertEqual([record.due_client for record in records], ["$44.82"] * 3)
        self.assertNotIn("UCM %", records[0].as_mapping())

    def test_partial_unlabeled_row_is_retained_for_manual_review(self) -> None:
        rows = parse_ocr_lines(
            [
                OCRLine("4478575 NOT US KRYSTIN MCINTOSH 3/25/26 $64.04 30", 0.89),
            ]
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "4478575 NOT US")
        self.assertEqual(rows[1][1], "KRYSTIN MCINTOSH")
        self.assertEqual(rows[1][2], "3/25/26")
        self.assertEqual(rows[1][3], "$64.04")
        self.assertEqual(rows[1][5], "")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partial.png"
            path.write_bytes(b"not-read-in-unit-test")
            with patch(
                "agents.chargeback_tracker.parser.extract_chargeback_rows",
                return_value=rows,
            ):
                record = parse_chargeback_report(path)[0]

        self.assertEqual(record.client_name, "ICR")
        self.assertEqual(record.account_id, "4478575")
        self.assertEqual(record.due_client, "")
        self.assertIn("Manual review required: missing Due Client", record.notes)

    def test_unlabeled_not_us_marker_is_inherited_and_removed(self) -> None:
        rows = parse_ocr_lines(
            [
                OCRLine(
                    "4478575 NOT US KRYSTIN MCINTOSH 3/25/26 $64.04 30 $44.82",
                    0.95,
                ),
                OCRLine("KRYSTIN MCINTOSH 4/9/26 $64.04 30 $44.82", 0.95),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partner.png"
            path.write_bytes(b"not-read-in-unit-test")
            with patch(
                "agents.chargeback_tracker.parser.extract_chargeback_rows",
                return_value=rows,
            ):
                records = parse_chargeback_report(path)

        self.assertEqual([record.client_name for record in records], ["ICR", "ICR"])
        self.assertEqual([record.account_id for record in records], ["4478575"] * 2)
        self.assertNotIn(
            "NOT US",
            " ".join(value for record in records for value in record.as_mapping().values()).upper(),
        )

    def test_unlabeled_refunded_and_error_rows_are_skipped(self) -> None:
        rows = parse_ocr_lines(
            [
                OCRLine("4478575 KRYSTIN MCINTOSH 3/25/26 $64.04 30 $44.82", 0.95),
                OCRLine("KRYSTIN MCINTOSH 4/9/26 $64.04 30 $44.82 refunded", 0.95),
                OCRLine(
                    "KRYSTIN MCINTOSH 4/24/26 $64.04 30 $44.82 ran in error",
                    0.95,
                ),
                OCRLine("KRYSTIN MCINTOSH 5/9/26 $64.04 30 $44.82", 0.95),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partner.png"
            path.write_bytes(b"not-read-in-unit-test")
            with patch(
                "agents.chargeback_tracker.parser.extract_chargeback_rows",
                return_value=rows,
            ):
                records = parse_chargeback_report(path)

        self.assertEqual(
            [record.chargeback_date for record in records],
            ["3/25/26", "5/9/26"],
        )

    def test_refunded_and_error_records_are_skipped_from_mixed_report(self) -> None:
        records = self._parse(
            "A-20,Valid One,7/1/2026,100.00,62.00,valid\n"
            "A-21,Person Twenty One,7/2/2026,200.00,124.00,refunded\n"
            "A-22,Error One,7/3/2026,300.00,186.00,ran in error\n"
            "A-23 NOT US,Valid Two,7/4/2026,400.00,248.00,valid\n"
        )

        self.assertEqual([record.account_id for record in records], ["A-20", "A-23"])
        self.assertEqual([record.client_name for record in records], ["NDH", "ICR"])

    def test_ocr_preserves_refund_wording_as_transient_record_text(self) -> None:
        rows = parse_ocr_lines(
            [
                OCRLine("Account ID A-21", 0.95),
                OCRLine("Consumer Name Person Twenty One", 0.95),
                OCRLine("Chargeback Date 7/2/2026", 0.95),
                OCRLine("Amount $200.00", 0.95),
                OCRLine("Due Client $124.00", 0.95),
                OCRLine("Payment was refunded", 0.95),
            ]
        )

        self.assertEqual(rows[0][-1], "Record Text")
        self.assertEqual(rows[1][-1], "Payment was refunded")

    def test_existing_xlsx_import_still_uses_legacy_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "chargebacks.xlsx"
            write_chargeback_xlsx(path)

            record = parse_chargeback_report(path)[0]

        self.assertEqual(record.account_id, "A-9")
        self.assertEqual(record.client_name, "NDH")
        self.assertEqual(record.amount, "$125.00")
        self.assertEqual(record.due_client, "$77.50")


class ChargebackImportTests(unittest.TestCase):
    def _report(self, folder: str, rows: str) -> Path:
        path = Path(folder) / "report.csv"
        path.write_text(
            "Account ID,Consumer Name,Chargeback Date,Amount,Due Client\n" + rows
        )
        return path

    def test_duplicate_key_normalizes_only_required_match_fields(self) -> None:
        self.assertEqual(
            duplicate_key(" A-1 ", "07/10/2026", "$1,200.00"),
            duplicate_key("A-1", "2026-07-10", "1200"),
        )

    def test_existing_legacy_row_and_same_report_duplicate_are_skipped(self) -> None:
        legacy_row = ["A-1", "Old Name", "", "7/10/2026", "$100.00", "NDH"]
        existing = [LEGACY_HEADERS, legacy_row]
        sheet = FakeChargebackSheet(existing)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._report(
                temp_dir,
                "A-1,Person One,2026-07-10,100,38\n"
                "A-2,Person Two,2026-07-11,50.00,19.00\n"
                "A-2,Person Two,7/11/2026,$50,$19\n",
            )

            result = ChargebackImportService(sheet).import_report(path, "NDH", apply=True)

            self.assertEqual(result.appended, 1)
            self.assertEqual(result.duplicates, 2)
            self.assertEqual([record.account_id for record in sheet.appended], ["A-2"])

    def test_dry_run_has_no_sheet_writes(self) -> None:
        sheet = FakeChargebackSheet([LEGACY_HEADERS])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._report(temp_dir, "A-3,Person Three,2026-07-12,75.00,28.50\n")

            result = ChargebackImportService(sheet).import_report(path, "Jim", apply=False)

            self.assertTrue(result.dry_run)
            self.assertEqual(result.appended, 1)
            self.assertEqual(sheet.validation_calls, 0)
            self.assertEqual(sheet.appended, [])

    def test_preview_has_per_record_sources_and_requested_totals(self) -> None:
        legacy_row = ["A-1", "Old Name", "", "7/10/2026", "100.00", "NDH"]
        sheet = FakeChargebackSheet([LEGACY_HEADERS, legacy_row])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._report(
                temp_dir,
                "A-1,Person One,7/10/2026,100.00,38.00\n"
                "A-2 NOT US,Person Two,7/11/2026,50.00,\n",
            )

            result = ChargebackImportService(sheet).import_report(path, apply=False)

        with self.assertLogs(level="INFO") as logs:
            log_import_result(result)

        self.assertEqual(result.ndh_records, 1)
        self.assertEqual(result.icr_records, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.manual_review, 1)
        self.assertEqual(
            [record.client_name for record in result.preview_records],
            ["NDH", "ICR"],
        )
        self.assertEqual(sheet.validation_calls, 0)
        self.assertEqual(sheet.appended, [])
        output = "\n".join(logs.output)
        self.assertIn("record=1 client_name=NDH", output)
        self.assertIn("record=2 client_name=ICR", output)
        self.assertIn("NDH=1 ICR=1", output)
        self.assertIn("duplicates=1 manual_review=1", output)

    def test_structure_validation_preserves_existing_headers(self) -> None:
        sheet = FakeChargebackSheet([LEGACY_HEADERS])
        original = list(sheet.rows[0])

        sheet.validate_structure()

        self.assertEqual(sheet.rows[0], original)

    def test_apply_maps_only_legacy_fields_and_leaves_manual_columns_blank(self) -> None:
        sheet = FakeChargebackSheet([LEGACY_HEADERS])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._report(temp_dir, "A-10,Person Ten,7/25/2026,$100.00,$62.00\n")

            ChargebackImportService(sheet).import_report(path, apply=True)

        appended = sheet.rows[1]
        self.assertEqual(appended[0], "A-10")
        self.assertEqual(appended[1], "Person Ten")
        self.assertEqual(appended[2], "")
        self.assertEqual(appended[3], "7/25/2026")
        self.assertEqual(appended[4], "$100.00")
        self.assertEqual(appended[5], "NDH")
        self.assertEqual(appended[6], "$62.00")
        self.assertEqual(appended[7:10], ["", "", ""])

    def test_apply_skips_incomplete_records_but_keeps_valid_records(self) -> None:
        sheet = FakeChargebackSheet([LEGACY_HEADERS])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._report(
                temp_dir,
                "A-30,Valid Person,7/25/2026,$100.00,$62.00\n"
                "A-31 NOT US,Partial Person,7/26/2026,$200.00,\n",
            )

            result = ChargebackImportService(sheet).import_report(path, apply=True)

        self.assertEqual(result.appended, 1)
        self.assertEqual(result.manual_review, 1)
        self.assertEqual([record.account_id for record in sheet.appended], ["A-30"])
        manual = next(record for record in result.preview_records if record.manual_review)
        self.assertEqual(manual.client_name, "ICR")
        self.assertEqual(
            manual.available_fields,
            ("Account ID", "Consumer Name", "Chargeback Date", "Amount"),
        )

    def test_mixed_screenshot_skips_refunds_and_appends_remaining_records(self) -> None:
        extracted = [
            [
                "Account ID",
                "Consumer Name",
                "Chargeback Date",
                "Amount",
                "Due Client",
                "Record Text",
            ],
            ["A-20", "Valid One", "7/1/2026", "$100.00", "$62.00", ""],
            ["A-21", "Person Twenty One", "7/2/2026", "$200.00", "$124.00", "refunded"],
            ["A-22", "Error One", "7/3/2026", "$300.00", "$186.00", "ran in error"],
            ["A-23 NOT US", "Valid Two", "7/4/2026", "$400.00", "$248.00", ""],
        ]
        sheet = FakeChargebackSheet([LEGACY_HEADERS])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mixed.png"
            path.write_bytes(b"not-read-in-unit-test")
            with patch(
                "agents.chargeback_tracker.parser.extract_chargeback_rows",
                return_value=extracted,
            ):
                result = ChargebackImportService(sheet).import_report(path, apply=True)

        self.assertEqual(result.source_rows, 4)
        self.assertEqual(result.skipped_refunded_error, 2)
        self.assertEqual(result.appended, 2)
        self.assertEqual([record.record_number for record in result.skipped_records], [2, 3])
        self.assertEqual([record.account_id for record in sheet.appended], ["A-20", "A-23"])
        self.assertEqual([record.client_name for record in sheet.appended], ["NDH", "ICR"])

        with tempfile.TemporaryDirectory() as temp_dir:
            preview_path = Path(temp_dir) / "mixed.png"
            preview_path.write_bytes(b"not-read-in-unit-test")
            with self.assertLogs(level="INFO") as logs:
                preview_sheet = FakeChargebackSheet([LEGACY_HEADERS])
                with patch(
                    "agents.chargeback_tracker.parser.extract_chargeback_rows",
                    return_value=extracted,
                ):
                    preview_result = ChargebackImportService(preview_sheet).import_report(
                        preview_path,
                        apply=False,
                    )
                log_import_result(preview_result)

        self.assertEqual(preview_sheet.appended, [])
        output = "\n".join(logs.output)
        self.assertIn("record=2 skipped=refunded_or_error", output)
        self.assertIn("record=3 skipped=refunded_or_error", output)
        self.assertIn("refunded_error_skipped=2", output)


class ChargebackConnectionTests(unittest.TestCase):
    def test_google_structure_validation_is_read_only(self) -> None:
        sheet = GoogleChargebackSheet.__new__(GoogleChargebackSheet)
        sheet.spreadsheet_id = PRODUCTION_SPREADSHEET_ID
        sheet.sheet_name = PRODUCTION_SHEET_NAME
        sheet.session = ReadOnlyVerificationSession()

        sheet.validate_structure()

        self.assertEqual(len(sheet.session.get_calls), 1)

    def test_verifies_sheet_tab_and_append_capability_without_writes(self) -> None:
        sheet = GoogleChargebackSheet.__new__(GoogleChargebackSheet)
        sheet.spreadsheet_id = PRODUCTION_SPREADSHEET_ID
        sheet.sheet_name = PRODUCTION_SHEET_NAME
        sheet.session = ReadOnlyVerificationSession()

        result = sheet.verify_connection()

        self.assertEqual(result.spreadsheet_title, "United Charge Back Tracker")
        self.assertEqual(result.worksheet_title, PRODUCTION_SHEET_NAME)
        self.assertTrue(result.can_append)
        self.assertEqual(len(sheet.session.get_calls), 2)

    def test_rejects_read_only_service_account(self) -> None:
        sheet = GoogleChargebackSheet.__new__(GoogleChargebackSheet)
        sheet.spreadsheet_id = PRODUCTION_SPREADSHEET_ID
        sheet.sheet_name = PRODUCTION_SHEET_NAME
        sheet.session = ReadOnlyVerificationSession(can_edit=False)

        with self.assertRaisesRegex(RuntimeError, "does not have content-edit access"):
            sheet.verify_connection()

    def test_configuration_is_pinned_to_existing_production_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            credential = Path(temp_dir) / "service-account.json"
            credential.write_text("{}")
            settings = ChargebackSettings(
                spreadsheet_id="wrong-sheet",
                sheet_name="new-tab",
                service_account_file=credential,
                log_level="INFO",
            )

            errors = validate_chargeback_settings(settings, require_credentials=True)

        self.assertIn(
            "CHARGEBACK_SPREADSHEET_ID must identify the existing United Charge Back Tracker.",
            errors,
        )
        self.assertIn(
            "CHARGEBACK_SHEET_NAME must identify the existing production worksheet.",
            errors,
        )


def write_chargeback_xlsx(path: Path) -> None:
    rows = [
        ["Account ID", "Consumer Name", "Chargeback Date", "Amount", "Due Client"],
        ["A-9", "Person Nine", "7/22/2026", "$125.00", "$77.50"],
    ]
    row_xml: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column_number, value in enumerate(row, start=1):
            reference = f"{chr(64 + column_number)}{row_number}"
            cells.append(
                f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


if __name__ == "__main__":
    unittest.main()
