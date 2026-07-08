from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


ACCEPT_INDICATORS: dict[str, tuple[str, ...]] = {
    "scollect": ("scollect", "admin tools", "administrator tools", "live dashboard"),
    "overview_cards": ("accounts worked", "accounts", "attempts", "rpc", "contact rate", "close rate"),
    "portfolio_table": ("collections by portfolio", "accounts_worked", "calls_per_act", "leftmsglive"),
    "agent_table": ("collections by agent", "lftmsgmach", "no_answer"),
    "collections": ("collections in range", "posted", "pending", "future scheduled", "future payments"),
    "run_during_time": ("run during time", "run rate"),
    "whiteboard": ("whiteboard",),
    "payment_summary": ("posted cash", "pending cash", "posted fees", "pending fees", "future scheduled cash"),
}

REJECT_INDICATORS: dict[str, tuple[str, ...]] = {
    "online_payment": ("online payment", "payment received", "credit or debit card", "debit card", "account number"),
    "email": ("gmail", "outlook", "inbox", "subject:", "from:", "to:", "mail.google", "message"),
    "attendance": ("daily checklist", "daily attendance", "time off requests", "save morning attendance"),
    "unrelated_browser": ("new tab", "google apps script user", "ask gemini"),
}


@dataclass(frozen=True)
class ScreenshotClassification:
    is_operations_dashboard: bool
    reason: str
    matched_indicators: list[str] = field(default_factory=list)
    rejected_indicators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "is_operations_dashboard": self.is_operations_dashboard,
            "reason": self.reason,
            "matched_indicators": self.matched_indicators,
            "rejected_indicators": self.rejected_indicators,
        }


class OperationsScreenshotClassifier:
    def __init__(self, ocr_command: str = "tesseract") -> None:
        self.ocr_command = ocr_command

    def classify_image(self, image_path: Path, *, existing_text: str = "") -> ScreenshotClassification:
        text = existing_text or self._ocr_text(image_path)
        return self.classify_text(text, image_path=image_path)

    def classify_text(self, text: str, *, image_path: Path | None = None) -> ScreenshotClassification:
        normalized = _normalize(text)
        matched = _matched_indicator_names(normalized, ACCEPT_INDICATORS)
        rejected = _matched_indicator_names(normalized, REJECT_INDICATORS)
        if image_path and self._looks_like_phone_screenshot(image_path):
            rejected.append("phone_screenshot")
        if rejected:
            return ScreenshotClassification(
                False,
                "Rejected non-operations screenshot: " + ", ".join(sorted(set(rejected))),
                sorted(set(matched)),
                sorted(set(rejected)),
            )
        if self._has_dashboard_signal(matched, normalized):
            return ScreenshotClassification(
                True,
                "Accepted SCollect operations dashboard indicators.",
                sorted(set(matched)),
                [],
            )
        return ScreenshotClassification(
            False,
            "Missing SCollect operations dashboard indicators.",
            sorted(set(matched)),
            [],
        )

    def _has_dashboard_signal(self, matched: list[str], normalized: str) -> bool:
        matched_set = set(matched)
        if "scollect" in matched_set and len(matched_set) >= 2:
            return True
        if {"portfolio_table", "collections"} <= matched_set:
            return True
        if {"agent_table", "collections"} <= matched_set:
            return True
        if "portfolio_table" in matched_set and "run_during_time" in matched_set:
            return True
        overview_terms = sum(
            1
            for term in ("accounts", "attempts", "rpc", "contact rate", "close rate")
            if term in normalized
        )
        return overview_terms >= 3 and bool(matched_set & {"collections", "whiteboard", "payment_summary"})

    def _ocr_text(self, image_path: Path) -> str:
        if not shutil.which(self.ocr_command):
            return ""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_base = Path(temp_dir) / "ops-classify"
            try:
                subprocess.run(
                    [self.ocr_command, str(image_path), str(output_base), "--psm", "11"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
            except (OSError, subprocess.SubprocessError):
                return ""
            return output_base.with_suffix(".txt").read_text(errors="ignore")

    def _looks_like_phone_screenshot(self, image_path: Path) -> bool:
        try:
            from PIL import Image
        except ImportError:
            return False
        try:
            with Image.open(image_path) as image:
                width, height = image.size
        except OSError:
            return False
        return height > width * 1.25


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _matched_indicator_names(text: str, indicators: dict[str, tuple[str, ...]]) -> list[str]:
    matched = []
    for name, terms in indicators.items():
        if any(term in text for term in terms):
            matched.append(name)
    return matched
