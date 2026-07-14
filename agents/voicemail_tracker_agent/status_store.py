"""Atomic, non-sensitive local status persistence for Voicemail Tracker."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .models import VoicemailRunOutcome, VoicemailStatusSnapshot


SCHEMA_VERSION = 1
STATUS_FIELDS = {
    "schema_version",
    "last_attempted_run",
    "last_successful_run",
    "last_run_outcome",
    "pending_callback_count",
    "total_records_processed",
    "last_error_message",
}


class VoicemailStatusStateError(RuntimeError):
    """Raised when a persisted status snapshot cannot be safely read."""


class VoicemailStatusStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def read(self) -> VoicemailStatusSnapshot | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return _snapshot_from_dict(payload)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise VoicemailStatusStateError("Voicemail status snapshot is corrupt or unreadable.") from exc

    def write(self, snapshot: VoicemailStatusSnapshot) -> None:
        """Atomically replace the snapshot without exposing sensitive voicemail data."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_snapshot_to_dict(snapshot), indent=2, sort_keys=True) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _snapshot_to_dict(snapshot: VoicemailStatusSnapshot) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "last_attempted_run": snapshot.last_attempted_run.isoformat(),
        "last_successful_run": (
            snapshot.last_successful_run.isoformat() if snapshot.last_successful_run else None
        ),
        "last_run_outcome": snapshot.last_run_outcome.value,
        "pending_callback_count": snapshot.pending_callback_count,
        "total_records_processed": snapshot.total_records_processed,
        "last_error_message": snapshot.last_error_message,
    }


def _snapshot_from_dict(payload: object) -> VoicemailStatusSnapshot:
    if not isinstance(payload, dict) or set(payload) != STATUS_FIELDS:
        raise ValueError("Unexpected voicemail status fields.")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported voicemail status schema version.")
    return VoicemailStatusSnapshot(
        last_attempted_run=datetime.fromisoformat(str(payload["last_attempted_run"])),
        last_successful_run=(
            datetime.fromisoformat(str(payload["last_successful_run"]))
            if payload["last_successful_run"]
            else None
        ),
        last_run_outcome=VoicemailRunOutcome(str(payload["last_run_outcome"])),
        pending_callback_count=(
            int(payload["pending_callback_count"])
            if payload["pending_callback_count"] is not None
            else None
        ),
        total_records_processed=int(payload["total_records_processed"]),
        last_error_message=(
            str(payload["last_error_message"]) if payload["last_error_message"] else None
        ),
    )
