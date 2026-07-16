"""Private, allowlisted status bridge for the Jason Railway service."""

from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


_SERVICE = {"not_started", "starting", "running", "stopped", "error", "unreadable", "unknown"}
_GRAPH = {"available", "unavailable", "unknown"}


def _safe_status(value: object) -> str:
    value = str(value or "unknown").lower()
    return value if value in _SERVICE else "unknown"


def _safe_graph(value: object) -> str:
    value = str(value or "unknown").lower()
    return value if value in _GRAPH else "unknown"


def _safe_timestamp(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 64 and "T" in value else None


def _safe_job(value: object) -> str | None:
    return value if isinstance(value, str) and value.replace("_", "").isalpha() and len(value) <= 64 else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_status_payload(payment_health_path: Path, voicemail_health_path: Path) -> dict[str, Any]:
    """Return only the explicit Jason contract; never forward health-file errors."""
    payment = _read_json(payment_health_path)
    voicemail = _read_json(voicemail_health_path)
    return {
        "service_status": _safe_status(payment.get("service_status", payment.get("status"))),
        "graph_status": _safe_graph(payment.get("graph_status")),
        "attention_required": payment.get("attention_required") is True,
        "last_successful_run": _safe_timestamp(payment.get("last_successful_run")),
        "last_successful_job": _safe_job(payment.get("last_successful_job")),
        "voicemail_status": _safe_status(voicemail.get("status")),
        "voicemail_last_successful_scan": _safe_timestamp(voicemail.get("last_successful_scan")),
        "voicemail_last_successful_job": _safe_job(voicemail.get("last_successful_job")),
    }


def _token_matches(value: str, expected: str) -> bool:
    return bool(value) and bool(expected) and hmac.compare_digest(value.encode(), expected.encode())


class PaymentStatusBridge:
    def __init__(self, *, token: str, payment_health_path: Path, voicemail_health_path: Path, host: str = "0.0.0.0", port: int = 8091) -> None:
        self.token = token
        self.payment_health_path = payment_health_path
        self.voicemail_health_path = voicemail_health_path
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/internal/status":
                    bridge._respond(self, 404, {})
                    return
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if not _token_matches(supplied, bridge.token):
                    logging.warning("payment_status_bridge result=denied")
                    bridge._respond(self, 401, {})
                    return
                logging.info("payment_status_bridge result=ok")
                bridge._respond(self, 200, build_status_payload(bridge.payment_health_path, bridge.voicemail_health_path))

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer((host, port), Handler)

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def start(self) -> None:
        import threading
        threading.Thread(target=self.server.serve_forever, daemon=True, name="payment-status-bridge").start()
        logging.info("payment_status_bridge result=started")

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        logging.info("payment_status_bridge result=stopped")


def from_environment(payment_health_path: Path) -> PaymentStatusBridge | None:
    if os.getenv("PAYMENT_STATUS_BRIDGE_ENABLED", "false").lower() != "true":
        return None
    token = os.getenv("PAYMENT_STATUS_BRIDGE_TOKEN", "")
    if not token:
        logging.error("payment_status_bridge result=disabled configuration=invalid")
        return None
    voicemail_path = Path(os.getenv("VOICEMAIL_HEALTH_PATH", "/data/voicemail_health.json"))
    try:
        return PaymentStatusBridge(
            token=token,
            payment_health_path=payment_health_path,
            voicemail_health_path=voicemail_path,
            host=os.getenv("PAYMENT_STATUS_BRIDGE_HOST", "0.0.0.0"),
            port=int(os.getenv("PAYMENT_STATUS_BRIDGE_PORT", "8091")),
        )
    except (OSError, ValueError):
        logging.error("payment_status_bridge result=disabled configuration=invalid")
        return None
