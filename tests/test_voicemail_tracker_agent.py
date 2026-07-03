from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
