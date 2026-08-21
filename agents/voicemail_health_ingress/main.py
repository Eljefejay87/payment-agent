from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urljoin

from shared.config import load_environment
from shared.logging import configure_logging


PUBLIC_PATH = "/internal/voicemail/health"
MAX_BODY_BYTES = 2048
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_REQUESTS = 12
ALLOWED_FIELDS = {
    "status",
    "last_successful_scan",
    "last_scan_result",
    "records_processed_count",
    "scan_timestamp",
    "last_error_category",
}
SERVICE_STATUSES = {"not_started", "starting", "running", "stopped", "error", "unreadable", "unknown"}
SCAN_RESULTS = {"success", "error", "not_started", "unknown"}
ERROR_CATEGORIES = {
    "graph_unavailable",
    "google_sheets_unavailable",
    "teams_unavailable",
    "parse_error",
    "storage_error",
    "runtime_error",
    "unknown",
}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    public_token: str
    payment_status_bridge_url: str
    payment_status_bridge_token: str
    forward_timeout_seconds: float


class PaymentAgentVoicemailHealthClient:
    """Forward sanitized voicemail health to the private Payment Agent bridge."""

    def __init__(
        self,
        *,
        bridge_url: str,
        bridge_token: str,
        timeout_seconds: float = 5.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.bridge_url = bridge_url.rstrip("/")
        self.bridge_token = bridge_token
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    def publish(self, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            urljoin(f"{self.bridge_url}/", PUBLIC_PATH.lstrip("/")),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.bridge_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= int(getattr(response, "status", 500)) < 300
        except (OSError, urllib.error.URLError, urllib.error.HTTPError):
            return False


class VoicemailHealthIngress:
    """Expose one public route and forward only sanitized aggregate health."""

    def __init__(
        self,
        *,
        public_token: str,
        forwarder: PaymentAgentVoicemailHealthClient,
        host: str = "0.0.0.0",
        port: int = 8080,
        server_factory: Callable[[tuple[str, int], type[BaseHTTPRequestHandler]], ThreadingHTTPServer] = ThreadingHTTPServer,
    ) -> None:
        self.public_token = public_token
        self.forwarder = forwarder
        self._rate_lock = threading.Lock()
        self._request_times: list[float] = []
        ingress = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "UCMVoicemailHealthIngress"
            sys_version = ""

            def do_POST(self) -> None:  # noqa: N802
                if self.path != PUBLIC_PATH:
                    ingress._respond(self, 404, {"status": "not_found"})
                    return
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if not _token_matches(supplied, ingress.public_token):
                    logging.warning("voicemail_health_ingress result=denied")
                    ingress._respond(self, 401, {"status": "unauthorized"})
                    return
                if ingress._rate_limited():
                    logging.warning("voicemail_health_ingress result=rate_limited")
                    ingress._respond(self, 429, {"status": "rate_limited"})
                    return
                payload = ingress._request_payload(self)
                sanitized = sanitize_payload(payload)
                if sanitized is None:
                    ingress._respond(self, 400, {"status": "invalid"})
                    return
                if not ingress.forwarder.publish(sanitized):
                    logging.warning("voicemail_health_ingress result=forward_failed")
                    ingress._respond(self, 502, {"status": "unavailable"})
                    return
                logging.info("voicemail_health_ingress result=forwarded")
                ingress._respond(self, 200, {"status": "ok"})

            def do_GET(self) -> None:  # noqa: N802
                ingress._reject_method(self)

            def do_PUT(self) -> None:  # noqa: N802
                ingress._reject_method(self)

            def do_PATCH(self) -> None:  # noqa: N802
                ingress._reject_method(self)

            def do_DELETE(self) -> None:  # noqa: N802
                ingress._reject_method(self)

            def do_OPTIONS(self) -> None:  # noqa: N802
                ingress._reject_method(self)

            def do_HEAD(self) -> None:  # noqa: N802
                ingress._reject_method(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = server_factory((host, port), Handler)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="voicemail-health-ingress")
        self._thread.start()
        logging.info("voicemail_health_ingress result=started")

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        logging.info("voicemail_health_ingress result=stopped")

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        with self._rate_lock:
            self._request_times = [recorded_at for recorded_at in self._request_times if recorded_at >= cutoff]
            if len(self._request_times) >= RATE_LIMIT_REQUESTS:
                return True
            self._request_times.append(now)
            return False

    @staticmethod
    def _request_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                return {}
            value = json.loads(handler.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _reject_method(self, handler: BaseHTTPRequestHandler) -> None:
        self._respond(handler, 405 if handler.path == PUBLIC_PATH else 404, {"status": "not_allowed"})


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if set(payload) - ALLOWED_FIELDS:
        return None
    status = _safe_status(payload.get("status"))
    scan_result = _safe_scan_result(payload.get("last_scan_result"))
    scan_timestamp = _safe_timestamp(payload.get("scan_timestamp"))
    records_processed = _safe_count(payload.get("records_processed_count"))
    if status is None or scan_result is None or scan_timestamp is None or records_processed is None:
        return None
    sanitized: dict[str, Any] = {
        "status": status,
        "last_scan_result": scan_result,
        "records_processed_count": records_processed,
        "scan_timestamp": scan_timestamp,
    }
    last_successful_scan = _safe_timestamp(payload.get("last_successful_scan"))
    if last_successful_scan is not None:
        sanitized["last_successful_scan"] = last_successful_scan
    error_category = _safe_error_category(payload.get("last_error_category"))
    if error_category is not None:
        sanitized["last_error_category"] = error_category
    return sanitized


def load_settings(env_file: str | None = None) -> Settings:
    load_environment(env_file)
    return Settings(
        host=os.getenv("VOICEMAIL_HEALTH_INGRESS_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=int(os.getenv("PORT", os.getenv("VOICEMAIL_HEALTH_INGRESS_PORT", "8080"))),
        public_token=os.getenv("VOICEMAIL_HEALTH_INGRESS_TOKEN", "").strip(),
        payment_status_bridge_url=os.getenv("PAYMENT_STATUS_BRIDGE_URL", "").strip(),
        payment_status_bridge_token=os.getenv("PAYMENT_STATUS_BRIDGE_TOKEN", "").strip(),
        forward_timeout_seconds=float(os.getenv("VOICEMAIL_HEALTH_INGRESS_FORWARD_TIMEOUT_SECONDS", "5")),
    )


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    if not settings.public_token:
        errors.append("VOICEMAIL_HEALTH_INGRESS_TOKEN is required.")
    if not settings.payment_status_bridge_url:
        errors.append("PAYMENT_STATUS_BRIDGE_URL is required.")
    if not settings.payment_status_bridge_token:
        errors.append("PAYMENT_STATUS_BRIDGE_TOKEN is required.")
    if not settings.payment_status_bridge_url.startswith("http://"):
        errors.append("PAYMENT_STATUS_BRIDGE_URL must use Railway private HTTP networking.")
    if settings.forward_timeout_seconds <= 0 or settings.forward_timeout_seconds > 30:
        errors.append("VOICEMAIL_HEALTH_INGRESS_FORWARD_TIMEOUT_SECONDS must be between 0 and 30.")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="UCM Voicemail Health public ingress")
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    configure_logging(os.getenv("LOG_LEVEL", "INFO").upper())
    errors = validate_settings(settings)
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    forwarder = PaymentAgentVoicemailHealthClient(
        bridge_url=settings.payment_status_bridge_url,
        bridge_token=settings.payment_status_bridge_token,
        timeout_seconds=settings.forward_timeout_seconds,
    )
    ingress = VoicemailHealthIngress(
        public_token=settings.public_token,
        forwarder=forwarder,
        host=settings.host,
        port=settings.port,
    )
    ingress.start()
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: Any) -> None:
        logging.info("voicemail_health_ingress result=shutdown signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        ingress.stop()
    return 0


def _token_matches(value: str, expected: str) -> bool:
    return bool(value) and bool(expected) and hmac.compare_digest(value.encode(), expected.encode())


def _safe_status(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in SERVICE_STATUSES and text != "unknown" else None


def _safe_scan_result(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in SCAN_RESULTS else None


def _safe_timestamp(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 64 and "T" in value else None


def _safe_count(value: object) -> int | None:
    return value if isinstance(value, int) and 0 <= value <= 1_000_000 else None


def _safe_error_category(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in ERROR_CATEGORIES else None


if __name__ == "__main__":
    raise SystemExit(main())
