"""Private, allowlisted status bridge for the Jason Railway service."""

from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agents.weekly_remit_agent.approval_service import WeeklyRemitApprovalService
from agents.weekly_remit_agent.config import load_remit_settings


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
    def __init__(self, *, token: str, payment_health_path: Path, voicemail_health_path: Path, weekly_remit_approvals: WeeklyRemitApprovalService | None = None, host: str = "0.0.0.0", port: int = 8091) -> None:
        self.token = token
        self.payment_health_path = payment_health_path
        self.voicemail_health_path = voicemail_health_path
        self.weekly_remit_approvals = weekly_remit_approvals
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

            def do_POST(self) -> None:  # noqa: N802
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if not _token_matches(supplied, bridge.token):
                    logging.warning("payment_status_bridge result=denied")
                    bridge._respond(self, 401, {})
                    return
                if bridge.weekly_remit_approvals is None:
                    bridge._respond(self, 404, {})
                    return
                payload = bridge._request_payload(self)
                user_id = payload.get("authorized_user_id")
                if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
                    bridge._respond(self, 400, {})
                    return
                if self.path == "/internal/weekly-remit/preview":
                    result, preview = bridge.weekly_remit_approvals.create_preview(user_id)
                elif self.path.startswith("/internal/weekly-remit/approvals/"):
                    approval_id = self.path.rsplit("/", 1)[-1]
                    action = payload.get("action")
                    result, preview = (bridge.weekly_remit_approvals.approve(user_id, approval_id) if action == "approve" else bridge.weekly_remit_approvals.cancel(user_id, approval_id) if action == "cancel" else ("invalid", None))
                else:
                    bridge._respond(self, 404, {})
                    return
                logging.info("weekly_remit_approval_bridge result=%s", result)
                bridge._respond(self, 200, {"status": result, "preview": bridge._safe_preview(preview)})

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

    @staticmethod
    def _request_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length < 1 or length > 2048:
                return {}
            value = json.loads(handler.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _safe_preview(preview: Any) -> dict[str, Any] | None:
        if preview is None:
            return None
        return {
            "approval_id": preview.approval_id, "created_at": preview.created_at, "expires_at": preview.expires_at,
            "broker": preview.broker_name, "recipient": preview.recipient_email, "week": preview.week_start,
            "subject": preview.subject, "attachment_filenames": [preview.remit_filename, preview.liquidation_filename],
            "attachment_count": 2, "status": preview.status,
        }

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
        remit_approvals = WeeklyRemitApprovalService(load_remit_settings()) if os.getenv("WEEKLY_REMIT_APPROVAL_BRIDGE_ENABLED", "false").lower() == "true" else None
        return PaymentStatusBridge(
            token=token,
            payment_health_path=payment_health_path,
            voicemail_health_path=voicemail_path,
            weekly_remit_approvals=remit_approvals,
            host=os.getenv("PAYMENT_STATUS_BRIDGE_HOST", "0.0.0.0"),
            port=int(os.getenv("PAYMENT_STATUS_BRIDGE_PORT", "8091")),
        )
    except (OSError, ValueError):
        logging.error("payment_status_bridge result=disabled configuration=invalid")
        return None
