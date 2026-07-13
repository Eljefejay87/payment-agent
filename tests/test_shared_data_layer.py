from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.cash_flow_hq.models import BillCandidate
from agents.icr_remit_agent.models import ICRRemitResult
from shared.data_layer import (
    AgentRunRecord,
    InMemorySharedRecordRepository,
    Priority,
    RecordFilters,
    RecordType,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    Status,
    cash_flow_idempotency_key,
    generate_idempotency_key,
    icr_remit_idempotency_key,
    normalize_cash_flow_bill,
    normalize_icr_remit,
)


NOW = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)


class SharedDataLayerTests(unittest.TestCase):
    def test_shared_record_serializes_decimal_datetime_and_metadata(self) -> None:
        record = build_record(metadata={"agent_field": {"value": "kept"}})

        payload = record.to_dict()
        restored = SharedRecord.from_dict(payload)

        self.assertEqual(payload["amount"], "10.50")
        self.assertEqual(payload["created_at"], "2026-07-12T20:00:00+00:00")
        self.assertEqual(restored.amount, Decimal("10.50"))
        self.assertEqual(restored.metadata, {"agent_field": {"value": "kept"}})

    def test_shared_record_rejects_invalid_types_and_naive_datetimes(self) -> None:
        with self.assertRaisesRegex(TypeError, "status must be Status"):
            build_record(status="new")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "amount must be Decimal"):
            build_record(amount=10.50)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            build_record(created_at=datetime(2026, 7, 12, 20, 0))

    def test_idempotency_is_stable_for_decimal_and_timezone_formatting(self) -> None:
        eastern = timezone(timedelta(hours=-4))
        first = generate_idempotency_key(
            "test",
            Decimal("10.5000"),
            datetime(2026, 7, 12, 16, 0, tzinfo=eastern),
        )
        second = generate_idempotency_key(
            "test",
            Decimal("10.5"),
            datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, generate_idempotency_key("test", Decimal("10.51"), NOW))

    def test_domain_idempotency_keys_change_for_material_differences(self) -> None:
        cash_key = cash_flow_idempotency_key("Vendor", Decimal("50.00"), date(2026, 7, 20), "message-1")
        same_cash_key = cash_flow_idempotency_key(" vendor ", Decimal("50"), date(2026, 7, 20), "MESSAGE-1")
        remit_key = icr_remit_idempotency_key("ICR:Jim", date(2026, 7, 6), Decimal("1279.51"), "remit.xlsx")

        self.assertEqual(cash_key, same_cash_key)
        self.assertNotEqual(cash_key, cash_flow_idempotency_key("Vendor", Decimal("51"), date(2026, 7, 20), "message-1"))
        self.assertNotEqual(remit_key, icr_remit_idempotency_key("ICR:Jim", date(2026, 7, 13), Decimal("1279.51"), "remit.xlsx"))

    def test_repository_upsert_prevents_source_and_idempotency_duplicates(self) -> None:
        repository = InMemorySharedRecordRepository()
        first = repository.upsert(build_record())
        replacement = repository.upsert(replace(build_record(record_id="different-id"), summary="updated"))

        self.assertEqual(replacement.id, first.id)
        self.assertEqual(len(repository.list()), 1)
        self.assertEqual(repository.get_by_source(SourceSystem.OUTLOOK, "source-1"), replacement)
        self.assertEqual(repository.get_by_idempotency_key("bill:key-1"), replacement)

    def test_repository_filters_and_review_status_updates(self) -> None:
        repository = InMemorySharedRecordRepository()
        record = repository.upsert(build_record())

        reviewed = repository.mark_reviewed(record.id, ReviewStatus.APPROVED, reviewer="owner")
        completed = repository.update_status(record.id, Status.COMPLETED)

        self.assertEqual(reviewed.metadata["reviewed_by"], "owner")
        self.assertEqual(completed.status, Status.COMPLETED)
        self.assertEqual(repository.list(RecordFilters(status=Status.COMPLETED)), [completed])

    def test_cash_flow_adapter_preserves_agent_specific_fields(self) -> None:
        candidate = BillCandidate(
            vendor_payee="Phone Vendor",
            expense_name="July phone bill",
            amount=Decimal("125.00"),
            due_date=date(2026, 7, 20),
            invoice_number="INV-7",
            payment_type="Auto Pay",
            category="Telecommunications",
            frequency="Monthly",
            email_link="https://outlook.office.com/mail/message-1",
            notes="Imported from Outlook",
            status="Needs Review",
            confidence="0.65",
            review_reasons=("Confirm amount",),
            field_sources={"amount": "email body"},
            message_id="message-1",
            internet_message_id="internet-1",
        )

        record = normalize_cash_flow_bill(candidate, notion_page_id="notion-page-1")

        self.assertEqual(record.record_type, RecordType.BILL)
        self.assertEqual(record.status, Status.NEEDS_REVIEW)
        self.assertEqual(record.review_status, ReviewStatus.PENDING)
        self.assertEqual(record.amount, Decimal("125.00"))
        self.assertTrue(record.metadata["autopay"])
        self.assertEqual(record.metadata["notion_page_id"], "notion-page-1")
        self.assertEqual(record.action_required, "Confirm amount")

    def test_icr_adapter_preserves_totals_and_existing_duplicate_key(self) -> None:
        result = ICRRemitResult(
            broker="ICR",
            contact="Jim",
            remit_week=date(2026, 7, 6),
            week_ending=date(2026, 7, 12),
            file_path=Path("UNITED REMIT 7-12-26.xlsx"),
            due_to_agency=Decimal("511.83"),
            due_to_client=Decimal("767.68"),
            total_collected=Decimal("1279.51"),
        )

        record = normalize_icr_remit(
            result,
            production_record_url="https://notion.so/page",
            outlook_draft_reference="draft-1",
        )

        self.assertEqual(record.record_type, RecordType.REMIT)
        self.assertEqual(record.amount, Decimal("767.68"))
        self.assertEqual(record.owner, "Jim")
        self.assertEqual(record.metadata["due_to_agency"], "511.83")
        self.assertEqual(record.metadata["due_to_client"], "767.68")
        self.assertEqual(record.metadata["total_collected"], "1279.51")
        self.assertEqual(record.metadata["outlook_draft_reference"], "draft-1")
        self.assertEqual(record.metadata["duplicate_key"], "ICR|2026-07-06|UNITED REMIT 7-12-26.xlsx")

    def test_agent_run_record_tracks_execution_counts(self) -> None:
        run = AgentRunRecord(
            agent_name="cash_flow_hq",
            run_id="run-1",
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=5),
            status=Status.COMPLETED,
            records_found=5,
            records_created=2,
            records_updated=1,
            records_skipped=1,
            records_flagged_for_review=1,
            dry_run=True,
            external_services_used=(SourceSystem.OUTLOOK, SourceSystem.NOTION),
        )
        repository = InMemorySharedRecordRepository()

        repository.record_agent_run(run)

        self.assertEqual(repository.get_agent_run("run-1"), run)
        self.assertEqual(run.to_dict()["external_services_used"], ["outlook", "notion"])


def build_record(
    *,
    record_id: str = "record-1",
    status: Status = Status.NEW,
    amount: Decimal = Decimal("10.50"),
    created_at: datetime = NOW,
    metadata: dict | None = None,
) -> SharedRecord:
    return SharedRecord(
        id=record_id,
        record_type=RecordType.BILL,
        source_system=SourceSystem.OUTLOOK,
        source_record_id="source-1",
        source_url="https://example.com/source-1",
        created_at=created_at,
        updated_at=NOW,
        effective_date=date(2026, 7, 20),
        status=status,
        priority=Priority.NORMAL,
        review_status=ReviewStatus.NOT_REQUIRED,
        amount=amount,
        title="Test bill",
        metadata=metadata or {},
        idempotency_key="bill:key-1",
    )


if __name__ == "__main__":
    unittest.main()
