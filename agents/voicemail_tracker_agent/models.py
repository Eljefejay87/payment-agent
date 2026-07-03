from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoicemailRecord:
    date_received: str
    time_received: str
    phone_number: str
    duration: str
    transcript: str
    audio_reference: str
    source_email_id: str
    email_subject: str = ""
    sender_email: str = ""
