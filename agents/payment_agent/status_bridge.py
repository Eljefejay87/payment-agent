"""Private, allowlisted status bridge for the Jason Railway service."""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService, StaleCashFlowRecordError
from agents.weekly_remit_agent.approval_service import WeeklyRemitApprovalService
from agents.weekly_remit_agent.config import load_remit_settings


_SERVICE = {"not_started", "starting", "running", "stopped", "error", "unreadable", "unknown"}
_GRAPH = {"available", "unavailable", "unknown"}
_VOICEMAIL_ERROR_CATEGORIES = {
    "graph_unavailable",
    "google_sheets_unavailable",
    "teams_unavailable",
    "parse_error",
    "storage_error",
    "runtime_error",
    "unknown",
}
_VOICEMAIL_HEALTH_ALLOWED_FIELDS = {
    "status",
    "last_successful_scan",
    "last_scan_result",
    "records_processed_count",
    "scan_timestamp",
    "last_error_category",
}
_VOICEMAIL_HEALTH_RATE_WINDOW_SECONDS = 60.0
_VOICEMAIL_HEALTH_RATE_LIMIT = 12


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


def _safe_count(value: object) -> int | None:
    return value if isinstance(value, int) and 0 <= value <= 1_000_000 else None


def _safe_voicemail_result(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in {"success", "error", "not_started", "unknown"} else None


def _safe_voicemail_error_category(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in _VOICEMAIL_ERROR_CATEGORIES else None


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
    payload = {
        "service_status": _safe_status(payment.get("service_status", payment.get("status"))),
        "graph_status": _safe_graph(payment.get("graph_status")),
        "attention_required": payment.get("attention_required") is True,
        "last_successful_run": _safe_timestamp(payment.get("last_successful_run")),
        "last_successful_job": _safe_job(payment.get("last_successful_job")),
        "voicemail_status": _safe_status(voicemail.get("status")),
        "voicemail_last_successful_scan": _safe_timestamp(voicemail.get("last_successful_scan")),
        "voicemail_last_successful_job": _safe_job(voicemail.get("last_successful_job")),
    }
    for key, value in {
        "voicemail_last_records_processed": _safe_count(voicemail.get("last_records_processed")),
        "voicemail_last_scan_result": _safe_voicemail_result(
            (voicemail.get("last_scan_result") or {}).get("status")
            if isinstance(voicemail.get("last_scan_result"), dict)
            else voicemail.get("last_run_result")
        ),
        "voicemail_last_error_category": _safe_voicemail_error_category(voicemail.get("last_error_category")),
    }.items():
        if value is not None:
            payload[key] = value
    return payload


def sanitize_voicemail_health_update(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Validate the write contract for standalone Voicemail Tracker health sync."""
    if set(payload) - _VOICEMAIL_HEALTH_ALLOWED_FIELDS:
        return None
    status = _safe_status(payload.get("status"))
    scan_result = _safe_voicemail_result(payload.get("last_scan_result"))
    scan_timestamp = _safe_timestamp(payload.get("scan_timestamp"))
    records_processed = _safe_count(payload.get("records_processed_count"))
    if status == "unknown" or scan_result is None or scan_timestamp is None or records_processed is None:
        return None
    sanitized: dict[str, Any] = {
        "service": "voicemail_tracker_agent",
        "status": status,
        "last_records_processed": records_processed,
        "last_scan_result": {
            "status": scan_result,
            "records_processed": records_processed,
            "scan_timestamp": scan_timestamp,
        },
        "updated_at": scan_timestamp,
    }
    if scan_result == "success":
        last_success = _safe_timestamp(payload.get("last_successful_scan")) or scan_timestamp
        sanitized["last_successful_scan"] = last_success
        sanitized["last_successful_job"] = "scan_once"
        sanitized["last_error_category"] = None
    else:
        sanitized["last_error_category"] = _safe_voicemail_error_category(payload.get("last_error_category")) or "unknown"
    return sanitized


def persist_voicemail_health_update(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _token_matches(value: str, expected: str) -> bool:
    return bool(value) and bool(expected) and hmac.compare_digest(value.encode(), expected.encode())


def _decimal_payload_value(value: Any) -> Decimal | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        return None


class PaymentStatusBridge:
    def __init__(
        self,
        *,
        token: str,
        cash_flow_mutation_token: str = "",
        payment_health_path: Path,
        voicemail_health_path: Path,
        weekly_remit_approvals: WeeklyRemitApprovalService | None = None,
        cash_flow_hq_service: CashFlowHqPrivateBridgeService | None = None,
        host: str = "0.0.0.0",
        port: int = 8091,
    ) -> None:
        self.token = token
        self.cash_flow_mutation_token = cash_flow_mutation_token or token
        self.payment_health_path = payment_health_path
        self.voicemail_health_path = voicemail_health_path
        self.weekly_remit_approvals = weekly_remit_approvals
        self.cash_flow_hq_service = cash_flow_hq_service
        self._voicemail_health_update_times: list[float] = []
        self._voicemail_health_rate_lock = threading.Lock()
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
                expected_token = (
                    bridge.cash_flow_mutation_token
                    if self.path == "/internal/cash-flow/mark-paid"
                    else bridge.token
                )
                if not _token_matches(supplied, expected_token):
                    logging.warning("payment_status_bridge result=denied")
                    bridge._respond(self, 401, {})
                    return
                payload = bridge._request_payload(self)

                if self.path == "/internal/voicemail/health":
                    if bridge._voicemail_health_rate_limited():
                        logging.warning("voicemail_health_bridge result=rate_limited")
                        bridge._respond(self, 429, {"status": "rate_limited"})
                        return
                    sanitized = sanitize_voicemail_health_update(payload)
                    if sanitized is None:
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    try:
                        persist_voicemail_health_update(bridge.voicemail_health_path, sanitized)
                    except OSError:
                        logging.warning("voicemail_health_bridge result=write_failed")
                        bridge._respond(self, 500, {"status": "error"})
                        return
                    logging.info("voicemail_health_bridge result=updated")
                    bridge._respond(self, 200, {"status": "ok"})
                    return

                if self.path == "/internal/cash-flow/search":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    query = payload.get("query")
                    if not isinstance(query, str) or not query.strip():
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    query = query.strip()
                    try:
                        result = bridge.cash_flow_hq_service.search(query)
                        bridge._respond(self, 200, result)
                    except Exception:
                        logging.exception("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/mark-paid":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    record_ref = payload.get("record_ref")
                    expected_status = payload.get("expected_status")
                    if (
                        not isinstance(record_ref, str)
                        or not record_ref.strip()
                        or not isinstance(expected_status, str)
                        or not expected_status.strip()
                        or len(expected_status) > 64
                    ):
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    try:
                        result = bridge.cash_flow_hq_service.mark_paid(record_ref, expected_status)
                        bridge._respond(self, 200, result)
                    except KeyError:
                        logging.warning("cash_flow_hq_bridge result=unknown_record")
                        bridge._respond(self, 404, {"status": "unknown_record"})
                    except StaleCashFlowRecordError:
                        logging.warning("cash_flow_hq_bridge result=stale_record")
                        bridge._respond(self, 409, {"status": "stale_record"})
                    except ValueError:
                        logging.warning("cash_flow_hq_bridge result=replayed_mutation")
                        bridge._respond(self, 409, {"status": "replayed_mutation"})
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/bills":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    scope = payload.get("scope")
                    if not isinstance(scope, str) or not scope.strip():
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    try:
                        bridge._respond(self, 200, bridge.cash_flow_hq_service.list_bills(scope))
                    except ValueError:
                        bridge._respond(self, 400, {"status": "invalid"})
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/planner-summary":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    try:
                        bridge._respond(self, 200, {"status": "ok", "planner_summary": bridge.cash_flow_hq_service.planner_summary()})
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/jim-remit/current":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    try:
                        result = bridge.cash_flow_hq_service.current_week_jim_remit()
                        bridge._respond(self, 200, result)
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/jim-remit/mark-paid":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    expected_week_id = payload.get("expected_week_id")
                    expected_week_start = payload.get("expected_week_start")
                    expected_status = payload.get("expected_status")
                    expected_amount = _decimal_payload_value(payload.get("expected_amount"))
                    if (
                        not isinstance(expected_week_id, str)
                        or not expected_week_id.strip()
                        or not isinstance(expected_week_start, str)
                        or not expected_week_start.strip()
                        or expected_amount is None
                        or not isinstance(expected_status, str)
                        or not expected_status.strip()
                    ):
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    try:
                        result = bridge.cash_flow_hq_service.mark_current_week_jim_remit_paid(
                            expected_week_id=expected_week_id,
                            expected_week_start=expected_week_start,
                            expected_amount=expected_amount,
                            expected_status=expected_status,
                        )
                        bridge._respond(self, 200, result)
                    except KeyError:
                        logging.warning("cash_flow_hq_bridge result=unknown_jim_remit")
                        bridge._respond(self, 404, {"status": "unknown_record"})
                    except StaleCashFlowRecordError:
                        logging.warning("cash_flow_hq_bridge result=stale_jim_remit")
                        bridge._respond(self, 409, {"status": "stale_record"})
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/incoming-weekly-remit":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    amount = _decimal_payload_value(payload.get("amount"))
                    if amount is None or amount <= 0:
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    try:
                        result = bridge.cash_flow_hq_service.create_incoming_weekly_remit(
                            amount,
                            replace_existing=payload.get("replace_existing") is True,
                        )
                        bridge._respond(self, 200, result)
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if self.path == "/internal/cash-flow/incoming-weekly-remit/received":
                    if bridge.cash_flow_hq_service is None:
                        bridge._respond(self, 404, {"status": "unavailable"})
                        return
                    amount_value = payload.get("amount")
                    amount = _decimal_payload_value(amount_value) if amount_value not in {None, ""} else None
                    if amount_value not in {None, ""} and (amount is None or amount <= 0):
                        bridge._respond(self, 400, {"status": "invalid"})
                        return
                    try:
                        result = bridge.cash_flow_hq_service.mark_incoming_weekly_remit_received(amount)
                        bridge._respond(self, 200, result)
                    except Exception:
                        logging.warning("cash_flow_hq_bridge result=error")
                        bridge._respond(self, 400, {"status": "error"})
                    return

                if bridge.weekly_remit_approvals is None:
                    bridge._respond(self, 404, {})
                    return
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

    def _voicemail_health_rate_limited(self) -> bool:
        now = time.monotonic()
        cutoff = now - _VOICEMAIL_HEALTH_RATE_WINDOW_SECONDS
        with self._voicemail_health_rate_lock:
            self._voicemail_health_update_times = [
                recorded_at for recorded_at in self._voicemail_health_update_times if recorded_at >= cutoff
            ]
            if len(self._voicemail_health_update_times) >= _VOICEMAIL_HEALTH_RATE_LIMIT:
                return True
            self._voicemail_health_update_times.append(now)
            return False

    def start(self) -> None:
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
    cash_flow_service = None
    try:
        database_path = os.getenv("SHARED_DATA_DATABASE_PATH")
        if database_path:
            cash_flow_service = CashFlowHqPrivateBridgeService(database_path)
        remit_approvals = WeeklyRemitApprovalService(load_remit_settings()) if os.getenv("WEEKLY_REMIT_APPROVAL_BRIDGE_ENABLED", "false").lower() == "true" else None
        return PaymentStatusBridge(
            token=token,
            cash_flow_mutation_token=token,
            payment_health_path=payment_health_path,
            voicemail_health_path=voicemail_path,
            weekly_remit_approvals=remit_approvals,
            cash_flow_hq_service=cash_flow_service,
            host=os.getenv("PAYMENT_STATUS_BRIDGE_HOST", "0.0.0.0"),
            port=int(os.getenv("PAYMENT_STATUS_BRIDGE_PORT", "8091")),
        )
    except (OSError, ValueError):
        logging.error("payment_status_bridge result=disabled configuration=invalid")
        return None
