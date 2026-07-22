from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.voicemail_tracker_agent.config import Settings
from agents.voicemail_tracker_agent.graph_client import VoicemailGraphClient
from agents.voicemail_tracker_agent.parser import is_vaspian_voicemail, parse_voicemail_message
from agents.voicemail_tracker_agent.sample_data import SAMPLE_VOICEMAIL_MESSAGES


class VoicemailParserTests(unittest.TestCase):
    def test_parse_sample_voicemail(self) -> None:
        record = parse_voicemail_message(SAMPLE_VOICEMAIL_MESSAGES[0], "America/New_York")

        self.assertEqual(record.date_received, "2026-07-01")
        self.assertEqual(record.time_received, "08:42:15")
        self.assertEqual(record.phone_number, "(555) 123-4567")
        self.assertEqual(record.duration, "00:01:14")
        self.assertIn("account number B123456", record.transcript)
        self.assertEqual(record.audio_reference, "voicemail-5551234567.wav")
        self.assertEqual(record.source_email_id, "<sample-message-1@vaspian>")

    def test_vaspian_filter_allows_configured_sender_and_subject(self) -> None:
        self.assertTrue(
            is_vaspian_voicemail(
                SAMPLE_VOICEMAIL_MESSAGES[0],
                sender_email="voicemail@vaspian.com",
                subject_contains="voicemail",
            )
        )

    def test_vaspian_filter_rejects_wrong_sender(self) -> None:
        self.assertFalse(
            is_vaspian_voicemail(
                SAMPLE_VOICEMAIL_MESSAGES[0],
                sender_email="other@example.com",
                subject_contains="voicemail",
            )
        )

    def test_graph_search_includes_messages_moved_out_of_inbox(self) -> None:
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
            status_path=Path("/tmp/status.json"),
            runtime_state_path=Path("/tmp/runtime.json"),
            health_path=Path("/tmp/health.json"),
        )
        client = VoicemailGraphClient(settings)
        moved_message = {**SAMPLE_VOICEMAIL_MESSAGES[0], "hasAttachments": False}

        with patch.object(
            client,
            "list_user_messages",
            return_value=[moved_message],
        ) as list_messages:
            messages = client.find_voicemail_messages()

        self.assertEqual(messages, [moved_message])
        list_messages.assert_called_once()
        self.assertIn(
            "receivedDateTime ge ",
            list_messages.call_args.kwargs["filter_query"],
        )

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("agents.voicemail_tracker_agent.audio.AIBudgetGuard")
    @patch("agents.voicemail_tracker_agent.audio.requests.post")
    @patch("agents.voicemail_tracker_agent.audio.subprocess.run")
    def test_mp3_attachment_supplies_duration_and_transcript(
        self,
        run,
        post,
        budget_guard,
    ) -> None:
        budget_guard.return_value.reserve.return_value = SimpleNamespace(
            allowed=True,
            warning=None,
        )
        run.return_value.stdout = "1.25\n"
        post.return_value.json.return_value = {"text": "No."}
        message = deepcopy(SAMPLE_VOICEMAIL_MESSAGES[0])
        message["body"]["content"] = "Transcript: This email-body text must not be used."
        message["attachments"] = [
            {
                "id": "attachment-1",
                "name": "Voice Message.mp3",
                "contentType": "audio/mpeg",
                "content_bytes": b"ID3-mp3-bytes",
            }
        ]

        record = parse_voicemail_message(message, "America/New_York")

        self.assertEqual(record.audio_reference, "Voice Message.mp3")
        self.assertEqual(record.duration, "00:00:01")
        self.assertEqual(record.transcript, "No.")
        self.assertEqual(post.call_args.kwargs["files"]["file"][1], b"ID3-mp3-bytes")


if __name__ == "__main__":
    unittest.main()
