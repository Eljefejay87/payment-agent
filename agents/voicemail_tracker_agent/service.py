from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from .config import Settings
from .graph_client import GraphClient
from .models import VoicemailRunOutcome, VoicemailStatusSnapshot
from .parser import parse_voicemail_message
from .runtime_store import (
    CallbackState,
    VoicemailRuntimeState,
    VoicemailRuntimeStateError,
    VoicemailRuntimeStore,
    utc_now,
)
from .status_store import VoicemailStatusStateError, VoicemailStatusStore

LOGGER = logging.getLogger(__name__)


class VoicemailTrackerAgent:
    def __init__(
        self,
        settings: Settings,
        graph: GraphClient | None = None,
        status_store: VoicemailStatusStore | None = None,
        runtime_store: VoicemailRuntimeStore | None = None,
    ) -> None:
        self.settings = settings
        self.graph = graph or GraphClient(settings)
        self.status_store = status_store or VoicemailStatusStore(settings.status_path)
        self.runtime_store = runtime_store or VoicemailRuntimeStore(settings.runtime_state_path)

    def scan_once(self) -> list[dict]:
        attempted_at = datetime.now(timezone.utc)
        previous = self._previous_status()
        runtime_state = self._runtime_state()
        try:
            messages = self.graph.find_voicemail_messages()
            LOGGER.info("Found %s Vaspian voicemail email(s)", len(messages))
            seen_ids = set(runtime_state.processed_voicemail_ids)
            new_messages = []
            for message in messages:
                message_id = _message_key(message)
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                new_messages.append(message)
            skipped_count = len(messages) - len(new_messages)
            if skipped_count:
                LOGGER.info("Skipped %s already processed voicemail email(s)", skipped_count)
            records = []
            for message in new_messages:
                record = asdict(parse_voicemail_message(message, self.settings.timezone))
                records.append(record)
                voicemail_id = record["source_email_id"] or _message_key(message)
                LOGGER.info(
                    "Voicemail detected: %s %s from %s duration=%s source=%s",
                    record["date_received"],
                    record["time_received"],
                    record["phone_number"] or "unknown phone",
                    record["duration"] or "unknown duration",
                    voicemail_id,
                )
                runtime_state.processed_voicemail_ids[voicemail_id] = utc_now()
                runtime_state.callbacks.setdefault(
                    voicemail_id,
                    CallbackState(voicemail_id=voicemail_id),
                )
            completed_at = datetime.now(timezone.utc)
            runtime_state.last_successful_scan = completed_at.isoformat()
            self._write_runtime_state(runtime_state)
            for record in records:
                if self.settings.dry_run:
                    LOGGER.info("DRY_RUN enabled; voicemail record was parsed only.")
        except Exception as exc:
            self._write_status(
                VoicemailStatusSnapshot(
                    last_attempted_run=attempted_at,
                    last_successful_run=_last_successful_run(previous, runtime_state),
                    last_run_outcome=VoicemailRunOutcome.ERROR,
                    pending_callback_count=(
                        len(runtime_state.pending_callbacks())
                        if runtime_state
                        else previous.pending_callback_count if previous else None
                    ),
                    total_records_processed=0,
                    last_error_message=f"{type(exc).__name__}: Voicemail scan failed.",
                )
            )
            raise
        self._write_status(
            VoicemailStatusSnapshot(
                last_attempted_run=attempted_at,
                last_successful_run=completed_at,
                last_run_outcome=VoicemailRunOutcome.SUCCESS,
                pending_callback_count=len(runtime_state.pending_callbacks()),
                total_records_processed=len(records),
                last_error_message=None,
            )
        )
        return records

    def scan_sample(self, sample_messages: list[dict]) -> list[dict]:
        records = [
            asdict(parse_voicemail_message(message, self.settings.timezone))
            for message in sample_messages
        ]
        print(json.dumps(records, indent=2))
        return records

    def _previous_status(self) -> VoicemailStatusSnapshot | None:
        try:
            return self.status_store.read()
        except VoicemailStatusStateError as exc:
            LOGGER.warning("%s A new snapshot will replace it after this scan.", exc)
            return None

    def _runtime_state(self) -> VoicemailRuntimeState:
        try:
            return self.runtime_store.read()
        except VoicemailRuntimeStateError as exc:
            LOGGER.warning("%s A new runtime state file will replace it after this scan.", exc)
            return VoicemailRuntimeState()

    def _write_status(self, snapshot: VoicemailStatusSnapshot) -> None:
        try:
            self.status_store.write(snapshot)
        except Exception as exc:
            # Status persistence must never change the existing scan result.
            LOGGER.warning("Could not persist Voicemail Tracker status: %s", type(exc).__name__)

    def _write_runtime_state(self, state: VoicemailRuntimeState) -> None:
        if self.settings.dry_run:
            LOGGER.info("DRY_RUN enabled; runtime state was still updated for restart safety.")
        self.runtime_store.write(state)


def _message_key(message: dict) -> str:
    return str(message.get("internetMessageId") or message.get("id") or "")


def _last_successful_run(
    previous: VoicemailStatusSnapshot | None,
    runtime_state: VoicemailRuntimeState | None,
) -> datetime | None:
    if runtime_state and runtime_state.last_successful_scan:
        try:
            return datetime.fromisoformat(runtime_state.last_successful_scan)
        except ValueError:
            pass
    return previous.last_successful_run if previous else None
