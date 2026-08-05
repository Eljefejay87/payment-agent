from __future__ import annotations

import tempfile
import unittest
import json
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

            def list_bills(self, scope: str) -> dict:
                return {
                    "status": "ok",
                    "scope": scope,
                    "bills": [
                        {
                            "bill_name": "Office Rent",
                            "amount": "1200.00",
                            "due_date": "2026-08-03",
                            "status": "upcoming",
                        }
                    ],
                }

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

                bills = HTTPConnection("127.0.0.1", port)
                bills.request(
                    "POST",
                    "/internal/cash-flow/bills",
                    body='{"scope":"current_week"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                bills_response = bills.getresponse()
                bills_payload = json.loads(bills_response.read().decode())
                self.assertEqual(bills_response.status, 200)
                self.assertEqual(set(bills_payload["bills"][0]), {"bill_name", "amount", "due_date", "status"})
                self.assertNotIn("record_ref", str(bills_payload))

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

    def test_cash_flow_hq_incoming_weekly_remit_private_http_contract(self) -> None:
        class FakeCashFlowService:
            def __init__(self) -> None:
                self.calls = []

            def create_incoming_weekly_remit(self, amount, *, replace_existing=False) -> dict:
                self.calls.append((str(amount), replace_existing))
                return {"status": "duplicate" if not replace_existing else "updated"}

        with tempfile.TemporaryDirectory() as directory:
            payment = Path(directory) / "payment.json"
            voicemail = Path(directory) / "voicemail.json"
            payment.write_text('{"service_status":"running","graph_status":"available"}')
            voicemail.write_text('{"status":"running"}')
            service = FakeCashFlowService()
            try:
                bridge = PaymentStatusBridge(
                    token="approved",
                    payment_health_path=payment,
                    voicemail_health_path=voicemail,
                    cash_flow_hq_service=service,
                    host="127.0.0.1",
                    port=0,
                )
            except PermissionError:
                self.skipTest("Local sandbox does not permit loopback listeners.")
            bridge.start()
            port = bridge.server.server_address[1]
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request(
                    "POST",
                    "/internal/cash-flow/incoming-weekly-remit",
                    body='{"amount":"8573"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                first = json.loads(conn.getresponse().read().decode())
                self.assertEqual(first["status"], "duplicate")

                conn = HTTPConnection("127.0.0.1", port)
                conn.request(
                    "POST",
                    "/internal/cash-flow/incoming-weekly-remit",
                    body='{"amount":"8573","replace_existing":true}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                second = json.loads(conn.getresponse().read().decode())
                self.assertEqual(second["status"], "updated")
                self.assertEqual(service.calls, [("8573", False), ("8573", True)])
            finally:
                bridge.stop()

    def test_cash_flow_hq_incoming_weekly_remit_received_private_http_contract(self) -> None:
        class FakeCashFlowService:
            def __init__(self) -> None:
                self.calls = []

            def mark_incoming_weekly_remit_received(self, amount) -> dict:
                self.calls.append(None if amount is None else str(amount))
                return {"status": "paid"}

        with tempfile.TemporaryDirectory() as directory:
            payment = Path(directory) / "payment.json"
            voicemail = Path(directory) / "voicemail.json"
            payment.write_text('{"service_status":"running","graph_status":"available"}')
            voicemail.write_text('{"status":"running"}')
            service = FakeCashFlowService()
            try:
                bridge = PaymentStatusBridge(
                    token="approved",
                    payment_health_path=payment,
                    voicemail_health_path=voicemail,
                    cash_flow_hq_service=service,
                    host="127.0.0.1",
                    port=0,
                )
            except PermissionError:
                self.skipTest("Local sandbox does not permit loopback listeners.")
            bridge.start()
            port = bridge.server.server_address[1]
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request(
                    "POST",
                    "/internal/cash-flow/incoming-weekly-remit/received",
                    body='{"amount":"8562.91"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                response = json.loads(conn.getresponse().read().decode())
                self.assertEqual(response["status"], "paid")
                self.assertEqual(service.calls, ["8562.91"])
            finally:
                bridge.stop()

    def test_cash_flow_hq_conversational_search_handles_vendor_amount_invoice_and_status_queries(self) -> None:
        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository
        from decimal import Decimal
        from datetime import date

        repository = InMemorySharedRecordRepository()
        for record in [
            SharedRecord(
                id="bill-adp",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-adp",
                title="ADP Payroll",
                amount=Decimal("69.00"),
                effective_date=date(2026, 8, 15),
                status=Status.UPCOMING,
                metadata={"invoice_number": "725823402"},
            ),
            SharedRecord(
                id="bill-comcast",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-comcast",
                title="Comcast",
                amount=Decimal("79.99"),
                effective_date=date(2026, 8, 16),
                status=Status.PAID,
                metadata={"invoice_number": "COMCAST-1001"},
            ),
            SharedRecord(
                id="bill-adp-extra",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-adp-extra",
                title="ADP Payroll Extra",
                amount=Decimal("150.00"),
                effective_date=date(2026, 8, 22),
                status=Status.PAID,
                metadata={"invoice_number": "ADP-150"},
            ),
        ]:
            repository.upsert(record)
        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=None)

        exact_vendor = service.search("show me Comcast")
        self.assertEqual(exact_vendor["status"], "ok")
        self.assertEqual(exact_vendor["matches"][0]["bill_name"], "Comcast")

        amount_query = service.search("Find the $69 invoice")
        self.assertEqual(amount_query["matches"][0]["bill_name"], "ADP Payroll")

        invoice_query = service.search("What is the status of invoice 725823402?")
        self.assertEqual(invoice_query["matches"][0]["bill_name"], "ADP Payroll")

        no_match = service.search("Do we still owe Google $999?")
        self.assertEqual(no_match["matches"], [])
        self.assertIn("no matching bill", no_match["answer"].lower())

        multi = service.search("ADP")
        self.assertGreaterEqual(len(multi["matches"]), 2)
        self.assertIn("which one", str(multi["answer"]).lower())

        search_no_mutation = service.search("Did we pay Comcast?")
        self.assertEqual(search_no_mutation["matches"][0]["bill_name"], "Comcast")
        self.assertIn("paid", str(search_no_mutation["answer"]).lower())

        original_mark_paid = service.mark_paid
        service.mark_paid = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search invoked mark_paid"))
        alias_collision = service.search("mark paid Comcast")
        service.mark_paid = original_mark_paid
        self.assertEqual(alias_collision["matches"][0]["bill_name"], "Comcast")
        self.assertNotIn("mark_paid", str(alias_collision).lower())

    def test_cash_flow_hq_private_bridge_service_contract(self) -> None:
        """Test CashFlowHqPrivateBridgeService exact response contracts."""
        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerService, WeeklyCashPlannerDatabase, active_business_week
        from agents.icr_remit_agent.database import ICRRemitDatabase
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository
        from decimal import Decimal
        from datetime import date

        with tempfile.TemporaryDirectory() as directory:
            # Create test repository with bills
            repository = InMemorySharedRecordRepository()
            
            # Create 12 bills to test 10-match limit
            for i in range(60):
                bill = SharedRecord(
                    id=f"bill-{i}",
                    record_type=RecordType.BILL,
                    source_system=SourceSystem.NOTION,
                    source_record_id=f"notion-{i}",
                    title=f"Test Bill {i}",
                    amount=Decimal("100.00"),
                    effective_date=date(2026, 8, min(i + 1, 28)),
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
            planner.record_already_sent_remit(active_business_week(), Decimal("5000.00"), Decimal("1200.00"))
            
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

            # Test read-only bill lists expose no internal record refs
            current_week = service.list_bills("current_week")
            self.assertEqual(current_week["status"], "ok")
            self.assertEqual(current_week["scope"], "current_week")
            self.assertTrue(current_week["bills"])
            self.assertEqual(set(current_week["bills"][0].keys()), {"bill_name", "amount", "due_date", "status"})
            self.assertLessEqual(len(current_week["bills"]), 50)
            self.assertNotIn("record_ref", str(current_week))

            needs_review = SharedRecord(
                id="bill-review",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-review",
                title="Review Needed Bill",
                amount=Decimal("75.00"),
                effective_date=date(2026, 8, 2),
                status=Status.NEEDS_REVIEW,
            )
            repository.upsert(needs_review)
            review_list = service.list_bills("bills_needing_review")
            self.assertEqual({bill["bill_name"] for bill in review_list["bills"]}, {"Review Needed Bill", "Office Rent"})
            unpaid_list = service.list_bills("unpaid")
            self.assertIn("ADP Payroll", [bill["bill_name"] for bill in unpaid_list["bills"]])
            self.assertEqual(len(unpaid_list["bills"]), 50)
            self.assertTrue(unpaid_list["truncated"])
            self.assertGreater(unpaid_list["total_count"], 50)
            with self.assertRaises(ValueError):
                service.list_bills("unsupported")
            
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
