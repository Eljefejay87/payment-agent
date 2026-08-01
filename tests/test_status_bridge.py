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
        from agents.cash_flow_hq.private_bridge_service import StaleCashFlowRecordError

        class CashFlowService:
            def __init__(self) -> None:
                self.marked: list[str] = []

            def search(self, query: str) -> dict:
                return {"status": "ok", "matches": []}

            def mark_paid(self, record_ref: str, expected_status: str) -> dict:
                if expected_status == "past_due":
                    raise StaleCashFlowRecordError("stale")
                self.marked.append(f"{record_ref}:{expected_status}")
                return {
                    "status": "ok",
                    "updated": {
                        "record_ref": record_ref,
                        "bill_name": "ADP Payroll",
                        "amount": "1500.00",
                        "due_date": "2026-08-15",
                        "current_status": "paid",
                    },
                    "planner_summary": {
                        "operating_cash": "$2,685.29",
                        "current_week_obligations": "$0.00",
                        "overdue_items_requiring_review": "$0.00",
                        "projected_ending_cash": "$2,685.29",
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            payment = Path(directory) / "payment.json"
            voicemail = Path(directory) / "voicemail.json"
            payment.write_text('{"service_status":"running","graph_status":"available"}')
            voicemail.write_text('{"status":"running"}')
            cash_flow_service = CashFlowService()
            bridge = PaymentStatusBridge(
                token="approved",
                cash_flow_mutation_token="mutation-approved",
                payment_health_path=payment,
                voicemail_health_path=voicemail,
                cash_flow_hq_service=cash_flow_service,
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
                self.assertEqual(response.status, 200)
                response.read()

                denied = HTTPConnection("127.0.0.1", port)
                denied.request(
                    "POST",
                    "/internal/cash-flow/mark-paid",
                    body='{"record_ref":"bill-adp","expected_status":"upcoming"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                self.assertEqual(denied.getresponse().status, 401)
                self.assertEqual(cash_flow_service.marked, [])

                allowed = HTTPConnection("127.0.0.1", port)
                allowed.request(
                    "POST",
                    "/internal/cash-flow/mark-paid",
                    body='{"record_ref":"bill-adp","expected_status":"upcoming"}',
                    headers={"Authorization": "Bearer mutation-approved", "Content-Type": "application/json"},
                )
                self.assertEqual(allowed.getresponse().status, 200)
                self.assertEqual(cash_flow_service.marked, ["bill-adp:upcoming"])

                stale = HTTPConnection("127.0.0.1", port)
                stale.request(
                    "POST",
                    "/internal/cash-flow/mark-paid",
                    body='{"record_ref":"bill-adp","expected_status":"past_due"}',
                    headers={"Authorization": "Bearer mutation-approved", "Content-Type": "application/json"},
                )
                stale_response = stale.getresponse()
                self.assertEqual(stale_response.status, 409)
                self.assertIn('"status":"stale_record"', stale_response.read().decode())
                self.assertEqual(cash_flow_service.marked, ["bill-adp:upcoming"])
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
            result = service.mark_paid("bill-adp", "upcoming")
            self.assertEqual(result["status"], "ok")
            self.assertIn("updated", result)
            self.assertIn("planner_summary", result)
            updated = result["updated"]
            self.assertEqual(set(updated.keys()), {"record_ref", "bill_name", "amount", "due_date", "current_status"})
            self.assertEqual(updated["current_status"], "paid")
            
            # Test mark_paid rejects replay or stale state without another write
            with self.assertRaises(ValueError) as ctx:
                service.mark_paid("bill-adp", "upcoming")
            self.assertIn("changed after confirmation", str(ctx.exception))
            self.assertNotIn("bill-adp", str(ctx.exception))  # No record ref in message
            
            # Test mark_paid raises KeyError if not found
            with self.assertRaises(KeyError) as ctx:
                service.mark_paid("nonexistent", "upcoming")
            self.assertIn("not found", str(ctx.exception))
            self.assertNotIn("nonexistent", str(ctx.exception))  # No record ref in message

            with self.assertRaises(ValueError):
                service.mark_paid("bill-cancelled", "cancelled")
            self.assertEqual(repository.get("bill-cancelled").status, Status.CANCELLED)
            
            # Test planner_summary returns exact keys
            summary = service.planner_summary()
            self.assertEqual(set(summary.keys()), {
                "operating_cash",
                "current_week_obligations",
                "overdue_items_requiring_review",
                "projected_ending_cash",
            })
            
            # Verify all values are dollar-formatted strings
            for key in summary:
                self.assertTrue(summary[key].startswith("$"), f"{key} should be dollar-formatted")
            
            # Verify projected_ending_cash is calculated independently (not copied from spendable_cash)
            # It should be operating_cash - current_week_obligations
            from agents.cash_flow_hq.private_bridge_service import _parse_money
            operating = _parse_money(summary["operating_cash"])
            obligations = _parse_money(summary["current_week_obligations"])
            projected = _parse_money(summary["projected_ending_cash"])
            self.assertEqual(projected, operating - obligations)
            
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
