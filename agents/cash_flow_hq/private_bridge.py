from __future__ import annotations

import hmac
import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from shared.config import load_environment

from .private_bridge_service import CashFlowHqPrivateBridgeService


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8092


class CashFlowHqPrivateBridge:
    def __init__(
        self,
        *,
        token: str,
        service: CashFlowHqPrivateBridgeService,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        server_factory: Callable[[tuple[str, int], type[BaseHTTPRequestHandler]], ThreadingHTTPServer] = ThreadingHTTPServer,
    ) -> None:
        self.token = token
        self.service = service
        self.host = host
        self.port = port
        self._server_factory = server_factory
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/internal/health":
                    bridge._respond(self, 404, {})
                    return
                bridge._respond(self, 200, {"status": "running", "service": "cash_flow_hq_bridge"})

            def do_POST(self) -> None:  # noqa: N802
                if not bridge._authorized(self):
                    bridge._respond(self, 401, {})
                    return
                payload = bridge._request_payload(self)
                if self.path == "/internal/cash-flow/incoming-weekly-remit":
                    try:
                        result = bridge.service.create_incoming_weekly_remit(
                            payload.get("amount"),
                            replace_existing=payload.get("replace_existing") is True,
                        )
                    except Exception:
                        logging.exception("cash_flow_hq_bridge result=error endpoint=incoming_weekly_remit")
                        bridge._respond(self, 500, {"status": "error"})
                        return
                elif self.path == "/internal/cash-flow/incoming-weekly-remit/received":
                    result = bridge.service.mark_incoming_weekly_remit_received(payload.get("amount"))
                else:
                    bridge._respond(self, 404, {})
                    return
                logging.info("cash_flow_hq_bridge result=%s", result.get("status", "ok"))
                bridge._respond(self, 200, result)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = self._server_factory((host, port), Handler)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="cash-flow-hq-bridge")
        self._thread.start()
        logging.info("cash_flow_hq_bridge result=started")

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        logging.info("cash_flow_hq_bridge result=stopped")

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
            if length < 1 or length > 4096:
                return {}
            value = json.loads(handler.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        supplied = handler.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(supplied) and hmac.compare_digest(supplied.encode(), self.token.encode())


def from_environment(
    env_file: str | None = None,
    *,
    service_factory: Callable[[Path], CashFlowHqPrivateBridgeService] | None = None,
    server_factory: Callable[[tuple[str, int], type[BaseHTTPRequestHandler]], ThreadingHTTPServer] = ThreadingHTTPServer,
) -> CashFlowHqPrivateBridge | None:
    load_environment(env_file)
    token = os.getenv("CASH_FLOW_HQ_BRIDGE_TOKEN", "").strip()
    if not token:
        logging.error("cash_flow_hq_bridge result=disabled configuration=invalid")
        return None
    host = os.getenv("CASH_FLOW_HQ_BRIDGE_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    try:
        port = int(os.getenv("CASH_FLOW_HQ_BRIDGE_PORT", str(DEFAULT_PORT)))
    except ValueError:
        logging.error("cash_flow_hq_bridge result=disabled configuration=invalid")
        return None
    database_path = Path(os.getenv("SHARED_DATA_DATABASE_PATH", "shared_ucm_data.sqlite3"))
    service = service_factory(database_path) if service_factory is not None else CashFlowHqPrivateBridgeService(str(database_path))
    return CashFlowHqPrivateBridge(token=token, service=service, host=host, port=port, server_factory=server_factory)


def run_bridge(env_file: str | None = None) -> int:
    bridge = from_environment(env_file)
    if bridge is None:
        return 2
    bridge.start()
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: Any) -> None:
        logging.info("cash_flow_hq_bridge result=shutdown signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    return 0


__all__ = ["CashFlowHqPrivateBridge", "from_environment", "run_bridge"]