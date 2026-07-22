from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from shared.utils.text import html_to_text

from .audio import AudioProcessingResult, process_audio_attachment
from .models import VoicemailRecord

PHONE_RE = re.compile(
    r"(?:phone|caller|from|callback|number)\s*:?\s*(\+?1?[\s.-]?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4})",
    re.IGNORECASE,
)
LOOSE_PHONE_RE = re.compile(r"\+?1?[\s.-]?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}")
DURATION_RE = re.compile(r"(?:duration|length)\D{0,20}([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)", re.IGNORECASE)
TRANSCRIPT_RE = re.compile(r"(?:transcript|message)\s*:?\s*(.+)", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParsedVoicemail:
    record: VoicemailRecord
    audio_attachment: dict | None
    audio_result: AudioProcessingResult


def parse_voicemail_message(
    message: dict,
    timezone_name: str,
    audio_processor: Callable[[dict], AudioProcessingResult] = process_audio_attachment,
) -> VoicemailRecord:
    return parse_voicemail_message_details(
        message,
        timezone_name,
        audio_processor,
    ).record


def parse_voicemail_message_details(
    message: dict,
    timezone_name: str,
    audio_processor: Callable[[dict], AudioProcessingResult] = process_audio_attachment,
) -> ParsedVoicemail:
    text = _message_text(message)
    received = _received_datetime(message.get("receivedDateTime", ""), timezone_name)
    attachments = message.get("attachments") or []
    audio = next(
        (
            attachment
            for attachment in attachments
            if is_audio_attachment(
                attachment.get("name", ""),
                attachment.get("contentType", ""),
            )
        ),
        None,
    )
    audio_result = (
        audio_processor(audio)
        if audio and "content_bytes" in audio
        else AudioProcessingResult(
            duration=_first_match(DURATION_RE, text),
            transcript=_transcript(text),
        )
    )
    record = VoicemailRecord(
        date_received=received.strftime("%Y-%m-%d"),
        time_received=received.strftime("%H:%M:%S"),
        phone_number=_first_match(PHONE_RE, text) or _first_match(LOOSE_PHONE_RE, text),
        duration=audio_result.duration,
        transcript=audio_result.transcript,
        audio_reference=_audio_reference(attachments),
        source_email_id=message.get("internetMessageId") or message.get("id", ""),
        email_subject=message.get("subject", ""),
        sender_email=_sender_email(message),
    )
    return ParsedVoicemail(
        record=record,
        audio_attachment=audio,
        audio_result=audio_result,
    )


def is_vaspian_voicemail(message: dict, sender_email: str, subject_contains: str) -> bool:
    subject = (message.get("subject") or "").lower()
    sender = _sender_email(message).lower()
    sender_filter = sender_email.strip().lower()
    subject_filter = subject_contains.strip().lower()
    sender_ok = not sender_filter or sender == sender_filter
    subject_ok = not subject_filter or subject_filter in subject
    return sender_ok and subject_ok


def _message_text(message: dict) -> str:
    body = message.get("body", {})
    content = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        return html_to_text(content)
    return content


def _received_datetime(value: str, timezone_name: str) -> datetime:
    if value:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    else:
        parsed = datetime.now(tz=ZoneInfo("UTC"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo(timezone_name))


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    if not match:
        return ""
    value = match.group(1) if match.groups() else match.group(0)
    return re.sub(r"\s+", " ", value).strip()


def _transcript(text: str) -> str:
    match = TRANSCRIPT_RE.search(text or "")
    if not match:
        return ""
    transcript = match.group(1).strip()
    for marker in ("Audio", "Attachment", "Duration", "Caller ID"):
        index = transcript.lower().find(marker.lower() + ":")
        if index > 0:
            transcript = transcript[:index].strip()
    return transcript


def _audio_reference(attachments: list[dict]) -> str:
    names = [
        attachment.get("name", "")
        for attachment in attachments
        if is_audio_attachment(attachment.get("name", ""), attachment.get("contentType", ""))
    ]
    return ", ".join(name for name in names if name)


def is_audio_attachment(name: str, content_type: str) -> bool:
    lowered_name = (name or "").lower()
    lowered_type = (content_type or "").lower()
    return lowered_type.startswith("audio/") or lowered_name.endswith((".wav", ".mp3", ".m4a", ".ogg"))


def _sender_email(message: dict) -> str:
    return (
        message.get("from", {})
        .get("emailAddress", {})
        .get("address", "")
    )
