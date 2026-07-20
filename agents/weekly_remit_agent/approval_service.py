"""Approval-only Weekly Remit preview service. It never sends or archives files."""

from __future__ import annotations

import logging
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

from .database import RemitDatabase
from .file_detector import RemitFileValidationError, find_required_remit_files
from .models import RemitApprovalPreview, RemitBatch
from .reports import build_broker_email_subject

LOGGER = logging.getLogger(__name__)
PENDING = "pending"


class WeeklyRemitApprovalService:
    def __init__(self, settings, *, now=lambda: datetime.now(timezone.utc), id_factory=lambda: secrets.token_urlsafe(24), ttl=timedelta(hours=24)) -> None:
        self.settings = settings
        self.db = RemitDatabase(settings.database_path)
        self.now = now
        self.id_factory = id_factory
        self.ttl = ttl

    def create_preview(self, user_id: str) -> tuple[str, RemitApprovalPreview | None]:
        self.db.initialize()
        now = self.now()
        try:
            files = find_required_remit_files(self.settings.incoming_folder, self.settings.remit_filename_contains, self.settings.liquidation_filename_contains, self.settings.allowed_extensions)
        except RemitFileValidationError:
            LOGGER.info("weekly_remit_approval action=preview result=files_unavailable")
            return "files_unavailable", None
        week_start = _week_start(now)
        if self.db.batch_exists(self.settings.broker_name, week_start):
            LOGGER.info("weekly_remit_approval action=preview result=duplicate")
            return "duplicate", None
        batch = RemitBatch(self.settings.broker_name, self.settings.broker_email, week_start, now.date().isoformat(), files, _hash(files.remit), _hash(files.liquidation))
        preview = RemitApprovalPreview(
            approval_id=self.id_factory(), created_at=now.isoformat(), expires_at=(now + self.ttl).isoformat(),
            authorized_user_id=str(user_id), broker_name=batch.broker_name, recipient_email=batch.recipient_email,
            week_start=batch.week_start, subject=build_broker_email_subject(batch), remit_filename=files.remit.name,
            liquidation_filename=files.liquidation.name, remit_hash=batch.remit_hash,
            liquidation_hash=batch.liquidation_hash, status=PENDING,
        )
        self.db.save_approval(preview)
        LOGGER.info("weekly_remit_approval action=preview result=created")
        return "created", preview

    def approve(self, user_id: str, approval_id: str) -> tuple[str, RemitApprovalPreview | None]:
        return self._resolve(user_id, approval_id, "approved_pending_send")

    def cancel(self, user_id: str, approval_id: str) -> tuple[str, RemitApprovalPreview | None]:
        return self._resolve(user_id, approval_id, "cancelled", verify_files=False)

    def _resolve(self, user_id: str, approval_id: str, status: str, *, verify_files: bool = True) -> tuple[str, RemitApprovalPreview | None]:
        preview = self.db.get_approval(approval_id)
        if not preview or preview.authorized_user_id != str(user_id) or preview.status != PENDING:
            return "not_pending", None
        now = self.now()
        if datetime.fromisoformat(preview.expires_at) <= now:
            self.db.update_approval_status(approval_id, str(user_id), PENDING, "expired", now.isoformat())
            return "expired", None
        if verify_files and not self._files_match(preview):
            self.db.update_approval_status(approval_id, str(user_id), PENDING, "invalidated", now.isoformat())
            LOGGER.info("weekly_remit_approval action=approve result=file_mismatch")
            return "file_mismatch", None
        if not self.db.update_approval_status(approval_id, str(user_id), PENDING, status, now.isoformat()):
            return "not_pending", None
        LOGGER.info("weekly_remit_approval action=%s result=updated", "approve" if status.startswith("approved") else "cancel")
        return status, self.db.get_approval(approval_id)

    def _files_match(self, preview: RemitApprovalPreview) -> bool:
        try:
            files = find_required_remit_files(self.settings.incoming_folder, self.settings.remit_filename_contains, self.settings.liquidation_filename_contains, self.settings.allowed_extensions)
        except RemitFileValidationError:
            return False
        candidate = RemitBatch(self.settings.broker_name, self.settings.broker_email, _week_start(self.now()), self.now().date().isoformat(), files, _hash(files.remit), _hash(files.liquidation))
        return (
            candidate.broker_name == preview.broker_name and candidate.recipient_email == preview.recipient_email
            and candidate.week_start == preview.week_start and build_broker_email_subject(candidate) == preview.subject
            and files.remit.name == preview.remit_filename and files.liquidation.name == preview.liquidation_filename
            and candidate.remit_hash == preview.remit_hash and candidate.liquidation_hash == preview.liquidation_hash
        )


def _hash(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _week_start(now: datetime) -> str:
    return (now.date() - timedelta(days=now.weekday())).isoformat()
