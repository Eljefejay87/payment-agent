from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from decimal import Decimal

from agents.cash_flow_hq.private_bridge import CashFlowHqPrivateBridge, from_environment
from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService
from shared.data_layer.models import RecordType, SourceSystem, Status, SharedRecord
from shared.data_layer.repository import InMemorySharedRecordRepository
from shared.data_layer.sqlite_repository import SQLiteSharedRecordRepository


class _FakeServer:
    def __init__(self) -> None:
        self.address = ("127.0.0.1", 0)
        self.started = threading.Event()
        self.stopped = threading.Event()

    def serve_forever(self) -> None:
        self.started.set()
        self.stopped.wait(timeout=0.5)

    def shutdown(self) -> None:
        self.stopped.set()

    def server_close(self) -> None:
        self.stopped.set()


class CashFlowHqPrivateBridgeTests(unittest.TestCase):
    def test_bridge_startup_uses_dedicated_env_and_server_factory(self) -> None:
        server = _FakeServer()
        service = object()
        bridge = CashFlowHqPrivateBridge(
            token="bridge-token",
            service=service,  # type: ignore[arg-type]
            host="127.0.0.1",
            port=8092,
            server_factory=lambda _address, _handler: server,
        )

        bridge.start()
        self.assertTrue(server.started.wait(timeout=1))
        bridge.stop()

    def test_bridge_requires_authorization_for_writes_and_exposes_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = InMemorySharedRecordRepository()
            service = CashFlowHqPrivateBridgeService(
                database_path=str(Path(directory) / "shared.sqlite3"),
                repository=repository,
                now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            )
            try:
                bridge = CashFlowHqPrivateBridge(
                    token="bridge-token",
                    service=service,
                    host="127.0.0.1",
                    port=0,
                )
            except PermissionError:
                self.skipTest("Local sandbox does not permit loopback listeners.")

            bridge.start()
            port = bridge.server.server_address[1]
            try:
                health = HTTPConnection("127.0.0.1", port)
                health.request("GET", "/internal/health")
                health_response = health.getresponse()
                self.assertEqual(health_response.status, 200)

                denied = HTTPConnection("127.0.0.1", port)
                denied.request(
                    "POST",
                    "/internal/cash-flow/incoming-weekly-remit",
                    body='{"amount":"8573"}',
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(denied.getresponse().status, 401)
            finally:
                bridge.stop()

    def test_bridge_creates_incoming_weekly_remit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = InMemorySharedRecordRepository()
            service = CashFlowHqPrivateBridgeService(
                database_path=str(Path(directory) / "shared.sqlite3"),
                repository=repository,
                now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            )
            try:
                bridge = CashFlowHqPrivateBridge(
                    token="bridge-token",
                    service=service,
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
                    body='{"amount":"8573","replace_existing":false}',
                    headers={"Authorization": "Bearer bridge-token", "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                payload = response.read().decode()
                self.assertIn('"status":"created"', payload)
                self.assertIn('Incoming Weekly Remit - 2026-08-03', payload)
                self.assertEqual(len(repository.list()), 1)
            finally:
                bridge.stop()

    def test_bridge_create_verifies_row_persisted_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "shared.sqlite3"
            service = CashFlowHqPrivateBridgeService(
                database_path=str(database_path),
                now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            )
            try:
                bridge = CashFlowHqPrivateBridge(
                    token="bridge-token",
                    service=service,
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
                    body='{"amount":"8573","replace_existing":false}',
                    headers={"Authorization": "Bearer bridge-token", "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                payload = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIn('"status":"created"', payload)

                repository = SQLiteSharedRecordRepository(str(database_path))
                saved = repository.get("incoming-weekly-remit-2026-08-03")
                self.assertIsNotNone(saved)
                assert saved is not None
                self.assertEqual(saved.title, "Incoming Weekly Remit - 2026-08-03")
                self.assertEqual(str(saved.amount), "8573")
                self.assertEqual(saved.status, Status.UPCOMING)
            finally:
                bridge.stop()

    def test_bridge_marks_incoming_weekly_remit_received(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = InMemorySharedRecordRepository()
            week_start = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc).date() - timedelta(days=2)
            repository.upsert(
                SharedRecord(
                    id="incoming-weekly-remit-2026-08-03",
                    record_type=RecordType.BILL,
                    source_system=SourceSystem.SQLITE,
                    source_record_id="incoming-weekly-remit:2026-08-03",
                    title="Incoming Weekly Remit - 2026-08-03",
                    effective_date=week_start,
                    status=Status.UPCOMING,
                    amount=Decimal("8573.00"),
                )
            )
            service = CashFlowHqPrivateBridgeService(
                database_path=str(Path(directory) / "shared.sqlite3"),
                repository=repository,
                now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            )
            try:
                bridge = CashFlowHqPrivateBridge(
                    token="bridge-token",
                    service=service,
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
                    headers={"Authorization": "Bearer bridge-token", "Content-Type": "application/json"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                payload = response.read().decode()
                self.assertIn('"status":"paid"', payload)
                record = repository.get("incoming-weekly-remit-2026-08-03")
                self.assertIsNotNone(record)
                self.assertEqual(record.status, Status.PAID)
                self.assertEqual(str(record.amount), "8562.91")
            finally:
                bridge.stop()


if __name__ == "__main__":
    unittest.main()