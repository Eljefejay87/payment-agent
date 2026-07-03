from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.integrations.microsoft_teams import TeamsNotifier

from .config import OperationsSettings
from .database import OperationsDatabase
from .graph_client import OperationsGraphClient
from .history import build_historical_context, build_historical_summary, format_historical_summary
from .models import SavedScreenshot, TeamsImage
from .ocr import ScreenshotOcrExtractor
from .reports import build_detailed_report_text, build_manual_review_message, build_operations_message

LOGGER = logging.getLogger(__name__)


@dataclass
class HistoryImportSummary:
    days_searched: int
    screenshots_found: int = 0
    successfully_imported: int = 0
    manual_review_required: int = 0
    duplicates_skipped: int = 0
    missing_days: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    historical_summary_text: str = ""

    def format(self) -> str:
        lines = [
            "Operations Intelligence historical import summary",
            f"- Days searched: {self.days_searched}",
            f"- Screenshots found: {self.screenshots_found}",
            f"- Successfully imported: {self.successfully_imported}",
            f"- Manual review required: {self.manual_review_required}",
            f"- Duplicates skipped: {self.duplicates_skipped}",
            f"- Missing days: {', '.join(self.missing_days) or 'none'}",
            f"- Failed downloads/errors: {len(self.errors)}",
        ]
        lines.extend(f"  - {error}" for error in self.errors[:10])
        if self.historical_summary_text:
            lines.extend(["", self.historical_summary_text])
        return "\n".join(lines)


class OperationsIntelligenceAgent:
    def __init__(self, settings: OperationsSettings) -> None:
        self.settings = settings
        self.db = OperationsDatabase(settings.database_path)
        self.graph = OperationsGraphClient(settings)
        self.ocr = ScreenshotOcrExtractor(
            settings.ocr_command,
            settings.ocr_min_confidence,
            settings.collector_codes,
            settings.ocr_debug,
            settings.reports_dir / "debug",
        )
        self.teams = TeamsNotifier(_LeadershipTeamsSettings(settings), self.graph)

    def initialize(self) -> None:
        self.db.initialize()
        self.settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.settings.reports_dir.mkdir(parents=True, exist_ok=True)

    def scan_once(self, force: bool = False) -> int:
        self.initialize()
        if not force and not self._inside_daily_window():
            LOGGER.info("Operations Intelligence Agent waiting for daily screenshot window")
            return 0

        processed = 0
        for image in self.graph.find_recent_images():
            try:
                if self._process_image(image):
                    processed += 1
            except Exception:
                LOGGER.exception("Failed to process Teams screenshot from message %s", image.message_id)
        LOGGER.info("Operations scan complete. Processed=%s", processed)
        return processed

    def import_history(
        self,
        days: int,
        *,
        dry_run: bool = False,
        debug: bool = False,
        force_reprocess: bool = False,
    ) -> HistoryImportSummary:
        self.initialize()
        summary = HistoryImportSummary(days_searched=days)
        try:
            images = sorted(self.graph.find_images_for_days(days), key=lambda image: image.created_at or "")
        except Exception as exc:
            LOGGER.exception("Failed to search Teams leadership chat for historical Operations screenshots")
            summary.errors.append(f"Teams search failed: {exc}")
            return summary
        summary.screenshots_found = len(images)
        imported_dates: set[str] = set()
        for image in images:
            try:
                result = self._import_history_image(
                    image,
                    dry_run=dry_run,
                    debug=debug,
                    force_reprocess=force_reprocess,
                )
                if result == "duplicate":
                    summary.duplicates_skipped += 1
                elif result == "manual_review":
                    summary.manual_review_required += 1
                    imported_dates.add(self._report_date(image.created_at))
                elif result == "imported":
                    summary.successfully_imported += 1
                    imported_dates.add(self._report_date(image.created_at))
            except Exception as exc:
                LOGGER.exception("Failed historical Operations import for Teams message %s", image.message_id)
                summary.errors.append(f"{image.created_at or 'unknown date'}: {exc}")
        summary.missing_days = self._missing_weekdays(days, imported_dates)
        if not dry_run:
            reports = self.db.reports_between()
            summary.historical_summary_text = format_historical_summary(build_historical_summary(reports))
        return summary

    def process_local_image(self, image_path: Path, report_date: str | None = None) -> str:
        self.initialize()
        content = image_path.read_bytes()
        digest = self._sha256(content)
        report_date = report_date or self._today()
        saved_path = self._save_local_copy(image_path, report_date, digest)
        screenshot = SavedScreenshot(
            message_id=f"local-{digest[:12]}",
            image_id=image_path.name,
            created_at=datetime.now(ZoneInfo(self.settings.timezone)).isoformat(),
            report_date=report_date,
            path=saved_path,
            sha256=digest,
        )
        self.db.save_screenshot(screenshot)
        return self._extract_store_and_summarize(screenshot)

    def _process_image(self, image: TeamsImage) -> bool:
        digest = self._sha256(image.content)
        if self.db.screenshot_exists(image.message_id, image.image_id, digest):
            LOGGER.info("Skipping already saved Teams screenshot %s/%s", image.message_id, image.image_id)
            return False
        screenshot = self._save_screenshot(image, digest)
        self.db.save_screenshot(screenshot)
        self._extract_store_and_summarize(screenshot)
        return True

    def _extract_store_and_summarize(self, screenshot: SavedScreenshot) -> str:
        if self.db.report_exists_for_hash(screenshot.sha256):
            existing = self.db.report_by_hash(screenshot.sha256)
            return existing["summary_text"] if existing else ""
        debug_dir = None
        if self.settings.ocr_debug:
            debug_dir = self.settings.reports_dir / "debug" / screenshot.report_date / screenshot.sha256[:12]
        report = self.ocr.extract(screenshot.path, screenshot.report_date, screenshot.sha256, debug_dir=debug_dir)
        previous = self.db.previous_report(report.report_date, report.screenshot_hash)
        history = build_historical_context(report.report_date, self.db.historical_reports_before(report.report_date))
        message = build_operations_message(report, previous, history=history.to_dict())
        self.db.save_report(report, message.text)
        self._write_text_report(report.report_date, build_detailed_report_text(report, previous))
        if self.settings.post_summary_to_teams:
            posted_message = self._post_quality_checked_message(report, message)
            if posted_message and not self.settings.dry_run:
                self.db.mark_report_posted(report.screenshot_hash)
        return message.text

    def _import_history_image(
        self,
        image: TeamsImage,
        *,
        dry_run: bool,
        debug: bool,
        force_reprocess: bool,
    ) -> str:
        digest = self._sha256(image.content)
        report_date = self._report_date(image.created_at)
        already_seen = self.db.screenshot_exists(image.message_id, image.image_id, digest)
        report_exists = self.db.report_exists_for_hash(digest)
        if already_seen and report_exists and not force_reprocess:
            LOGGER.info("Skipping duplicate historical screenshot %s/%s", image.message_id, image.image_id)
            return "duplicate"

        if dry_run:
            return "duplicate" if already_seen and not force_reprocess else "imported"

        screenshot = self._save_screenshot(image, digest)
        self.db.save_screenshot(screenshot)
        if report_exists and not force_reprocess:
            return "duplicate"

        debug_dir = None
        if self.settings.ocr_debug or debug:
            debug_dir = self.settings.reports_dir / "debug" / report_date / digest[:12]
        extractor = self._history_extractor(debug)
        report = extractor.extract(screenshot.path, report_date, digest, debug_dir=debug_dir)
        previous = self.db.previous_report(report.report_date, report.screenshot_hash)
        history = build_historical_context(report.report_date, self.db.historical_reports_before(report.report_date))
        message = build_operations_message(report, previous, history=history.to_dict())
        self.db.save_report(report, message.text)
        self._write_text_report(report.report_date, build_detailed_report_text(report, previous))
        return "imported" if report.passes_quality_gate else "manual_review"

    def _history_extractor(self, debug: bool) -> ScreenshotOcrExtractor:
        if not debug or self.settings.ocr_debug:
            return self.ocr
        return ScreenshotOcrExtractor(
            self.settings.ocr_command,
            self.settings.ocr_min_confidence,
            self.settings.collector_codes,
            True,
            self.settings.reports_dir / "debug",
        )

    def _post_quality_checked_message(self, report, message) -> bool:
        if report.passes_quality_gate:
            self.teams.send(message)
            return True
        if self.settings.low_quality_action == "alert":
            self.teams.send(build_manual_review_message(report))
            LOGGER.warning(
                "Low-quality Operations report generated manual-review alert for %s. Missing required fields: %s",
                report.report_date,
                ", ".join(report.missing_quality_fields),
            )
            return True
        LOGGER.warning(
            "Low-quality Operations report was not posted for %s. Missing required fields: %s",
            report.report_date,
            ", ".join(report.missing_quality_fields),
        )
        return False

    def _save_screenshot(self, image: TeamsImage, digest: str) -> SavedScreenshot:
        report_date = self._report_date(image.created_at)
        date_folder = self.settings.screenshots_dir / report_date
        date_folder.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(image.file_name)
        target = date_folder / f"{digest[:12]}-{safe_name}"
        target.write_bytes(image.content)
        return SavedScreenshot(
            message_id=image.message_id,
            image_id=image.image_id,
            created_at=image.created_at,
            report_date=report_date,
            path=target,
            sha256=digest,
        )

    def _save_local_copy(self, source: Path, report_date: str, digest: str) -> Path:
        date_folder = self.settings.screenshots_dir / report_date
        date_folder.mkdir(parents=True, exist_ok=True)
        target = date_folder / f"{digest[:12]}-{_safe_filename(source.name)}"
        if not target.exists():
            target.write_bytes(source.read_bytes())
        return target

    def _write_text_report(self, report_date: str, summary: str) -> None:
        self.settings.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.reports_dir / f"{report_date}.txt").write_text(summary)

    def _inside_daily_window(self) -> bool:
        now = datetime.now(ZoneInfo(self.settings.timezone)).time()
        start = _parse_time(self.settings.daily_scan_start)
        end = _parse_time(self.settings.daily_scan_end)
        return start <= now <= end

    def _report_date(self, created_at: str) -> str:
        if not created_at:
            return self._today()
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo(self.settings.timezone)).date().isoformat()

    def _today(self) -> str:
        return datetime.now(ZoneInfo(self.settings.timezone)).date().isoformat()

    def _sha256(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _missing_weekdays(self, days: int, imported_dates: set[str]) -> list[str]:
        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        missing: list[str] = []
        for offset in range(days):
            day = today.fromordinal(today.toordinal() - offset)
            if day.weekday() >= 5:
                continue
            value = day.isoformat()
            if value not in imported_dates and not self.db.screenshots_for_date(value):
                missing.append(value)
        return list(reversed(missing))


class _LeadershipTeamsSettings:
    def __init__(self, settings: OperationsSettings) -> None:
        self.dry_run = settings.dry_run
        self.teams_webhook_url = ""
        self.teams_post_method = "graph_chat"
        self.teams_chat_id = settings.leadership_chat_id
        self.teams_team_id = ""
        self.teams_channel_id = ""


def _parse_time(value: str):
    hour, minute = value.split(":", 1)
    from datetime import time

    return time(hour=int(hour), minute=int(minute))


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip(".-")
    return cleaned or "scollect-screenshot.png"
