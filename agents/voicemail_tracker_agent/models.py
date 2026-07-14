from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


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


class VoicemailRunOutcome(str, Enum):
    SUCCESS = "Success"
    ERROR = "Error"


@dataclass(frozen=True)
class VoicemailStatusSnapshot:
    """Non-sensitive operational status from the latest live scan attempt."""

    last_attempted_run: datetime
    last_successful_run: datetime | None
    last_run_outcome: VoicemailRunOutcome
    pending_callback_count: int | None
    total_records_processed: int
    last_error_message: str | None = None

    def __post_init__(self) -> None:
        if self.last_attempted_run.tzinfo is None:
            raise ValueError("last_attempted_run must include timezone information.")
        if self.last_successful_run is not None and self.last_successful_run.tzinfo is None:
            raise ValueError("last_successful_run must include timezone information.")
        if self.pending_callback_count is not None and self.pending_callback_count < 0:
            raise ValueError("pending_callback_count cannot be negative.")
        if self.total_records_processed < 0:
            raise ValueError("total_records_processed cannot be negative.")
