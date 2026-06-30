from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.integrations.microsoft_graph import GraphClient
from shared.integrations.microsoft_teams import TeamsNotifier

from .config import RemitSettings
from .database import RemitDatabase
from .file_detector import RemitFileValidationError, find_required_remit_files
from .models import RemitBatch, RemitFiles
from .reports import build_broker_email_html, build_broker_email_subject, build_owner_teams_message

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
        if not force and not self._is_send_window(now):
            LOGGER.info("Weekly Remit Agent waiting for Monday send window")
            return False

        week_start = self._week_start(now)
        sent_date = now.date().isoformat()
        try:
            files = find_required_remit_files(
                self.settings.incoming_folder,
                self.settings.remit_filename_contains,
                self.settings.liquidation_filename_contains,
                self.settings.allowed_extensions,
            )
        except RemitFileValidationError as exc:
            LOGGER.info("Weekly remit files not ready: %s", exc)
            return False

        if self.db.batch_exists(self.settings.broker_name, week_start):
            LOGGER.info("Duplicate weekly remit detected for %s week %s", self.settings.broker_name, week_start)
            self._move_files(files, self._dated_folder(self.settings.duplicate_folder, sent_date))
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

        self._send_broker_email(batch)
        self.db.save_sent_batch(batch)
        self._send_owner_update(batch)
        self._move_files(batch.files, self._dated_folder(self.settings.sent_folder, batch.sent_date))
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

    def _send_owner_update(self, batch: RemitBatch) -> None:
        if not self.settings.send_owner_teams_update:
            return
        try:
            self.teams.send(build_owner_teams_message(batch))
        except Exception:
            LOGGER.exception("Owner Teams remit confirmation failed")

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
