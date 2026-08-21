from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from agents.voicemail_health_ingress.main import (
    PaymentAgentVoicemailHealthClient,
    VoicemailHealthIngress,
    load_settings,
    sanitize_payload,
    validate_settings,
)


VALID_PAYLOAD = {
    "status": "running",
    "last_successful_scan": "2026-08-21T13:00:00+00:00",
    "last_scan_result": "success",
    "records_processed_count": 2,
    "scan_timestamp": "2026-08-21T13:00:00+00:00",
}


class FakeForwarder:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.payloads: list[dict] = []

    def publish(self, payload: dict) -> bool:
        self.payloads.append(payload)
        return self.succeeds


class VoicemailHealthIngressTests(unittest.TestCase):
    def _start_ingress(self, forwarder: FakeForwarder | None = None):
        try:
            ingress = VoicemailHealthIngress(
                public_token="public-token",
                forwarder=forwarder or FakeForwarder(),
                host="127.0.0.1",
                port=0,
            )
        except PermissionError:
            self.skipTest("Local sandbox does not permit loopback listeners.")
        ingress.start()
        return ingress, ingress.server.server_address[1]

    def _post(self, port: int, body: str, *, token: str = "public-token", path: str = "/internal/voicemail/health"):
        conn = HTTPConnection("127.0.0.1", port)
        conn.request(
            "POST",
            path,
            body=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read().decode()

    def test_authorized_post_forwards_sanitized_payload(self) -> None:
        forwarder = FakeForwarder()
        ingress, port = self._start_ingress(forwarder)
        try:
            status, body = self._post(port, json.dumps(VALID_PAYLOAD))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"status": "ok"})
            self.assertEqual(forwarder.payloads, [VALID_PAYLOAD])
        finally:
            ingress.stop()

    def test_invalid_token_unknown_path_and_get_are_rejected(self) -> None:
        forwarder = FakeForwarder()
        ingress, port = self._start_ingress(forwarder)
        try:
            denied_status, _ = self._post(port, json.dumps(VALID_PAYLOAD), token="wrong")
            self.assertEqual(denied_status, 401)

            wrong_path_status, _ = self._post(port, json.dumps(VALID_PAYLOAD), path="/internal/status")
            self.assertEqual(wrong_path_status, 404)

            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/internal/voicemail/health", headers={"Authorization": "Bearer public-token"})
            self.assertEqual(conn.getresponse().status, 405)
            self.assertEqual(forwarder.payloads, [])
        finally:
            ingress.stop()

    def test_unknown_payload_fields_malformed_json_and_oversized_bodies_are_rejected(self) -> None:
        forwarder = FakeForwarder()
        ingress, port = self._start_ingress(forwarder)
        try:
            payload = dict(VALID_PAYLOAD)
            payload["transcript"] = "private voicemail"
            unknown_status, _ = self._post(port, json.dumps(payload))
            self.assertEqual(unknown_status, 400)

            malformed_status, _ = self._post(port, "{")
            self.assertEqual(malformed_status, 400)

            oversized_status, _ = self._post(port, json.dumps({"padding": "x" * 3000}))
            self.assertEqual(oversized_status, 400)
            self.assertEqual(forwarder.payloads, [])
        finally:
            ingress.stop()

    def test_rate_limit_rejects_repeated_posts(self) -> None:
        ingress, port = self._start_ingress(FakeForwarder())
        try:
            for _ in range(12):
                status, _ = self._post(port, json.dumps(VALID_PAYLOAD))
                self.assertEqual(status, 200)
            limited_status, body = self._post(port, json.dumps(VALID_PAYLOAD))
            self.assertEqual(limited_status, 429)
            self.assertIn("rate_limited", body)
        finally:
            ingress.stop()

    def test_internal_payment_agent_failure_is_sanitized(self) -> None:
        forwarder = FakeForwarder(succeeds=False)
        ingress, port = self._start_ingress(forwarder)
        try:
            status, body = self._post(port, json.dumps(VALID_PAYLOAD))
            self.assertEqual(status, 502)
            self.assertEqual(json.loads(body), {"status": "unavailable"})
        finally:
            ingress.stop()

    def test_no_token_or_raw_body_logging(self) -> None:
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers = [handler]
        root.setLevel(logging.INFO)
        forwarder = FakeForwarder(succeeds=False)
        ingress, port = self._start_ingress(forwarder)
        try:
            payload = dict(VALID_PAYLOAD)
            payload["last_error_category"] = "graph_unavailable"
            self._post(port, json.dumps(payload), token="public-token")
            self._post(port, json.dumps(payload), token="wrong-secret")
        finally:
            ingress.stop()
            root.handlers = original_handlers
        text = logs.getvalue()
        self.assertNotIn("public-token", text)
        self.assertNotIn("wrong-secret", text)
        self.assertNotIn("graph_unavailable", text)
        self.assertNotIn("2026-08-21T13:00:00", text)

    def test_sanitize_payload_rejects_sensitive_fields_and_preserves_allowlist(self) -> None:
        self.assertEqual(sanitize_payload(VALID_PAYLOAD), VALID_PAYLOAD)
        sensitive = dict(VALID_PAYLOAD)
        sensitive.update({"caller_phone": "555-1212", "message_id": "secret"})
        self.assertIsNone(sanitize_payload(sensitive))

    def test_private_forwarding_contract_uses_payment_status_bridge_token(self) -> None:
        sent = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def fake_urlopen(request, timeout):
            sent["url"] = request.full_url
            sent["headers"] = dict(request.header_items())
            sent["body"] = request.data.decode()
            sent["timeout"] = timeout
            return FakeResponse()

        client = PaymentAgentVoicemailHealthClient(
            bridge_url="http://payment-agent.railway.internal:8091",
            bridge_token="internal-token",
            timeout_seconds=3,
            urlopen=fake_urlopen,
        )
        self.assertTrue(client.publish(VALID_PAYLOAD))
        self.assertEqual(sent["url"], "http://payment-agent.railway.internal:8091/internal/voicemail/health")
        self.assertIn("Bearer internal-token", sent["headers"]["Authorization"])
        self.assertEqual(json.loads(sent["body"]), VALID_PAYLOAD)
        self.assertEqual(sent["timeout"], 3)

    def test_config_requires_separate_public_and_private_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "\n".join(
                    [
                        "VOICEMAIL_HEALTH_INGRESS_TOKEN=public-token",
                        "PAYMENT_STATUS_BRIDGE_URL=http://payment-agent.railway.internal:8091",
                        "PAYMENT_STATUS_BRIDGE_TOKEN=internal-token",
                        "PORT=8080",
                    ]
                )
            )
            with patch.dict("os.environ", {}, clear=True):
                settings = load_settings(str(env))
        self.assertEqual(validate_settings(settings), [])
        self.assertEqual(settings.public_token, "public-token")
        self.assertEqual(settings.payment_status_bridge_token, "internal-token")

    def test_config_rejects_public_forwarding_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "\n".join(
                    [
                        "VOICEMAIL_HEALTH_INGRESS_TOKEN=public-token",
                        "PAYMENT_STATUS_BRIDGE_URL=https://payment-agent.example.com",
                        "PAYMENT_STATUS_BRIDGE_TOKEN=internal-token",
                    ]
                )
            )
            with patch.dict("os.environ", {}, clear=True):
                settings = load_settings(str(env))
        self.assertIn("PAYMENT_STATUS_BRIDGE_URL must use Railway private HTTP networking.", validate_settings(settings))


if __name__ == "__main__":
    unittest.main()
