from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.cash_flow_hq.models import BillCandidate
from agents.dashboard.shared_data import ReadOnlyDashboardDataService, ReviewQueueFilters
from agents.icr_remit_agent.models import ICRRemitResult
from shared.data_layer import (
    AgentRunRecord,
    InMemorySharedRecordRepository,
    Priority,
    RecordType,
    ReviewStatus,
    SharedRecord,
    SourceSystem,
    Status,
    normalize_cash_flow_bill,
    normalize_icr_remit,
)


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 7, 12)


class DashboardSharedDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemorySharedRecordRepository()
        self.service = ReadOnlyDashboardDataService(self.repository, today=TODAY)

    def test_summary_uses_decimal_safe_boundaries_and_totals(self) -> None:
        self.repository.upsert(record("today", amount="0.10", effective_date=TODAY))
        self.repository.upsert(record("day-7", amount="0.20", effective_date=TODAY + timedelta(days=7)))
        self.repository.upsert(record("day-30", amount="0.30", effective_date=TODAY + timedelta(days=30)))
        self.repository.upsert(record("past", amount="1.11", effective_date=TODAY - timedelta(days=1)))

        summary = self.service.summary()

        self.assertEqual(summary["due_next_7_days"], "0.30")
        self.assertEqual(summary["due_next_30_days"], "0.60")
        self.assertEqual(summary["past_due_total"], "1.11")

    def test_review_inclusion_exclusion_ordering_and_safe_metadata(self) -> None:
        normal = record("normal", amount="5.00")
        high = replace(
            record("high", amount="7.00", effective_date=TODAY - timedelta(days=2)),
            priority=Priority.HIGH,
            status=Status.NEEDS_REVIEW,
            review_status=ReviewStatus.PENDING,
            action_required="Confirm invoice",
            confidence=0.60,
            metadata={"vendor": "Safe Vendor", "source_file": "/secret/path", "token": "never"},
        )
        critical = replace(
            record("critical", amount="9.00"),
            priority=Priority.CRITICAL,
            review_status=ReviewStatus.PENDING,
        )
        self.repository.upsert(normal)
        self.repository.upsert(high)
        self.repository.upsert(critical)

        items = self.service.needs_review()

        self.assertEqual([item["id"] for item in items], ["critical", "high"])
        self.assertIn("Confidence is below 72%", items[1]["review_reason"])
        self.assertEqual(items[1]["metadata"], {"vendor": "Safe Vendor"})
        self.assertNotIn("normal", [item["id"] for item in items])

    def test_review_filters_and_pagination(self) -> None:
        for index in range(3):
            self.repository.upsert(
                replace(
                    record(f"bill-{index}", amount="1.00"),
                    review_status=ReviewStatus.PENDING,
                    priority=Priority.HIGH,
                )
            )

        items = self.service.needs_review(
            ReviewQueueFilters(record_type=RecordType.BILL, priority=Priority.HIGH),
            page=2,
            page_size=2,
        )

        self.assertEqual(len(items), 1)

    def test_cash_flow_and_icr_adapters_appear_in_dashboard(self) -> None:
        bill = normalize_cash_flow_bill(
            BillCandidate(
                vendor_payee="Phone Vendor",
                expense_name="Phone bill",
                amount=Decimal("125.00"),
                due_date=TODAY + timedelta(days=5),
                invoice_number="INV-1",
                payment_type="AutoPay",
                category="Phone",
                frequency="Monthly",
                email_link="https://example.com/message",
                notes="",
                status="Needs Review",
                confidence="65%",
                review_reasons=("Confirm amount",),
                field_sources={},
                message_id="message-1",
                internet_message_id="internet-1",
            )
        )
        remit = normalize_icr_remit(
            ICRRemitResult(
                broker="ICR",
                contact="Jim",
                remit_week=TODAY - timedelta(days=2),
                week_ending=TODAY,
                file_path=Path("remit.xlsx"),
                due_to_agency=Decimal("40.00"),
                due_to_client=Decimal("60.00"),
                total_collected=Decimal("100.00"),
            )
        )
        self.repository.upsert(bill)
        self.repository.upsert(remit)

        summary = self.service.summary()

        self.assertEqual(summary["due_next_7_days"], "125.00")
        self.assertEqual(summary["recent_remit_total"], "60.00")
        self.assertEqual(summary["needs_review"]["unresolved_count"], 1)

    def test_failed_agent_runs_are_reviewable_and_health_visible(self) -> None:
        self.repository.record_agent_run(
            AgentRunRecord(
                agent_name="cash_flow_hq",
                run_id="failed-1",
                started_at=NOW - timedelta(days=1),
                completed_at=NOW,
                status=Status.FAILED,
                error_message="Fixture failure",
            )
        )

        summary = self.service.summary()

        self.assertEqual(summary["needs_review"]["failed_agent_run_count"], 1)
        self.assertEqual(summary["needs_review"]["top_items"][0]["record_type"], "agent_run")
        self.assertEqual(len(summary["agent_health"]["failed_runs"]), 1)

    def test_service_exposes_no_write_operations(self) -> None:
        forbidden = {"upsert", "create", "update", "approve", "reject", "delete", "send", "pay"}
        self.assertTrue(forbidden.isdisjoint(set(dir(self.service))))


def record(
    record_id: str,
    *,
    amount: str,
    effective_date: date = TODAY + timedelta(days=1),
) -> SharedRecord:
    return SharedRecord(
        id=record_id,
        record_type=RecordType.BILL,
        source_system=SourceSystem.OUTLOOK,
        source_record_id=f"source-{record_id}",
        created_at=NOW - timedelta(days=3),
        updated_at=NOW,
        effective_date=effective_date,
        amount=Decimal(amount),
        title=f"Bill {record_id}",
        idempotency_key=f"key-{record_id}",
    )


if __name__ == "__main__":
    unittest.main()
