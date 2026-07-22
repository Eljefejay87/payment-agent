from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Callable

from .audio import (
    AudioProcessingResult,
    prepare_pending_audio_attachment,
    process_audio_attachment,
)
from .config import Settings
from .graph_client import GraphClient
from .models import VoicemailRunOutcome, VoicemailStatusSnapshot
from .parser import parse_voicemail_message, parse_voicemail_message_details
from .runtime_store import (
    CallbackState,
    TranscriptionJob,
    VoicemailRuntimeState,
    VoicemailRuntimeStateError,
    VoicemailRuntimeStore,
    utc_now,
)
from .status_store import VoicemailStatusStateError, VoicemailStatusStore

LOGGER = logging.getLogger(__name__)
TRANSCRIPTION_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=24),
)


class VoicemailTrackerAgent:
    def __init__(
        self,
        settings: Settings,
        graph: GraphClient | None = None,
        status_store: VoicemailStatusStore | None = None,
        runtime_store: VoicemailRuntimeStore | None = None,
        audio_processor: Callable[[dict], AudioProcessingResult] = process_audio_attachment,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.graph = graph or GraphClient(settings)
        self.status_store = status_store or VoicemailStatusStore(settings.status_path)
        self.runtime_store = runtime_store or VoicemailRuntimeStore(settings.runtime_state_path)
        self.audio_processor = audio_processor
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def scan_once(self) -> list[dict]:
        attempted_at = self.now_provider()
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
                parsed = parse_voicemail_message_details(
                    message,
                    self.settings.timezone,
                    self.audio_processor,
                )
                record = asdict(parsed.record)
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
                self._store_transcription_outcome(
                    runtime_state,
                    voicemail_id,
                    message,
                    parsed.audio_attachment,
                    parsed.audio_result,
                    record,
                )
            completed_at = self.now_provider()
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

    def retry_pending_transcriptions(self) -> list[dict]:
        runtime_state = self._runtime_state()
        now = self.now_provider()
        attempted_records: list[dict] = []
        for job in runtime_state.pending_transcriptions():
            if not _retry_is_due(job.next_retry_at, now):
                continue
            attachment = {
                "id": job.attachment_id,
                "name": job.attachment_name,
                "contentType": job.content_type,
            }
            try:
                downloaded = self.graph.get_audio_attachment_content(
                    job.message_id,
                    attachment,
                )
                result = self.audio_processor(downloaded)
            except Exception as exc:
                result = AudioProcessingResult(
                    duration="",
                    transcript="Transcription Pending",
                    transcription_status="Pending",
                    last_error=f"{type(exc).__name__}: Temporary attachment or transcription failure.",
                )
            job.attempt_count += 1
            attempted_records.append(dict(job.record))
            if result.transcription_status == "Succeeded":
                job.record["transcript"] = result.transcript
                attempted_records[-1]["transcript"] = result.transcript
                job.status = "Completed"
                job.next_retry_at = None
                job.last_error = None
                LOGGER.info("Retry succeeded for voicemail %s", job.voicemail_id)
                continue
            job.last_error = result.last_error
            if (
                result.transcription_status == "Pending"
                and job.attempt_count < self.settings.transcription_max_attempts
            ):
                job.next_retry_at = _next_retry_at(now, job.attempt_count)
                LOGGER.info(
                    "Retry scheduled for voicemail %s at %s (attempt %s/%s)",
                    job.voicemail_id,
                    job.next_retry_at,
                    job.attempt_count,
                    self.settings.transcription_max_attempts,
                )
                continue
            job.status = "Failed"
            job.next_retry_at = None
            job.record["transcript"] = "Transcription Failed"
            attempted_records[-1]["transcript"] = "Transcription Failed"
            LOGGER.warning(
                "Retry exhausted for voicemail %s after %s attempt(s)",
                job.voicemail_id,
                job.attempt_count,
            )
        if attempted_records:
            self._write_runtime_state(runtime_state)
        return attempted_records

    def backfill_pending_transcriptions(
        self,
        source_runtime_state_path: str | None = None,
    ) -> list[dict]:
        runtime_state = self._runtime_state()
        source_state = (
            VoicemailRuntimeStore(source_runtime_state_path).read()
            if source_runtime_state_path
            else runtime_state
        )
        legacy_ids = set(source_state.processed_voicemail_ids)
        if not legacy_ids:
            LOGGER.info("Transcription backfill found no legacy voicemail records.")
            return []

        queued: list[dict] = []
        messages = self.graph.find_voicemail_messages()
        legacy_matches = 0
        existing_jobs = 0
        ineligible_records = 0
        for message in messages:
            voicemail_id = _message_key(message)
            if voicemail_id not in legacy_ids:
                continue
            legacy_matches += 1
            existing_job = runtime_state.transcription_jobs.get(voicemail_id)
            if existing_job is not None:
                existing_jobs += 1
                continue
            parsed = parse_voicemail_message_details(
                message,
                self.settings.timezone,
                prepare_pending_audio_attachment,
            )
            record = asdict(parsed.record)
            transcript = record["transcript"].strip().lower()
            if (
                parsed.audio_attachment is None
                or not record["audio_reference"]
                or (transcript and not transcript.startswith("transcription error"))
            ):
                ineligible_records += 1
                continue
            record["transcript"] = "Transcription Pending"
            attachment = parsed.audio_attachment
            runtime_state.transcription_jobs[voicemail_id] = TranscriptionJob(
                voicemail_id=voicemail_id,
                message_id=str(message.get("id") or ""),
                attachment_id=str(attachment.get("id") or ""),
                attachment_name=str(attachment.get("name") or "voicemail.mp3"),
                content_type=str(attachment.get("contentType") or "audio/mpeg"),
                record=dict(record),
                status="Pending",
                attempt_count=1,
                next_retry_at=self.now_provider().isoformat(),
                last_error="Backfilled legacy transcription failure.",
            )
            runtime_state.processed_voicemail_ids.setdefault(
                voicemail_id,
                source_state.processed_voicemail_ids[voicemail_id],
            )
            if voicemail_id in source_state.callbacks:
                runtime_state.callbacks.setdefault(
                    voicemail_id,
                    source_state.callbacks[voicemail_id],
                )
            queued.append(record)
            LOGGER.info("Transcription queued for voicemail %s by one-time backfill", voicemail_id)

        if queued:
            self._write_runtime_state(runtime_state)
        LOGGER.info(
            "Transcription backfill inspected %s Outlook candidate(s): "
            "%s legacy match(es), %s existing job(s), %s ineligible record(s).",
            len(messages),
            legacy_matches,
            existing_jobs,
            ineligible_records,
        )
        LOGGER.info("Transcription backfill queued %s pending job(s).", len(queued))
        return queued

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

    def _store_transcription_outcome(
        self,
        runtime_state: VoicemailRuntimeState,
        voicemail_id: str,
        message: dict,
        attachment: dict | None,
        result: AudioProcessingResult,
        record: dict[str, str],
    ) -> None:
        if attachment is None or result.transcription_status == "Succeeded":
            return
        job = TranscriptionJob(
            voicemail_id=voicemail_id,
            message_id=str(message.get("id") or ""),
            attachment_id=str(attachment.get("id") or ""),
            attachment_name=str(attachment.get("name") or "voicemail.mp3"),
            content_type=str(attachment.get("contentType") or "audio/mpeg"),
            record=dict(record),
            status=result.transcription_status,
            attempt_count=1,
            last_error=result.last_error,
        )
        if (
            result.transcription_status == "Pending"
            and self.settings.transcription_max_attempts > 1
        ):
            job.next_retry_at = _next_retry_at(self.now_provider(), job.attempt_count)
            LOGGER.info("Transcription queued for voicemail %s", voicemail_id)
            LOGGER.info(
                "Retry scheduled for voicemail %s at %s (attempt %s/%s)",
                voicemail_id,
                job.next_retry_at,
                job.attempt_count,
                self.settings.transcription_max_attempts,
            )
        else:
            job.status = "Failed"
            job.record["transcript"] = "Transcription Failed"
            record["transcript"] = "Transcription Failed"
            LOGGER.warning(
                "Retry exhausted for voicemail %s after %s attempt(s)",
                voicemail_id,
                job.attempt_count,
            )
        runtime_state.transcription_jobs[voicemail_id] = job


def _message_key(message: dict) -> str:
    return str(message.get("internetMessageId") or message.get("id") or "")


def _next_retry_at(now: datetime, attempt_count: int) -> str:
    index = min(max(attempt_count - 1, 0), len(TRANSCRIPTION_RETRY_DELAYS) - 1)
    return (now + TRANSCRIPTION_RETRY_DELAYS[index]).isoformat()


def _retry_is_due(next_retry_at: str | None, now: datetime) -> bool:
    if not next_retry_at:
        return True
    try:
        scheduled = datetime.fromisoformat(next_retry_at)
    except ValueError:
        return True
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    return scheduled <= now


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
