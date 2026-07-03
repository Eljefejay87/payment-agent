from __future__ import annotations


SAMPLE_VOICEMAIL_MESSAGES = [
    {
        "id": "sample-message-1",
        "internetMessageId": "<sample-message-1@vaspian>",
        "subject": "New Voicemail from 555-123-4567",
        "receivedDateTime": "2026-07-01T12:42:15Z",
        "from": {"emailAddress": {"address": "voicemail@vaspian.com"}},
        "hasAttachments": True,
        "body": {
            "contentType": "text",
            "content": "\n".join(
                [
                    "New voicemail received.",
                    "Caller: (555) 123-4567",
                    "Duration: 00:01:14",
                    "Transcript: Hi, this is Maria. Please call me back about account number B123456.",
                ]
            ),
        },
        "attachments": [
            {
                "id": "attachment-1",
                "name": "voicemail-5551234567.wav",
                "contentType": "audio/wav",
                "size": 24576,
            }
        ],
    }
]
