from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from agents.dashboard.review_actions import ReviewActionService
from agents.icr_remit_agent.database import ICRRemitDatabase
from agents.icr_remit_agent.models import ICRRemitResult
from shared.data_layer import (
    InMemorySharedRecordRepository,
    ReviewStatus,
    normalize_cash_flow_notion_page,
)
from shared.data_layer.sync import SharedDataSyncService, load_icr_records


NOW = datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc)


class SharedDataSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemorySharedRecordRepository()
        self.service = SharedDataSyncService(self.repository)
        self.record = normalize_cash_flow_notion_page(notion_page())

    def test_cash_flow_notion_adapter_maps_decimal_status_and_safe_identity(self) -> None:
        record = self.record

        self.assertEqual(record.source_system.value, "notion")
        self.assertEqual(record.source_record_id, "page-1")
        self.assertEqual(record.amount, Decimal("125.5"))
        self.assertEqual(record.effective_date, date(2026, 7, 15))
        self.assertEqual(record.status.value, "needs_review")
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertEqual(record.metadata["vendor"], "Phone Vendor")

    def test_cash_flow_notion_adapter_treats_no_as_no_action(self) -> None:
        record = normalize_cash_flow_notion_page(
            notion_page(status="Upcoming", action_required="No")
        )

        self.assertIsNone(record.action_required)
        self.assertEqual(record.review_status, ReviewStatus.NOT_REQUIRED)
        self.assertEqual(record.priority.value, "normal")

    def test_cash_flow_notion_adapter_normalizes_yes_to_clear_action(self) -> None:
        record = normalize_cash_flow_notion_page(
            notion_page(status="Upcoming", action_required="Yes")
        )

        self.assertEqual(record.action_required, "Action required")
        self.assertEqual(record.review_status, ReviewStatus.PENDING)

    def test_cash_flow_notion_adapter_preserves_specific_action_text(self) -> None:
        record = normalize_cash_flow_notion_page(
            notion_page(status="Upcoming", action_required="Confirm invoice amount")
        )

        self.assertEqual(record.action_required, "Confirm invoice amount")

    def test_dry_run_plans_create_without_writing(self) -> None:
        plan = self.service.plan([self.record])

        self.assertEqual(plan.to_dict()["counts"]["create"], 1)
        self.assertEqual(self.repository.list(), [])

    def test_apply_writes_only_create_and_is_idempotent(self) -> None:
        first_plan = self.service.plan([self.record])
        first_report = self.service.apply(first_plan)
        second_plan = self.service.plan([self.record])

        self.assertEqual(first_report["counts"]["create"], 1)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertEqual(second_plan.to_dict()["counts"]["skip"], 1)

    def test_open_record_source_change_plans_update(self) -> None:
        self.repository.upsert(self.record)
        changed = replace(self.record, amount=Decimal("130.00"))

        plan = self.service.plan([changed])
        self.service.apply(plan)

        self.assertEqual(plan.to_dict()["counts"]["update"], 1)
        self.assertEqual(self.repository.get(self.record.id).amount, Decimal("130.00"))

    def test_unchanged_terminal_review_is_skipped(self) -> None:
        saved = self.repository.upsert(self.record)
        ReviewActionService(self.repository).apply(
            saved.id,
            action="approve",
            reviewer="Jaye",
            reason=None,
            expected_updated_at=saved.updated_at.isoformat(),
            request_id="approve-1",
        )

        plan = self.service.plan([self.record])

        self.assertEqual(plan.to_dict()["counts"]["skip"], 1)

    def test_changed_terminal_review_blocks_entire_apply(self) -> None:
        saved = self.repository.upsert(self.record)
        ReviewActionService(self.repository).apply(
            saved.id,
            action="approve",
            reviewer="Jaye",
            reason=None,
            expected_updated_at=saved.updated_at.isoformat(),
            request_id="approve-1",
        )
        second = normalize_cash_flow_notion_page(notion_page(page_id="page-2"))
        plan = self.service.plan([replace(self.record, amount=Decimal("140.00")), second])

        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            self.service.apply(plan)

        self.assertIsNone(self.repository.get(second.id))

    def test_source_error_blocks_apply(self) -> None:
        plan = self.service.plan([self.record], source_errors=["bad source record"])

        with self.assertRaisesRegex(RuntimeError, "failed to load"):
            self.service.apply(plan)

    def test_duplicate_input_source_identity_is_conflict(self) -> None:
        plan = self.service.plan([self.record, self.record])

        self.assertEqual(plan.to_dict()["counts"]["conflict"], 1)

    def test_icr_database_loader_returns_normalized_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "payment.sqlite3"
            database = ICRRemitDatabase(database_path)
            database.initialize()
            database.save_import(
                ICRRemitResult(
                    broker="ICR",
                    contact="Jim",
                    remit_week=date(2026, 7, 6),
                    week_ending=date(2026, 7, 12),
                    file_path=Path("United Remit 7-12-26.xlsx"),
                    due_to_agency=Decimal("511.83"),
                    due_to_client=Decimal("767.68"),
                    total_collected=Decimal("1279.51"),
                    status="Completed",
                )
            )

            loaded = load_icr_records(SimpleNamespace(database_path=database_path), limit=10)

        self.assertEqual(loaded.errors, ())
        self.assertEqual(len(loaded.records), 1)
        self.assertEqual(loaded.records[0].amount, Decimal("767.68"))
        self.assertEqual(loaded.records[0].status.value, "completed")


def notion_page(
    *,
    page_id: str = "page-1",
    status: str = "Needs Review",
    action_required: str = "Confirm amount",
) -> dict:
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "created_time": "2026-07-01T12:00:00.000Z",
        "last_edited_time": "2026-07-12T12:00:00.000Z",
        "properties": {
            "Expense Name": {"title": [{"plain_text": "July phone bill"}]},
            "Vendor / Payee": {"rich_text": [{"plain_text": "Phone Vendor"}]},
            "Amount": {"number": 125.5},
            "Due Date": {"date": {"start": "2026-07-15"}},
            "Status": {"select": {"name": status}},
            "Due Status": {"formula": {"type": "string", "string": "Due Soon"}},
            "Action Required": {"formula": {"type": "string", "string": action_required}},
            "Payment Type": {"select": {"name": "Auto Pay"}},
            "Category": {"select": {"name": "Phone"}},
            "Source": {"select": {"name": "Email"}},
            "Notes": {"rich_text": [{"plain_text": "Review before paying"}]},
        },
    }


if __name__ == "__main__":
    unittest.main()
