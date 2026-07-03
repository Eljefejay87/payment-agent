from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


METRIC_FIELDS: tuple[str, ...] = (
    "accounts_worked",
    "attempts",
    "rpc",
    "contact_rate",
    "close_rate",
    "dollars_per_contact",
    "activated",
    "moved_to_hot",
    "live_contacts",
    "no_answer",
    "left_message",
    "average_agent",
    "emails_sent",
    "campaigns",
    "unique_open",
    "unique_link",
    "open_rate",
    "pay_rate",
    "posted_cash",
    "posted_fees",
    "pending_cash",
    "pending_fees",
    "green_cleared_cash",
    "future_scheduled_cash",
    "future_scheduled_fees",
)


MONEY_FIELDS = {
    "dollars_per_contact",
    "posted_cash",
    "posted_fees",
    "pending_cash",
    "pending_fees",
    "green_cleared_cash",
    "future_scheduled_cash",
    "future_scheduled_fees",
}


PERCENT_FIELDS = {"contact_rate", "close_rate", "open_rate", "pay_rate"}


QUALITY_REQUIRED_FIELDS: tuple[str, ...] = (
    "accounts_worked",
    "attempts",
    "live_contacts",
    "contact_rate",
)

QUALITY_REQUIRED_MONEY_FIELDS: tuple[str, ...] = (
    "posted_cash",
    "future_scheduled_cash",
)


@dataclass(frozen=True)
class TeamsImage:
    message_id: str
    image_id: str
    created_at: str
    file_name: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class SavedScreenshot:
    message_id: str
    image_id: str
    created_at: str
    report_date: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class MetricValue:
    value: float | int | str | None
    raw_text: str
    confidence: float

    @property
    def needs_review(self) -> bool:
        return self.value is None or self.confidence < 0.72

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "raw_text": self.raw_text,
            "confidence": round(self.confidence, 3),
            "needs_review": self.needs_review,
        }


@dataclass(frozen=True)
class ExtractedReport:
    report_date: str
    screenshot_hash: str
    screenshot_path: Path
    ocr_text: str
    metrics: dict[str, MetricValue]
    collector_totals: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    manual_review_notes: list[str] = field(default_factory=list)
    extracted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def needs_manual_review(self) -> bool:
        return bool(self.missing_fields or self.manual_review_notes)

    def metric_value(self, field: str) -> float | int | str | None:
        metric = self.metrics.get(field)
        return metric.value if metric else None

    @property
    def completeness_score(self) -> float:
        if not self.metrics:
            return 0.0
        present = sum(1 for metric in self.metrics.values() if metric.value is not None)
        return present / len(self.metrics)

    @property
    def confidence_score(self) -> float:
        present = [metric.confidence for metric in self.metrics.values() if metric.value is not None]
        if not present:
            return 0.0
        return sum(present) / len(present)

    @property
    def missing_quality_fields(self) -> list[str]:
        missing = [
            field
            for field in QUALITY_REQUIRED_FIELDS
            if self.metric_value(field) is None
        ]
        if not any(self.metric_value(field) is not None for field in QUALITY_REQUIRED_MONEY_FIELDS):
            missing.append("posted_cash_or_future_scheduled_cash")
        return missing

    @property
    def passes_quality_gate(self) -> bool:
        return not self.missing_quality_fields
