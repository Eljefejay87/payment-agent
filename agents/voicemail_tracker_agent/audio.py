"""Audio duration and transcription helpers for voicemail attachments."""

from __future__ import annotations

import logging
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

from shared.ai_budget import AIBudgetGuard, estimate_transcription_cost


TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioProcessingResult:
    duration: str
    transcript: str
    transcription_status: str = "Succeeded"
    last_error: str | None = None


def process_audio_attachment(attachment: dict) -> AudioProcessingResult:
    content = attachment.get("content_bytes")
    if not isinstance(content, bytes) or not content:
        return AudioProcessingResult(
            duration="",
            transcript="Transcription Pending",
            transcription_status="Pending",
            last_error="Audio attachment content was unavailable.",
        )

    name = str(attachment.get("name") or "voicemail.mp3")
    content_type = str(attachment.get("contentType") or "audio/mpeg")
    try:
        duration = _audio_duration(content, name)
    except Exception:
        duration = ""
    try:
        transcript = _transcribe_audio(content, name, content_type, duration)
    except TransientTranscriptionError as exc:
        return AudioProcessingResult(
            duration=duration,
            transcript="Transcription Pending",
            transcription_status="Pending",
            last_error=str(exc),
        )
    except Exception as exc:
        return AudioProcessingResult(
            duration=duration,
            transcript="Transcription Failed",
            transcription_status="Failed",
            last_error=f"{type(exc).__name__}: {exc}",
        )
    return AudioProcessingResult(duration=duration, transcript=transcript)


def prepare_pending_audio_attachment(attachment: dict) -> AudioProcessingResult:
    """Extract local audio metadata without making a transcription request."""
    content = attachment.get("content_bytes")
    duration = ""
    if isinstance(content, bytes) and content:
        try:
            duration = _audio_duration(
                content,
                str(attachment.get("name") or "voicemail.mp3"),
            )
        except Exception:
            pass
    return AudioProcessingResult(
        duration=duration,
        transcript="",
        transcription_status="Pending",
        last_error="Backfilled legacy transcription failure.",
    )


class TransientTranscriptionError(RuntimeError):
    """A transcription failure that is safe to retry later."""


def _audio_duration(content: bytes, name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in {".mp3", ".m4a", ".ogg", ".wav"}:
        suffix = ".mp3"
    with tempfile.TemporaryDirectory(prefix="ucm-voicemail-") as directory:
        path = Path(directory) / f"audio{suffix}"
        path.write_bytes(content)
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    seconds = float(result.stdout.strip())
    total_seconds = max(1, int(math.floor(seconds + 0.5))) if seconds > 0 else 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"


def _transcribe_audio(
    content: bytes,
    name: str,
    content_type: str,
    duration: str,
) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    model = os.getenv(
        "VOICEMAIL_TRANSCRIPTION_MODEL",
        DEFAULT_TRANSCRIPTION_MODEL,
    )
    decision = AIBudgetGuard().reserve(
        agent="Voicemail Tracker",
        estimated_cost=estimate_transcription_cost(duration, model),
        model=model,
    )
    if not decision.allowed:
        LOGGER.warning(
            "Blocked OpenAI request for Voicemail Tracker: %s",
            decision.reason,
        )
        raise TransientTranscriptionError(
            f"OpenAI request blocked by AI budget guard: {decision.reason}."
        )
    if decision.warning:
        LOGGER.warning(
            "AI budget %s; month spend $%.2f, remaining $%.2f.",
            decision.warning,
            decision.spend_this_month,
            decision.remaining_budget,
        )
    try:
        response = requests.post(
            TRANSCRIPTION_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": model,
                "response_format": "json",
            },
            files={"file": (name, content, content_type)},
            timeout=120,
        )
    except requests.RequestException as exc:
        raise TransientTranscriptionError(
            f"{type(exc).__name__}: Temporary transcription network failure."
        ) from exc
    status_code = response.status_code
    if status_code == 429 or (
        isinstance(status_code, int) and 500 <= status_code < 600
    ):
        raise TransientTranscriptionError(
            f"OpenAI transcription returned HTTP {response.status_code}."
        )
    response.raise_for_status()
    transcript = str(response.json().get("text") or "").strip()
    if not transcript:
        raise RuntimeError("The transcription service returned no text.")
    return transcript
