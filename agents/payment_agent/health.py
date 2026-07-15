from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentAgentHealth:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "service": "payment_agent",
                "status": "not_started",
                "last_successful_run": None,
                "last_error": None,
            }
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "service": "payment_agent",
                "status": "unreadable",
                "last_successful_run": None,
                "last_error": f"Health file could not be read: {exc}",
            }

    def mark_starting(self) -> None:
        self._write(status="starting", service_status="starting", graph_status="unknown")

    def mark_running(self) -> None:
        self._write(status="running", service_status="running")

    def mark_success(self, job_name: str) -> None:
        now = utc_now()
        self._write(
            status="running",
            last_successful_run=now,
            last_successful_job=job_name,
            last_error=None,
            last_error_at=None,
            service_status="running",
            graph_status="available",
            attention_required=False,
            attention_message=None,
        )

    def mark_error(self, job_name: str, error: Exception) -> None:
        self._write(
            status="error",
            last_failed_job=job_name,
            last_error=_safe_error_message(error),
            last_error_at=utc_now(),
            service_status="running",
        )

    def mark_graph_authentication_error(self, job_name: str) -> None:
        self._write(
            status="error",
            last_failed_job=job_name,
            last_error="Microsoft Graph authentication is unavailable.",
            last_error_at=utc_now(),
            service_status="running",
            graph_status="unavailable",
            attention_required=True,
            attention_message="Microsoft Graph authentication requires attention.",
        )

    def mark_stopped(self) -> None:
        self._write(status="stopped", service_status="stopped", stopped_at=utc_now())

    def _write(self, **updates: Any) -> None:
        current = self.read()
        payload = {
            "service": "payment_agent",
            "status": current.get("status", "unknown"),
            "last_successful_run": current.get("last_successful_run"),
            "last_successful_job": current.get("last_successful_job"),
            "last_error": current.get("last_error"),
            "last_error_at": current.get("last_error_at"),
            "last_failed_job": current.get("last_failed_job"),
            "service_status": current.get("service_status", current.get("status", "unknown")),
            "graph_status": current.get("graph_status", "unknown"),
            "attention_required": bool(current.get("attention_required", False)),
            "attention_message": current.get("attention_message"),
            "updated_at": utc_now(),
            "pid": os.getpid(),
        }
        payload.update(updates)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


def _safe_error_message(error: Exception) -> str:
    return f"{type(error).__name__}: job failed."
