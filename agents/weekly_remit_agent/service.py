from __future__ import annotations

import hashlib
import logging
import shutil
import time as time_module
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.integrations.microsoft_graph import GraphClient
from shared.integrations.microsoft_teams import TeamsNotifier

from .config import RemitSettings
from .database import RemitDatabase
from .file_detector import RemitFileValidationError, find_required_remit_files
from .models import RemitBatch, RemitFiles, RemitRunStatus
from .reports import build_broker_email_html, build_broker_email_subject, build_remit_status_teams_message

LOGGER = logging.getLogger(__name__)


class WeeklyRemitAgent:
    def __init__(self, settings: RemitSettings) -> None:
        self.settings = settings
        self.db = RemitDatabase(settings.database_path)
        self.graph = GraphClient(
            tenant_id=settings.graph_tenant_id,
            client_id=settings.graph_client_id,
            client_secret=settings.graph_client_secret,
        )
        self.teams_graph = GraphClient(
            tenant_id=settings.teams_graph_tenant_id,
            client_id=settings.teams_graph_client_id,
            client_secret=settings.teams_graph_client_secret,
            delegated_token_cache_path=settings.teams_graph_token_cache_path,
        )
        self.teams = TeamsNotifier(_OwnerTeamsSettings(settings), self.teams_graph)

    def initialize(self) -> None:
        self.db.initialize()
        self.settings.incoming_folder.mkdir(parents=True, exist_ok=True)
        self.settings.sent_folder.mkdir(parents=True, exist_ok=True)
        self.settings.failed_folder.mkdir(parents=True, exist_ok=True)
        self.settings.duplicate_folder.mkdir(parents=True, exist_ok=True)

    def scan_once(self, force: bool = False) -> bool:
        self.initialize()
        now = self._now()
        started_at = time_module.monotonic()
        week_start = self._week_start(now)
        sent_date = now.date().isoformat()
        if not force and not self._is_send_window(now):
            if self._is_deadline_missed(now) and not self.db.batch_exists(self.settings.broker_name, week_start):
                remit_found, liquidation_found = self._file_availability()
                self._send_status_update(
                    week_start,
                    self._run_status(
                        now,
                        started_at,
                        remit_found=remit_found,
                        liquidation_found=liquidation_found,
                        attachments_sent=0,
                        archive_result="Not attempted",
                        final_status="Deadline missed",
                    ),
                )
            LOGGER.info("Weekly Remit Agent waiting for Monday send window")
            return False

        try:
            files = find_required_remit_files(
                self.settings.incoming_folder,
                self.settings.remit_filename_contains,
                self.settings.liquidation_filename_contains,
                self.settings.allowed_extensions,
            )
        except RemitFileValidationError as exc:
            LOGGER.info("Weekly remit files not ready: %s", exc)
            remit_found, liquidation_found = self._file_availability()
            self._send_status_update(
                week_start,
                self._run_status(
                    now,
                    started_at,
                    remit_found=remit_found,
                    liquidation_found=liquidation_found,
                    attachments_sent=0,
                    archive_result="Not attempted",
                    final_status=self._validation_failure_status(exc),
                ),
            )
            return False

        if self.db.batch_exists(self.settings.broker_name, week_start):
            LOGGER.info("Duplicate weekly remit detected for %s week %s", self.settings.broker_name, week_start)
            self._move_files(files, self._dated_folder(self.settings.duplicate_folder, sent_date))
            self._send_status_update(
                week_start,
                self._run_status(
                    now,
                    started_at,
                    remit_found=True,
                    liquidation_found=True,
                    attachments_sent=0,
                    archive_result="Archived to duplicate folder",
                    final_status="Duplicate remit",
                ),
            )
            return False

        batch = RemitBatch(
            broker_name=self.settings.broker_name,
            recipient_email=self.settings.broker_email,
            week_start=week_start,
            sent_date=sent_date,
            files=files,
            remit_hash=self._file_hash(files.remit),
            liquidation_hash=self._file_hash(files.liquidation),
        )

        try:
            self._send_broker_email(batch)
        except Exception:
            self._send_status_update(
                week_start,
                self._run_status(
                    now,
                    started_at,
                    remit_found=True,
                    liquidation_found=True,
                    attachments_sent=0,
                    archive_result="Not attempted",
                    final_status="Email send failed",
                ),
            )
            raise

        try:
            self.db.save_sent_batch(batch)
        except Exception:
            self._send_status_update(
                week_start,
                self._run_status(
                    now,
                    started_at,
                    remit_found=True,
                    liquidation_found=True,
                    attachments_sent=0,
                    archive_result="Not attempted",
                    final_status="Validation failed",
                ),
            )
            raise

        try:
            self._move_files(batch.files, self._dated_folder(self.settings.sent_folder, batch.sent_date))
        except Exception:
            self._send_status_update(
                week_start,
                self._run_status(
                    now,
                    started_at,
                    remit_found=True,
                    liquidation_found=True,
                    attachments_sent=len(batch.files.attachments) if not self.settings.dry_run else 0,
                    archive_result="Archive failed",
                    final_status="Validation failed",
                ),
            )
            raise

        self._send_status_update(
            week_start,
            self._run_status(
                now,
                started_at,
                remit_found=True,
                liquidation_found=True,
                attachments_sent=len(batch.files.attachments) if not self.settings.dry_run else 0,
                archive_result="Archived",
                final_status="Success" if not self.settings.dry_run else "Success (DRY RUN preview)",
            ),
        )
        LOGGER.info("Weekly remit sent for %s week %s", batch.broker_name, batch.week_start)
        return True

    def _send_broker_email(self, batch: RemitBatch) -> None:
        if self.settings.dry_run:
            LOGGER.info(
                "DRY RUN weekly remit email to %s with %s attachment(s)",
                batch.recipient_email,
                len(batch.files.attachments),
            )
            return
        self.graph.send_user_mail(
            mailbox_user_id=self.settings.mailbox_user_id,
            to_recipients=[batch.recipient_email],
            subject=build_broker_email_subject(batch),
            html_content=build_broker_email_html(batch),
            attachments=batch.files.attachments,
        )

    def _send_status_update(self, week_start: str, status: RemitRunStatus) -> None:
        if not self.settings.send_owner_teams_update:
            return
        if not self.settings.dry_run and not self.db.reserve_status_notification(
            self.settings.broker_name,
            week_start,
            status.final_status,
        ):
            LOGGER.info("Weekly remit Teams status already recorded for %s", status.final_status)
            return
        try:
            self.teams.send(build_remit_status_teams_message(status))
        except Exception:
            LOGGER.exception("Owner Teams remit status update failed")

    def _run_status(
        self,
        now: datetime,
        started_at: float,
        *,
        remit_found: bool,
        liquidation_found: bool,
        attachments_sent: int,
        archive_result: str,
        final_status: str,
    ) -> RemitRunStatus:
        return RemitRunStatus(
            broker_name=self.settings.broker_name,
            recipient_email=self.settings.broker_email,
            remit_found=remit_found,
            liquidation_found=liquidation_found,
            attachments_sent=attachments_sent,
            send_time=now.isoformat(timespec="seconds"),
            processing_seconds=time_module.monotonic() - started_at,
            archive_result=archive_result,
            final_status=final_status,
            dry_run=self.settings.dry_run,
        )

    def _file_availability(self) -> tuple[bool, bool]:
        files = [
            path
            for path in self.settings.incoming_folder.iterdir()
            if path.is_file() and path.suffix.lower() in self.settings.allowed_extensions
        ]
        return (
            any(self.settings.remit_filename_contains.lower() in path.name.lower() for path in files),
            any(self.settings.liquidation_filename_contains.lower() in path.name.lower() for path in files),
        )

    def _validation_failure_status(self, error: RemitFileValidationError) -> str:
        value = str(error).lower()
        if "missing remit" in value:
            return "Missing United Remit"
        if "missing liquidation" in value:
            return "Missing United Liq"
        return "Validation failed"

    def _move_files(self, files: RemitFiles, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for source in files.attachments:
            target = self._unique_destination(destination / source.name)
            shutil.move(str(source), str(target))
            LOGGER.info("Moved remit file to %s", target)

    def _unique_destination(self, target: Path) -> Path:
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        for counter in range(1, 1000):
            candidate = target.with_name(f"{stem}-{counter}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not create unique archive filename for {target.name}")

    def _dated_folder(self, base_folder: Path, sent_date: str) -> Path:
        return base_folder / sent_date

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self.settings.timezone))

    def _week_start(self, now: datetime) -> str:
        monday = now.date() - timedelta(days=now.weekday())
        return monday.isoformat()

    def _is_send_window(self, now: datetime) -> bool:
        if now.strftime("%A").lower() != self.settings.run_day:
            return False
        return now.time() <= self._deadline_time()

    def _is_deadline_missed(self, now: datetime) -> bool:
        return now.strftime("%A").lower() == self.settings.run_day and now.time() > self._deadline_time()

    def _deadline_time(self) -> time:
        hour, minute = self.settings.send_deadline.split(":", 1)
        return time(hour=int(hour), minute=int(minute))

    def _file_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class _OwnerTeamsSettings:
    def __init__(self, settings: RemitSettings) -> None:
        self.dry_run = settings.dry_run
        self.teams_webhook_url = ""
        self.teams_post_method = "graph_chat"
        self.teams_chat_id = settings.owner_teams_chat_id
        self.teams_team_id = ""
        self.teams_channel_id = ""
