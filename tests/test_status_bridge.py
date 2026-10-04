from __future__ import annotations

import tempfile
import unittest
import json
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path

from agents.payment_agent.status_bridge import PaymentStatusBridge, build_status_payload, _token_matches
from agents.cash_flow_hq.private_bridge_service import StaleCashFlowRecord


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
            try:
                bridge = PaymentStatusBridge(
                    token="approved",
                    payment_health_path=payment,
                    voicemail_health_path=voicemail,
                    cash_flow_hq_service=None,
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

    def test_cash_flow_hq_mark_paid_private_http_honors_expected_status(self) -> None:
        class FakeCashFlowService:
            def __init__(self) -> None:
                self.calls = []

            def mark_paid(self, record_ref: str, expected_status: str | None = None) -> dict:
                self.calls.append((record_ref, expected_status))
                if expected_status != "upcoming":
                    raise StaleCashFlowRecord("changed")
                return {
                    "status": "ok",
                    "updated": {
                        "record_ref": record_ref,
                        "bill_name": "Comcast",
                        "amount": "218.00",
                        "due_date": "2026-08-07",
                        "current_status": "paid",
                    },
                    "planner_summary": {},
                }

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
                    "/internal/cash-flow/mark-paid",
                    body='{"record_ref":"bill-comcast","expected_status":"upcoming"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(service.calls[-1], ("bill-comcast", "upcoming"))

                stale = HTTPConnection("127.0.0.1", port)
                stale.request(
                    "POST",
                    "/internal/cash-flow/mark-paid",
                    body='{"record_ref":"bill-comcast","expected_status":"paid"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                stale_response = stale.getresponse()
                payload = json.loads(stale_response.read().decode())
                self.assertEqual(stale_response.status, 409)
                self.assertEqual(payload, {"status": "stale_record"})
            finally:
                bridge.stop()

    def test_jim_remit_mark_paid_private_http_contract_uses_dedicated_action(self) -> None:
        class FakeCashFlowService:
            def __init__(self) -> None:
                self.calls = []

            def current_week_jim_remit(self) -> dict:
                self.calls.append(("current",))
                return {
                    "status": "ok",
                    "record": {
                        "week_id": "weekly-cash-plan-2026-08-31",
                        "week_start": "2026-08-31",
                        "week_end": "2026-09-06",
                        "amount": "$1,330.22",
                        "current_status": "Open",
                        "paid_at": "",
                    },
                }

            def mark_current_week_jim_remit_paid(self, **kwargs) -> dict:
                self.calls.append(("mark", kwargs))
                if kwargs["expected_status"] != "Open":
                    raise StaleCashFlowRecord("changed")
                return {
                    "status": "ok",
                    "record": {
                        "week_id": kwargs["expected_week_id"],
                        "week_start": kwargs["expected_week_start"],
                        "week_end": "2026-09-06",
                        "amount": "$1,330.22",
                        "current_status": "Paid",
                        "paid_at": "2026-09-03T12:00:00+00:00",
                    },
                }

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
                current = HTTPConnection("127.0.0.1", port)
                current.request(
                    "POST",
                    "/internal/cash-flow/jim-remit/current",
                    body="{}",
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                current_response = current.getresponse()
                self.assertEqual(current_response.status, 200)
                self.assertEqual(json.loads(current_response.read().decode())["record"]["current_status"], "Open")

                mark = HTTPConnection("127.0.0.1", port)
                mark.request(
                    "POST",
                    "/internal/cash-flow/jim-remit/mark-paid",
                    body='{"expected_week_id":"weekly-cash-plan-2026-08-31","expected_week_start":"2026-08-31","expected_amount":"1330.22","expected_status":"Open"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                mark_response = mark.getresponse()
                self.assertEqual(mark_response.status, 200)
                self.assertEqual(json.loads(mark_response.read().decode())["record"]["current_status"], "Paid")
                self.assertEqual(service.calls[0], ("current",))
                self.assertEqual(service.calls[1][0], "mark")
                self.assertEqual(service.calls[1][1]["expected_amount"], Decimal("1330.22"))

                stale = HTTPConnection("127.0.0.1", port)
                stale.request(
                    "POST",
                    "/internal/cash-flow/jim-remit/mark-paid",
                    body='{"expected_week_id":"weekly-cash-plan-2026-08-31","expected_week_start":"2026-08-31","expected_amount":"1330.22","expected_status":"Paid"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                stale_response = stale.getresponse()
                self.assertEqual(stale_response.status, 409)
                self.assertEqual(json.loads(stale_response.read().decode()), {"status": "stale_record"})
            finally:
                bridge.stop()

    def test_cash_flow_hq_bill_list_private_http_contract_is_read_only_and_sanitized(self) -> None:
        class FakeCashFlowService:
            def __init__(self) -> None:
                self.calls = []

            def list_bills(self, scope: str) -> dict:
                self.calls.append(scope)
                return {
                    "status": "ok",
                    "scope": "current_week",
                    "bills": [
                        {
                            "bill_name": "Office Rent",
                            "amount": "1200.00",
                            "due_date": "2026-08-03",
                            "status": "upcoming",
                        }
                    ],
                }

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
                    "/internal/cash-flow/bills",
                    body='{"scope":"current_week"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                payload = json.loads(response.read().decode())
                self.assertEqual(response.status, 200)
                self.assertEqual(service.calls, ["current_week"])
                self.assertEqual(set(payload["bills"][0]), {"bill_name", "amount", "due_date", "status"})
                self.assertNotIn("record_ref", str(payload))
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

    def test_cash_flow_hq_incoming_weekly_remit_lookup_private_http_contract(self) -> None:
        class FakeCashFlowService:
            def search(self, query: str) -> dict:
                self.query = query
                return {
                    "status": "ok",
                    "record": {
                        "record_id": "page-123",
                        "amount": "8573.00",
                        "status": "upcoming",
                        "effective_date": "2026-08-05",
                        "collection": "Cash Flow HQ",
                        "table": "Incoming Weekly Remits",
                    },
                }

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
                    "/internal/cash-flow/search",
                    body='{"query":"Where did you save the NDH remit?"}',
                    headers={"Authorization": "Bearer approved", "Content-Type": "application/json"},
                )
                response = json.loads(conn.getresponse().read().decode())
                self.assertEqual(response["status"], "ok")
                self.assertEqual(response["record"]["record_id"], "page-123")
                self.assertEqual(response["record"]["collection"], "Cash Flow HQ")
                self.assertEqual(response["record"]["table"], "Incoming Weekly Remits")
            finally:
                bridge.stop()

    def test_cash_flow_hq_conversational_search_handles_vendor_amount_invoice_and_paid_status_queries(self) -> None:
        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository
        from decimal import Decimal
        from datetime import date

        repository = InMemorySharedRecordRepository()
        repository.upsert(
            SharedRecord(
                id="bill-adp-69",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-adp-69",
                title="ADP Payroll",
                amount=Decimal("69.00"),
                effective_date=date(2026, 8, 20),
                status=Status.UPCOMING,
                metadata={"invoice_number": "725823402"},
            )
        )
        repository.upsert(
            SharedRecord(
                id="bill-comcast-paid",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-comcast-paid",
                title="Comcast",
                amount=Decimal("79.99"),
                effective_date=date(2026, 8, 10),
                status=Status.PAID,
                metadata={"invoice_number": "COMCAST-1001"},
            )
        )
        repository.upsert(
            SharedRecord(
                id="bill-adp-other",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-adp-other",
                title="ADP Payroll Extra",
                amount=Decimal("150.00"),
                effective_date=date(2026, 8, 22),
                status=Status.PAID,
                metadata={"invoice_number": "ADP-150"},
            )
        )
        repository.upsert(
            SharedRecord(
                id="bill-utility-123",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-utility-123",
                title="City Water",
                amount=Decimal("87.00"),
                effective_date=date(2026, 8, 30),
                status=Status.UPCOMING,
                metadata={"invoice_number": "CITY-87"},
            )
        )
        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=None)

        exact_vendor = service.search("show me Comcast")
        self.assertEqual(exact_vendor["status"], "ok")
        self.assertEqual(len(exact_vendor["matches"]), 1)
        self.assertEqual(exact_vendor["matches"][0]["bill_name"], "Comcast")

        partial_vendor = service.search("search bills for payroll")
        self.assertEqual(partial_vendor["status"], "ok")
        self.assertEqual(len(partial_vendor["matches"]), 2)
        self.assertIn("ADP Payroll", {bill["bill_name"] for bill in partial_vendor["matches"]})

        amount_query = service.search("Find the $69 invoice")
        self.assertEqual(amount_query["status"], "ok")
        self.assertEqual(amount_query["matches"][0]["bill_name"], "ADP Payroll")

        approximate_amount = service.search("about $69")
        self.assertEqual(approximate_amount["status"], "ok")
        self.assertEqual(approximate_amount["matches"][0]["bill_name"], "ADP Payroll")

        invoice_query = service.search("What is the status of invoice 725823402?")
        self.assertEqual(invoice_query["status"], "ok")
        self.assertEqual(invoice_query["matches"][0]["bill_name"], "ADP Payroll")

        no_match = service.search("Do we still owe Google $999?")
        self.assertEqual(no_match["status"], "ok")
        self.assertEqual(no_match["matches"], [])
        self.assertIn("no matching bill", no_match["answer"].lower())

        multi = service.search("ADP")
        self.assertGreaterEqual(len(multi["matches"]), 2)
        self.assertIn("which one", str(multi["answer"]).lower())

        conflict = service.search("Is the ADP bill still due?")
        self.assertEqual(conflict["status"], "ok")
        self.assertEqual(conflict["matches"][0]["bill_name"], "ADP Payroll")
        self.assertIn("stored status", str(conflict["answer"]).lower())

        search_no_mutation = service.search("Did we pay Comcast?")
        self.assertEqual(search_no_mutation["status"], "ok")
        self.assertEqual(search_no_mutation["matches"][0]["bill_name"], "Comcast")
        self.assertIn("paid", str(search_no_mutation["answer"]).lower())

        original_mark_paid = service.mark_paid
        service.mark_paid = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search invoked mark_paid"))
        alias_collision = service.search("mark paid Comcast")
        service.mark_paid = original_mark_paid
        self.assertEqual(alias_collision["status"], "ok")
        self.assertEqual(alias_collision["matches"][0]["bill_name"], "Comcast")
        self.assertNotIn("mark_paid", str(alias_collision).lower())

    def test_cash_flow_hq_conversational_search_returns_incoming_weekly_remit_details(self) -> None:
        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerDatabase, WeeklyCashPlannerService, active_business_week
        from agents.icr_remit_agent.database import ICRRemitDatabase
        from shared.data_layer.models import RecordType, SourceSystem, Status, SharedRecord
        from shared.data_layer.repository import InMemorySharedRecordRepository
        from decimal import Decimal
        from datetime import date, timedelta

        current_week = active_business_week()
        repository = InMemorySharedRecordRepository()
        repository.upsert(
            SharedRecord(
                id="bill-ndh-remit",
                record_type=RecordType.BILL,
                source_system=SourceSystem.NOTION,
                source_record_id="notion-ndh-remit",
                title=f"Incoming Weekly Remit - {current_week.isoformat()}",
                amount=Decimal("8573.00"),
                effective_date=current_week + timedelta(days=2),
                status=Status.UPCOMING,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            planner_db = WeeklyCashPlannerDatabase(Path(directory) / "planner.sqlite3")
            remit_db = ICRRemitDatabase(Path(directory) / "remit.sqlite3")
            planner = WeeklyCashPlannerService(planner_db.path, remit_db.path)
            planner.record_already_sent_remit(current_week, Decimal("5000.00"), Decimal("1200.00"))
            service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=planner)

            result = service.search("Where did you save the NDH remit?")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["record"]["record_id"], "bill-ndh-remit")
        self.assertEqual(result["record"]["amount"], "8573.00")
        self.assertEqual(result["record"]["status"], "upcoming")
        self.assertEqual(result["record"]["effective_date"], (current_week + timedelta(days=2)).isoformat())
        self.assertEqual(result["record"]["collection"], "Cash Flow HQ")
        self.assertEqual(result["record"]["table"], "Incoming Weekly Remits")

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
            result = service.mark_paid("bill-adp")
            self.assertEqual(result["status"], "ok")
            self.assertIn("updated", result)
            self.assertIn("planner_summary", result)
            updated = result["updated"]
            self.assertEqual(set(updated.keys()), {"record_ref", "bill_name", "amount", "due_date", "current_status"})
            self.assertEqual(updated["current_status"], "paid")

            # Test mark_paid rejects stale expected status before updating
            with self.assertRaises(StaleCashFlowRecord):
                service.mark_paid("bill-review", expected_status="upcoming")
            self.assertEqual(repository.get("bill-review").status, Status.NEEDS_REVIEW)
            
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
            
            # Test planner_summary preserves the existing totals and adds the full planner contract
            summary = service.planner_summary(today=date(2026, 8, 7))
            self.assertEqual(set(summary.keys()), {
                "status",
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
            
            # Verify all values are dollar-formatted strings
            for key in ("operating_cash", "current_week_obligations", "overdue_items_requiring_review", "projected_ending_cash", "already_paid", "reserved_funds_total", "safe_to_spend_cash"):
                self.assertTrue(summary[key].startswith("$"), f"{key} should be dollar-formatted")
            
            # Verify projected_ending_cash is calculated independently (not copied from spendable_cash)
            # It should be operating_cash - current_week_obligations
            from agents.cash_flow_hq.private_bridge_service import _parse_money
            operating = _parse_money(summary["operating_cash"])
            obligations = _parse_money(summary["current_week_obligations"])
            projected = _parse_money(summary["projected_ending_cash"])
            self.assertIsNotNone(operating)
            self.assertIsNotNone(obligations)
            self.assertIsNotNone(projected)
            self.assertEqual(projected, operating - obligations)
            self.assertEqual(summary["current_weekly_remit"], {"week_start": active_business_week().isoformat(), "amount": "$5,000.00"})
            self.assertEqual(summary["jim_remit"], "$1,200.00")
            self.assertEqual(summary["jim_remit_status"], "Open")
            self.assertEqual(summary["reserved_funds_total"], "$0.00")
            self.assertEqual(summary["safe_to_spend_cash"], "$3,800.00")
            self.assertTrue(all(set(item) == {"title", "amount", "status", "due_date"} for item in summary["current_week_obligation_details"]))
            
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

    def test_planner_summary_returns_no_plan_when_no_plan_exists(self) -> None:
        """Test that planner_summary returns a clear no-plan response when no plan exists."""
        from datetime import date
        from decimal import Decimal

        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository

        repository = InMemorySharedRecordRepository()
        repository.upsert(SharedRecord(
            id="bill-1",
            record_type=RecordType.BILL,
            source_system=SourceSystem.NOTION,
            source_record_id="notion-1",
            title="Test Bill",
            amount=Decimal("100.00"),
            effective_date=date(2026, 8, 5),
            status=Status.UPCOMING,
        ))

        class FakePlanner:
            def jason_snapshot(self, _bills=None):
                # Return snapshot with no plan
                return {
                    "plan": None,
                    "operating_cash": "$0.00",
                    "reserved_cash": "$0.00",
                    "spendable_cash": "$0.00",
                    "reservations": [],
                    "bills_due_before_next_remit": [],
                }

        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=FakePlanner())  # type: ignore[arg-type]
        summary = service.planner_summary(today=date(2026, 8, 7))

        # Should return no_plan status
        self.assertEqual(summary["status"], "no_plan")
        self.assertIn("message", summary)
        self.assertIsNone(summary["operating_cash"])
        self.assertIsNone(summary["projected_ending_cash"])

    def test_planner_summary_distinguishes_zero_cash_from_no_plan(self) -> None:
        """Test that a real plan with $0 cash is distinguishable from no plan."""
        from datetime import date
        from decimal import Decimal

        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository

        repository = InMemorySharedRecordRepository()

        class FakePlanner:
            def jason_snapshot(self, _bills=None):
                # Return snapshot with a real plan that has $0 operating cash
                return {
                    "plan": {
                        "week_start": "2026-08-03",
                        "weekly_remit_amount": "$0.00",
                        "jim_remit_amount": "$0.00",
                        "jim_remit_status": "Open",
                    },
                    "operating_cash": "$0.00",
                    "reserved_cash": "$0.00",
                    "spendable_cash": "$0.00",
                    "reservations": [],
                    "bills_due_before_next_remit": [],
                }

        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=FakePlanner())  # type: ignore[arg-type]
        summary = service.planner_summary(today=date(2026, 8, 7))

        # Should return ok status with $0 values (not no_plan)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["operating_cash"], "$0.00")
        self.assertEqual(summary["projected_ending_cash"], "$0.00")

    def test_planner_summary_handles_negative_cash_correctly(self) -> None:
        """Test that legitimate negative cash is preserved."""
        from datetime import date
        from decimal import Decimal

        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
        from shared.data_layer.models import SharedRecord, RecordType, SourceSystem, Status
        from shared.data_layer.repository import InMemorySharedRecordRepository

        repository = InMemorySharedRecordRepository()
        repository.upsert(SharedRecord(
            id="bill-1",
            record_type=RecordType.BILL,
            source_system=SourceSystem.NOTION,
            source_record_id="notion-1",
            title="Expensive Bill",
            amount=Decimal("5000.00"),
            effective_date=date(2026, 8, 5),
            status=Status.UPCOMING,
        ))

        class FakePlanner:
            def jason_snapshot(self, _bills=None):
                # Return snapshot with a plan that has negative operating cash
                return {
                    "plan": {
                        "week_start": "2026-08-03",
                        "weekly_remit_amount": "$1000.00",
                        "jim_remit_amount": "$2000.00",
                        "jim_remit_status": "Open",
                    },
                    "operating_cash": "$-1000.00",
                    "reserved_cash": "$0.00",
                    "spendable_cash": "$-1000.00",
                    "reservations": [],
                    "bills_due_before_next_remit": [
                        {"title": "Expensive Bill", "amount": Decimal("5000.00"), "status": "upcoming", "due_date": date(2026, 8, 5)}
                    ],
                }

        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=repository, planner=FakePlanner())  # type: ignore[arg-type]
        summary = service.planner_summary(today=date(2026, 8, 7))

        # Should return ok status with negative values
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["operating_cash"], "$-1,000.00")
        self.assertEqual(summary["projected_ending_cash"], "$-6,000.00")

    def test_parse_money_returns_none_for_invalid_input(self) -> None:
        """Test that _parse_money returns None for invalid/malformed input."""
        from agents.cash_flow_hq.private_bridge_service import _parse_money

        # Invalid inputs should return None
        self.assertIsNone(_parse_money("invalid"))
        self.assertIsNone(_parse_money(""))
        self.assertIsNone(_parse_money(None))  # type: ignore[arg-type]
        self.assertIsNone(_parse_money("abc123"))
        self.assertIsNone(_parse_money("$"))

    def test_parse_money_handles_valid_inputs(self) -> None:
        """Test that _parse_money correctly parses valid money strings."""
        from agents.cash_flow_hq.private_bridge_service import _parse_money

        # Valid inputs should parse correctly
        self.assertEqual(_parse_money("$0.00"), Decimal("0.00"))
        self.assertEqual(_parse_money("$1,234.56"), Decimal("1234.56"))
        self.assertEqual(_parse_money("$-500.00"), Decimal("-500.00"))
        self.assertEqual(_parse_money("123"), Decimal("123"))
        self.assertEqual(_parse_money("$0"), Decimal("0"))

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

    def test_incoming_weekly_remit_private_bridge_service_uses_cash_flow_helpers(self) -> None:
        from datetime import date
        from decimal import Decimal

        from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService

        class FakePlanner:
            def jason_snapshot(self, _bills=None):
                return {"bills_due_before_next_remit": []}

        class FakeCashFlow:
            def __init__(self) -> None:
                self.updated = []
                self.marked_paid = []
                self.ensured = []
                self.created_payloads = []
                self.created_pages = []
                self.bills = []
                self.notion = self

            def get_existing_foundation(self) -> dict:
                return {"data_source_id": "source-id"}

            def list_cash_flow_bills(self, _data_source_id: str):
                return list(self.bills)

            def create_manual_expense_payload(self, **kwargs):
                self.created_payloads.append(kwargs)
                return {
                    "Expense Name": {"title": [{"type": "text", "text": {"content": kwargs["expense_name"]}}]},
                    "Vendor / Payee": {"rich_text": []},
                    "Amount": {"number": kwargs["amount"]},
                    "Due Date": {"date": {"start": kwargs["due_date"]}},
                    "Status": {"select": {"name": "Upcoming"}},
                    "Payment Type": {"select": {"name": "Manual"}},
                    "Source": {"select": {"name": kwargs["source"]}},
                    "Category": {"select": {"name": kwargs["category"]}},
                }

            def request(self, method, path, json):
                self.created_pages.append((method, path, json))
                return {"id": "page-1", "properties": json["properties"]}

            def update_bill_fields(self, page_id, **kwargs):
                self.updated.append((page_id, kwargs))

            def ensure_payment_confirmation_properties(self, data_source_id):
                self.ensured.append(data_source_id)

            def mark_bill_paid_manually(self, page_id, payment_date, payment_method="Manual", confirmation_link=None, confirmation_subject=None):
                self.marked_paid.append((page_id, payment_date.isoformat(), payment_method, confirmation_subject))

        class Bill:
            def __init__(self, page_id, title, amount, due_date, status="Upcoming", notes="Partner supplied expected deposit"):
                self.page_id = page_id
                self.expense_name = title
                self.amount = amount
                self.due_date = due_date
                self.status = status
                self.notes = notes

        cash_flow = FakeCashFlow()
        service = CashFlowHqPrivateBridgeService(database_path="unused", repository=None, planner=FakePlanner(), cash_flow=cash_flow)

        created = service.create_incoming_weekly_remit(Decimal("8573.00"), today=date(2026, 8, 5))
        self.assertEqual(created["status"], "created")
        self.assertEqual(cash_flow.created_payloads[0]["expense_name"], "Incoming Weekly Remit - 2026-08-03")

        cash_flow.bills = [Bill("page-1", "Incoming Weekly Remit - 2026-08-03", Decimal("8573.00"), date(2026, 8, 5))]
        duplicate = service.create_incoming_weekly_remit(Decimal("8600.00"), today=date(2026, 8, 5))
        self.assertEqual(duplicate["status"], "duplicate")

        replaced = service.create_incoming_weekly_remit(Decimal("8600.00"), replace_existing=True, today=date(2026, 8, 5))
        self.assertEqual(replaced["status"], "updated")
        self.assertEqual(cash_flow.updated[-1][0], "page-1")

        paid = service.mark_incoming_weekly_remit_received(Decimal("8562.91"), today=date(2026, 8, 5))
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(cash_flow.ensured, ["source-id"])
        self.assertEqual(cash_flow.marked_paid[-1][0], "page-1")
