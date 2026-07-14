from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoicemailHealth:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "service": "voicemail_tracker_agent",
                "status": "not_started",
                "last_successful_scan": None,
                "last_error": None,
            }
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "service": "voicemail_tracker_agent",
                "status": "unreadable",
                "last_successful_scan": None,
                "last_error": f"Health file could not be read: {exc}",
            }

    def mark_starting(self) -> None:
        self._write(status="starting")

    def mark_running(self) -> None:
        self._write(status="running")

    def mark_success(self, job_name: str, records_processed: int = 0) -> None:
        now = utc_now()
        self._write(
            status="running",
            last_successful_scan=now,
            last_successful_job=job_name,
            last_records_processed=records_processed,
            last_error=None,
            last_error_at=None,
        )

    def mark_error(self, job_name: str, error: Exception) -> None:
        self._write(
            status="error",
            last_failed_job=job_name,
            last_error=str(error),
            last_error_at=utc_now(),
        )

    def mark_stopped(self) -> None:
        self._write(status="stopped", stopped_at=utc_now())

    def _write(self, **updates: Any) -> None:
        current = self.read()
        payload = {
            "service": "voicemail_tracker_agent",
            "status": current.get("status", "unknown"),
            "last_successful_scan": current.get("last_successful_scan"),
            "last_successful_job": current.get("last_successful_job"),
            "last_records_processed": current.get("last_records_processed", 0),
            "last_error": current.get("last_error"),
            "last_error_at": current.get("last_error_at"),
            "last_failed_job": current.get("last_failed_job"),
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
