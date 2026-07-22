from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from agents.voicemail_tracker_agent.audio import (
    AudioProcessingResult,
    process_audio_attachment,
)
from agents.voicemail_tracker_agent.config import Settings
from agents.voicemail_tracker_agent.runtime_store import VoicemailRuntimeStore
from agents.voicemail_tracker_agent.runtime_store import (
    CallbackState,
    VoicemailRuntimeState,
)
from agents.voicemail_tracker_agent.sample_data import SAMPLE_VOICEMAIL_MESSAGES
from agents.voicemail_tracker_agent.service import (
    VoicemailTrackerAgent,
    _next_retry_at,
)


class RetryGraph:
    def __init__(self, message: dict) -> None:
        self.message = message
        self.download_calls = 0

    def find_voicemail_messages(self) -> list[dict]:
        return [self.message]

    def get_audio_attachment_content(self, message_id: str, attachment: dict) -> dict:
        self.download_calls += 1
        return {**attachment, "content_bytes": b"retry-mp3"}


class ResultSequence:
    def __init__(self, *results: AudioProcessingResult) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, _attachment: dict) -> AudioProcessingResult:
        result = self.results[self.calls]
        self.calls += 1
        return result


class VoicemailTranscriptionRetryTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("agents.voicemail_tracker_agent.audio.AIBudgetGuard")
    @patch("agents.voicemail_tracker_agent.audio.requests.post")
    @patch("agents.voicemail_tracker_agent.audio.subprocess.run")
    def test_http_429_queues_transcription(self, run, post, budget_guard) -> None:
        budget_guard.return_value.reserve.return_value = SimpleNamespace(
            allowed=True,
            warning=None,
        )
        run.return_value.stdout = "1.0\n"
        post.return_value.status_code = 429

        result = process_audio_attachment(self._attachment())

        self.assertEqual(result.transcription_status, "Pending")
        self.assertEqual(result.transcript, "Transcription Pending")
        self.assertIn("HTTP 429", result.last_error or "")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("agents.voicemail_tracker_agent.audio.AIBudgetGuard")
    @patch("agents.voicemail_tracker_agent.audio.requests.post")
    @patch("agents.voicemail_tracker_agent.audio.subprocess.run")
    def test_timeout_queues_transcription(self, run, post, budget_guard) -> None:
        budget_guard.return_value.reserve.return_value = SimpleNamespace(
            allowed=True,
            warning=None,
        )
        run.return_value.stdout = "1.0\n"
        post.side_effect = requests.Timeout("timed out")

        result = process_audio_attachment(self._attachment())

        self.assertEqual(result.transcription_status, "Pending")
        self.assertIn("Timeout", result.last_error or "")

    def test_retry_schedule_uses_required_delays(self) -> None:
        now = datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc)

        self.assertEqual(_next_retry_at(now, 1), (now + timedelta(minutes=5)).isoformat())
        self.assertEqual(_next_retry_at(now, 2), (now + timedelta(minutes=30)).isoformat())
        self.assertEqual(_next_retry_at(now, 3), (now + timedelta(hours=2)).isoformat())
        self.assertEqual(_next_retry_at(now, 4), (now + timedelta(hours=24)).isoformat())

    def test_successful_retry_completes_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent, store, clock, processor, _graph = self._retry_agent(directory)
            agent.scan_once()
            clock[0] += timedelta(minutes=5)

            records = agent.retry_pending_transcriptions()
            state = store.read()
            job = state.transcription_jobs["<sample-message-1@vaspian>"]

            self.assertEqual(len(records), 1)
            self.assertEqual(processor.calls, 2)
            self.assertEqual(job.status, "Completed")
            self.assertIsNone(job.next_retry_at)
            self.assertIsNone(job.last_error)

    def test_retry_updates_only_transcript_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent, store, clock, _processor, _graph = self._retry_agent(directory)
            agent.scan_once()
            before = dict(store.read().transcription_jobs["<sample-message-1@vaspian>"].record)
            clock[0] += timedelta(minutes=5)

            agent.retry_pending_transcriptions()
            after = store.read().transcription_jobs["<sample-message-1@vaspian>"].record

            self.assertEqual(after["transcript"], "Retry transcription succeeded.")
            self.assertEqual(
                {key: value for key, value in after.items() if key != "transcript"},
                {key: value for key, value in before.items() if key != "transcript"},
            )

    def test_retry_does_not_create_duplicate_voicemail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent, store, clock, _processor, graph = self._retry_agent(directory)
            agent.scan_once()
            clock[0] += timedelta(minutes=5)
            agent.retry_pending_transcriptions()

            self.assertEqual(agent.retry_pending_transcriptions(), [])
            self.assertEqual(agent.scan_once(), [])
            state = store.read()
            self.assertEqual(len(state.processed_voicemail_ids), 1)
            self.assertEqual(len(state.callbacks), 1)
            self.assertEqual(len(state.transcription_jobs), 1)
            self.assertEqual(graph.download_calls, 1)

    def test_backfill_queues_legacy_failed_record_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent, store, _clock, _processor, _graph = self._retry_agent(directory)
            source_store = VoicemailRuntimeStore(Path(directory) / "legacy-runtime.json")
            voicemail_id = "<sample-message-1@vaspian>"
            source_store.write(
                VoicemailRuntimeState(
                    processed_voicemail_ids={
                        voicemail_id: "2026-07-18T20:47:48+00:00",
                    },
                    callbacks={
                        voicemail_id: CallbackState(voicemail_id=voicemail_id),
                    },
                )
            )

            with patch(
                "agents.voicemail_tracker_agent.service.prepare_pending_audio_attachment",
                return_value=AudioProcessingResult(
                    duration="00:00:01",
                    transcript="",
                    transcription_status="Pending",
                    last_error="Backfilled legacy transcription failure.",
                ),
            ):
                records = agent.backfill_pending_transcriptions(str(source_store.path))

            state = store.read()
            job = state.transcription_jobs[voicemail_id]
            self.assertEqual(len(records), 1)
            self.assertEqual(job.status, "Pending")
            self.assertEqual(job.record["transcript"], "Transcription Pending")
            self.assertEqual(job.record["audio_reference"], "Voice Message.mp3")
            self.assertEqual(len(state.processed_voicemail_ids), 1)
            self.assertEqual(len(state.callbacks), 1)

    @staticmethod
    def _attachment() -> dict:
        return {
            "id": "attachment-1",
            "name": "Voice Message.mp3",
            "contentType": "audio/mpeg",
            "content_bytes": b"mp3-bytes",
        }

    @classmethod
    def _retry_agent(cls, directory: str):
        base = Path(directory)
        runtime_store = VoicemailRuntimeStore(base / "runtime.json")
        message = deepcopy(SAMPLE_VOICEMAIL_MESSAGES[0])
        message["attachments"] = [cls._attachment()]
        graph = RetryGraph(message)
        clock = [datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc)]
        processor = ResultSequence(
            AudioProcessingResult(
                duration="00:00:01",
                transcript="Transcription Pending",
                transcription_status="Pending",
                last_error="OpenAI transcription returned HTTP 429.",
            ),
            AudioProcessingResult(
                duration="00:00:01",
                transcript="Retry transcription succeeded.",
            ),
        )
        settings = Settings(
            dry_run=True,
            log_level="INFO",
            timezone="America/New_York",
            mailbox_user_id="voicemail@example.com",
            graph_tenant_id="tenant",
            graph_client_id="client",
            graph_client_secret="secret",
            sender_email="voicemail@vaspian.com",
            subject_contains="voicemail",
            lookback_hours=48,
            scan_interval_minutes=15,
            summary_time="08:50",
            run_startup_scan=True,
            status_path=base / "status.json",
            runtime_state_path=runtime_store.path,
            health_path=base / "health.json",
        )
        agent = VoicemailTrackerAgent(
            settings,
            graph=graph,
            runtime_store=runtime_store,
            audio_processor=processor,
            now_provider=lambda: clock[0],
        )
        return agent, runtime_store, clock, processor, graph


if __name__ == "__main__":
    unittest.main()
