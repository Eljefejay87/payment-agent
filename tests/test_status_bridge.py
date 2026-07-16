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
