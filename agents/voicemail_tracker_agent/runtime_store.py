"""Durable runtime state for Voicemail Tracker restart safety."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CallbackState:
    voicemail_id: str
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    teams_alert_sent_at: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.status != "completed"


@dataclass
class VoicemailRuntimeState:
    processed_voicemail_ids: dict[str, str] = field(default_factory=dict)
    last_successful_scan: str | None = None
    callbacks: dict[str, CallbackState] = field(default_factory=dict)
    last_teams_summary_sent_for: str | None = None

    def pending_callbacks(self) -> list[CallbackState]:
        return [
            callback
            for callback in self.callbacks.values()
            if callback.is_pending
        ]


class VoicemailRuntimeStateError(RuntimeError):
    """Raised when persisted runtime state cannot be read safely."""


class VoicemailRuntimeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def read(self) -> VoicemailRuntimeState:
        if not self.path.exists():
            return VoicemailRuntimeState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return _state_from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise VoicemailRuntimeStateError("Voicemail runtime state is corrupt or unreadable.") from exc

    def write(self, state: VoicemailRuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_state_to_dict(state), indent=2, sort_keys=True) + "\n"
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


def _state_to_dict(state: VoicemailRuntimeState) -> dict[str, Any]:
    callbacks = {
        callback_id: {
            "voicemail_id": callback.voicemail_id,
            "status": callback.status,
            "created_at": callback.created_at,
            "completed_at": callback.completed_at,
            "teams_alert_sent_at": callback.teams_alert_sent_at,
        }
        for callback_id, callback in state.callbacks.items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "processed_voicemail_ids": state.processed_voicemail_ids,
        "last_successful_scan": state.last_successful_scan,
        "pending_callbacks": [
            callback.voicemail_id
            for callback in state.pending_callbacks()
        ],
        "callback_completion_status": callbacks,
        "last_teams_summary_sent_for": state.last_teams_summary_sent_for,
    }


def _state_from_dict(payload: object) -> VoicemailRuntimeState:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected voicemail runtime state payload.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported voicemail runtime state schema version.")
    processed = payload.get("processed_voicemail_ids", {})
    callback_payload = payload.get("callback_completion_status", {})
    if not isinstance(processed, dict) or not isinstance(callback_payload, dict):
        raise ValueError("Unexpected voicemail runtime state fields.")
    callbacks: dict[str, CallbackState] = {}
    for callback_id, value in callback_payload.items():
        if not isinstance(value, dict):
            raise ValueError("Unexpected callback state.")
        callbacks[str(callback_id)] = CallbackState(
            voicemail_id=str(value.get("voicemail_id", callback_id)),
            status=str(value.get("status", "pending")),
            created_at=str(value.get("created_at") or utc_now()),
            completed_at=(
                str(value["completed_at"]) if value.get("completed_at") else None
            ),
            teams_alert_sent_at=(
                str(value["teams_alert_sent_at"])
                if value.get("teams_alert_sent_at")
                else None
            ),
        )
    return VoicemailRuntimeState(
        processed_voicemail_ids={str(key): str(value) for key, value in processed.items()},
        last_successful_scan=(
            str(payload["last_successful_scan"])
            if payload.get("last_successful_scan")
            else None
        ),
        callbacks=callbacks,
        last_teams_summary_sent_for=(
            str(payload["last_teams_summary_sent_for"])
            if payload.get("last_teams_summary_sent_for")
            else None
        ),
    )
