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
class TranscriptionJob:
    voicemail_id: str
    message_id: str
    attachment_id: str
    attachment_name: str
    content_type: str
    record: dict[str, str]
    status: str = "Pending"
    attempt_count: int = 1
    next_retry_at: str | None = None
    last_error: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == "Pending"


@dataclass
class VoicemailRuntimeState:
    processed_voicemail_ids: dict[str, str] = field(default_factory=dict)
    last_successful_scan: str | None = None
    callbacks: dict[str, CallbackState] = field(default_factory=dict)
    last_teams_summary_sent_for: str | None = None
    transcription_jobs: dict[str, TranscriptionJob] = field(default_factory=dict)

    def pending_callbacks(self) -> list[CallbackState]:
        return [
            callback
            for callback in self.callbacks.values()
            if callback.is_pending
        ]

    def pending_transcriptions(self) -> list[TranscriptionJob]:
        return [job for job in self.transcription_jobs.values() if job.is_pending]


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
    transcription_jobs = {
        voicemail_id: {
            "voicemail_id": job.voicemail_id,
            "message_id": job.message_id,
            "attachment_id": job.attachment_id,
            "attachment_name": job.attachment_name,
            "content_type": job.content_type,
            "record": job.record,
            "status": job.status,
            "attempt_count": job.attempt_count,
            "next_retry_at": job.next_retry_at,
            "last_error": job.last_error,
        }
        for voicemail_id, job in state.transcription_jobs.items()
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
        "transcription_jobs": transcription_jobs,
    }


def _state_from_dict(payload: object) -> VoicemailRuntimeState:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected voicemail runtime state payload.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported voicemail runtime state schema version.")
    processed = payload.get("processed_voicemail_ids", {})
    callback_payload = payload.get("callback_completion_status", {})
    transcription_payload = payload.get("transcription_jobs", {})
    if (
        not isinstance(processed, dict)
        or not isinstance(callback_payload, dict)
        or not isinstance(transcription_payload, dict)
    ):
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
    transcription_jobs: dict[str, TranscriptionJob] = {}
    for voicemail_id, value in transcription_payload.items():
        if not isinstance(value, dict) or not isinstance(value.get("record", {}), dict):
            raise ValueError("Unexpected transcription job state.")
        transcription_jobs[str(voicemail_id)] = TranscriptionJob(
            voicemail_id=str(value.get("voicemail_id", voicemail_id)),
            message_id=str(value.get("message_id", "")),
            attachment_id=str(value.get("attachment_id", "")),
            attachment_name=str(value.get("attachment_name", "voicemail.mp3")),
            content_type=str(value.get("content_type", "audio/mpeg")),
            record={str(key): str(item) for key, item in value.get("record", {}).items()},
            status=str(value.get("status", "Pending")),
            attempt_count=max(0, int(value.get("attempt_count", 0))),
            next_retry_at=(
                str(value["next_retry_at"]) if value.get("next_retry_at") else None
            ),
            last_error=str(value["last_error"]) if value.get("last_error") else None,
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
        transcription_jobs=transcription_jobs,
    )
