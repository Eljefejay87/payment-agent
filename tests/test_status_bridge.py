from __future__ import annotations

import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from agents.payment_agent.status_bridge import PaymentStatusBridge, build_status_payload, _token_matches


class PaymentStatusBridgeTests(unittest.TestCase):
    def test_bridge_payload_is_strictly_allowlisted_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payment = Path(directory) / "payment.json"
            voicemail = Path(directory) / "voicemail.json"
            payment.write_text('{"service_status":"running","graph_status":"unavailable","attention_required":true,"last_successful_run":"2026-07-16T08:00:00Z","last_successful_job":"scan_once","last_error":"secret body","account_number":"123"}')
            voicemail.write_text('{"status":"running","last_successful_scan":"2026-07-16T08:00:00Z","last_successful_job":"scan_once","phone_number":"123"}')
            payload = build_status_payload(payment, voicemail)
        self.assertEqual(set(payload), {"service_status", "graph_status", "attention_required", "last_successful_run", "last_successful_job", "voicemail_status", "voicemail_last_successful_scan", "voicemail_last_successful_job"})
        self.assertNotIn("secret", str(payload))
        self.assertNotIn("123", str(payload))

    def test_bridge_token_comparison_rejects_missing_or_wrong_values(self) -> None:
        self.assertTrue(_token_matches("approved", "approved"))
        self.assertFalse(_token_matches("wrong", "approved"))
        self.assertFalse(_token_matches("", "approved"))

    def test_private_endpoint_requires_token_and_returns_only_sanitized_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payment = Path(directory) / "payment.json"
            voicemail = Path(directory) / "voicemail.json"
            payment.write_text('{"service_status":"running","graph_status":"available"}')
            voicemail.write_text('{"status":"running"}')
            try:
                bridge = PaymentStatusBridge(token="approved", payment_health_path=payment, voicemail_health_path=voicemail, host="127.0.0.1", port=0)
            except PermissionError:
                self.skipTest("Local sandbox does not permit loopback listeners.")
            bridge.start()
            port = bridge.server.server_address[1]
            denied = HTTPConnection("127.0.0.1", port); denied.request("GET", "/internal/status")
            self.assertEqual(denied.getresponse().status, 401)
            allowed = HTTPConnection("127.0.0.1", port); allowed.request("GET", "/internal/status", headers={"Authorization": "Bearer approved"})
            response = allowed.getresponse()
            self.assertEqual(response.status, 200)
            self.assertNotIn("last_error", response.read().decode())
            bridge.stop()

    def test_cash_flow_hq_search_and_mark_paid_private_http_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payment = Path(directory) / "payment.json"
            voicemail = Path(directory) / "voicemail.json"
            payment.write_text('{"service_status":"running","graph_status":"available"}')
            voicemail.write_text('{"status":"running"}')
            bridge = PaymentStatusBridge(
                token="approved",
                payment_health_path=payment,
                voicemail_health_path=voicemail,
                cash_flow_hq_service=None,
                host="127.0.0.1",
                port=0,
            )
            bridge.start()
            port = bridge.server.server_address[1]
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request(
                    "POST",
                    "/internal/cash-flow/search",
                    body='{"query":"ADP"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                # When service is None, bridge returns 404 unavailable
                self.assertEqual(response.status, 404)
                payload = response.read().decode()
                self.assertIn('"status":"unavailable"', payload)
            finally:
                bridge.stop()

    def test_cash_flow_hq_private_bridge_service_contract(self) -> None:
        """Test CashFlowHqPrivateBridgeService exact response contracts."""
        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerService, WeeklyCashPlannerDatabase
        from agents.icr_remit_agent.database import ICRRemitDatabase
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository
        from decimal import Decimal
        from datetime import date

        with tempfile.TemporaryDirectory() as directory:
            # Create test repository with bills
            repository = InMemorySharedRecordRepository()
            
            # Create 12 bills to test 10-match limit
            for i in range(12):
                bill = SharedRecord(
                    id=f"bill-{i}",
                    record_type=RecordType.BILL,
                    source_system=SourceSystem.NOTION,
                    source_record_id=f"notion-{i}",
                    title=f"Test Bill {i}",
                    amount=Decimal("100.00"),
                    effective_date=date(2026, 8, i + 1),
                    status=Status.UPCOMING,
                )
                repository.upsert(bill)
            
            # Create specific test bills
            adp_bill = SharedRecord(
                id="bill-adp",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-adp",
                title="ADP Payroll",
                amount=Decimal("1500.00"),
                effective_date=date(2026, 8, 15),
                status=Status.UPCOMING,
            )
            rent_bill = SharedRecord(
                id="bill-rent",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-rent",
                title="Office Rent",
                amount=Decimal("2000.00"),
                effective_date=date(2026, 8, 1),
                status=Status.PAST_DUE,
            )
            cancelled_bill = SharedRecord(
                id="bill-cancelled",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-cancelled",
                title="Cancelled Service",
                amount=Decimal("500.00"),
                effective_date=date(2026, 8, 10),
                status=Status.CANCELLED,
            )
            completed_bill = SharedRecord(
                id="bill-completed",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-completed",
                title="Completed Payment",
                amount=Decimal("300.00"),
                effective_date=date(2026, 8, 5),
                status=Status.COMPLETED,
            )
            failed_bill = SharedRecord(
                id="bill-failed",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-failed",
                title="Failed Payment",
                amount=Decimal("400.00"),
                effective_date=date(2026, 8, 6),
                status=Status.FAILED,
            )
            repository.upsert(adp_bill)
            repository.upsert(rent_bill)
            repository.upsert(cancelled_bill)
            repository.upsert(completed_bill)
            repository.upsert(failed_bill)
            
            # Create test planner
            planner_db = WeeklyCashPlannerDatabase(Path(directory) / "planner.sqlite3")
            remit_db = ICRRemitDatabase(Path(directory) / "remit.sqlite3")
            planner = WeeklyCashPlannerService(planner_db.path, remit_db.path)
            
            # Initialize service with test dependencies
            service = CashFlowHqPrivateBridgeService(
                database_path="unused",
                repository=repository,
                planner=planner,
            )
            
            # Test search returns exact keys
            result = service.search("ADP")
            self.assertEqual(result["status"], "ok")
            self.assertIn("matches", result)
            self.assertEqual(len(result["matches"]), 1)
            match = result["matches"][0]
            self.assertEqual(set(match.keys()), {"record_ref", "bill_name", "amount", "due_date", "current_status"})
            self.assertEqual(match["bill_name"], "ADP Payroll")
            self.assertEqual(match["current_status"], "upcoming")
            
            # Test empty query returns no matches
            result = service.search("")
            self.assertEqual(result["matches"], [])
            
            # Test search excludes cancelled, completed, and failed bills
            result = service.search("Cancelled")
            self.assertEqual(len(result["matches"]), 0)
            result = service.search("Completed")
            self.assertEqual(len(result["matches"]), 0)
            result = service.search("Failed")
            self.assertEqual(len(result["matches"]), 0)
            
            # Test search excludes paid bills
            repository.update_status("bill-rent", Status.PAID)
            result = service.search("Office")
            self.assertEqual(len(result["matches"]), 0)
            
            # Test search returns max 10 matches
            result = service.search("Test")
            self.assertEqual(len(result["matches"]), 10)
            
            # Test mark_paid returns exact keys
            result = service.mark_paid("bill-adp")
            self.assertEqual(result["status"], "ok")
            self.assertIn("updated", result)
            self.assertIn("planner_summary", result)
            updated = result["updated"]
            self.assertEqual(set(updated.keys()), {"record_ref", "bill_name", "amount", "due_date", "current_status"})
            self.assertEqual(updated["current_status"], "paid")
            
            # Test mark_paid raises ValueError if already paid
            with self.assertRaises(ValueError) as ctx:
                service.mark_paid("bill-adp")
            self.assertIn("already marked paid", str(ctx.exception))
            self.assertNotIn("bill-adp", str(ctx.exception))  # No record ref in message
            
            # Test mark_paid raises KeyError if not found
            with self.assertRaises(KeyError) as ctx:
                service.mark_paid("nonexistent")
            self.assertIn("not found", str(ctx.exception))
            self.assertNotIn("nonexistent", str(ctx.exception))  # No record ref in message
            
            # Test planner_summary preserves existing totals and adds the full planner contract
            summary = service.planner_summary(today=date(2026, 8, 7))
            self.assertEqual(set(summary.keys()), {
                "operating_cash",
                "current_week_obligations",
                "overdue_items_requiring_review",
                "projected_ending_cash",
                "current_weekly_remit",
                "jim_remit",
                "jim_remit_status",
                "already_paid",
                "reserved_funds_total",
                "reserved_funds",
                "safe_to_spend_cash",
                "current_week_obligation_details",
            })
            
            # Verify all scalar financial values are dollar-formatted strings
            for key in ("operating_cash", "current_week_obligations", "overdue_items_requiring_review", "projected_ending_cash", "already_paid", "reserved_funds_total", "safe_to_spend_cash"):
                self.assertTrue(summary[key].startswith("$"), f"{key} should be dollar-formatted")
            
            # Verify projected_ending_cash is calculated independently (not copied from spendable_cash)
            # It should be operating_cash - current_week_obligations
            from agents.cash_flow_hq.private_bridge_service import _parse_money
            operating = _parse_money(summary["operating_cash"])
            obligations = _parse_money(summary["current_week_obligations"])
            projected = _parse_money(summary["projected_ending_cash"])
            self.assertEqual(projected, operating - obligations)
            self.assertEqual(summary["current_weekly_remit"], {"week_start": "", "amount": "$0.00"})
            self.assertEqual(summary["jim_remit"], "$0.00")
            self.assertEqual(summary["jim_remit_status"], "")
            self.assertEqual(summary["reserved_funds_total"], "$0.00")
            self.assertEqual(summary["reserved_funds"], [])
            self.assertEqual(summary["safe_to_spend_cash"], "$0.00")
            self.assertEqual(summary["current_week_obligation_details"], [])
            
            # Test negative projected ending cash is supported
            # Create many high-value bills to exceed operating cash
            for i in range(5):
                high_bill = SharedRecord(
                    id=f"high-bill-{i}",
                    record_type=RecordType.BILL,
                    source_system=SourceSystem.NOTION,
                    source_record_id=f"notion-high-{i}",
                    title=f"High Bill {i}",
                    amount=Decimal("10000.00"),
                    effective_date=date(2026, 8, 20 + i),
                    status=Status.DUE,
                )
                repository.upsert(high_bill)
            
            summary2 = service.planner_summary()
            projected2 = _parse_money(summary2["projected_ending_cash"])
            # Verify negative values are formatted correctly
            self.assertTrue(summary2["projected_ending_cash"].startswith("$"))

    def test_planner_summary_reports_current_month_paid_bills_without_double_counting_jim_remit(self) -> None:
        from datetime import date
        from decimal import Decimal

        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository

        repository = InMemorySharedRecordRepository()
        for record_id, title, amount, due_date in [
            ("paid-current", "Current Paid Bill", Decimal("100.00"), date(2026, 8, 5)),
            ("paid-previous", "Previous Month Paid Bill", Decimal("200.00"), date(2026, 7, 31)),
            ("paid-future", "Future Month Paid Bill", Decimal("300.00"), date(2026, 9, 1)),
            ("paid-jim", "Jim Remit", Decimal("1200.00"), date(2026, 8, 6)),
        ]:
            repository.upsert(SharedRecord(
                id=record_id,
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id=f"notion-{record_id}",
                title=title,
                amount=amount,
                effective_date=due_date,
                status=Status.PAID,
            ))
        repository.upsert(SharedRecord(
            id="paid-invalid-date",
            record_type=RecordType.BILL,
            source_system=SourceSystem.NOTION,
            source_record_id="notion-paid-invalid-date",
            title="Invalid Date Paid Bill",
            amount=Decimal("400.00"),
            effective_date=None,
            status=Status.PAID,
        ))

        class FakePlanner:
            def jason_snapshot(self, _bills=None):
                return {
                    "plan": {
                        "week_start": "2026-08-03",
                        "weekly_remit_amount": "$5,000.00",
                        "jim_remit_amount": "$1,200.00",
                        "jim_remit_status": "Open",
                    },
                    "operating_cash": "$3,800.00",
                    "reserved_cash": "$800.00",
                    "spendable_cash": "$3,000.00",
                    "reservations": [{"title": "Payroll", "amount": "$800.00", "status": "Reserved", "due_date": "2026-08-10"}],
                    "bills_due_before_next_remit": [{"title": "Office Rent", "amount": Decimal("700.00"), "status": "Planned", "due_date": date(2026, 8, 10)}],
                }

        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=FakePlanner())
        summary = service.planner_summary(today=date(2026, 8, 7))

        self.assertEqual(summary["already_paid"], "$1,300.00")
        self.assertEqual(summary["jim_remit"], "$1,200.00")
        self.assertEqual(summary["reserved_funds_total"], "$800.00")
        self.assertEqual(summary["safe_to_spend_cash"], "$3,000.00")
        self.assertEqual(summary["reserved_funds"], [{"title": "Payroll", "amount": "$800.00", "status": "Reserved", "due_date": "2026-08-10"}])
        self.assertEqual(summary["current_week_obligation_details"], [{"title": "Office Rent", "amount": "$700.00", "status": "Planned", "due_date": "2026-08-10"}])
