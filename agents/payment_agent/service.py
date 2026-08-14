from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from shared.integrations.microsoft_teams import TeamsNotifier
from shared.integrations.microsoft_graph import GraphClient as MicrosoftGraphClient
from shared.utils.text import html_to_text, sanitize_filename

from .config import Settings
from .database import PaymentDatabase
from .graph_client import GraphClient
from .models import PaymentRecord
from .parser import parse_payment_email
from .reports import build_daily_report, build_realtime_alert, today_in_timezone

LOGGER = logging.getLogger(__name__)
PROCESSED_PAYMENTS_FOLDER = "Processed Payments"
DUPLICATE_PAYMENTS_FOLDER = "Duplicate Payments"


@dataclass(frozen=True)
class PaymentScanSummary:
    new_payment_count: int
    new_payment_total_cents: int
    teams_update_status: str
    scanned_at: str


class PaymentAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = PaymentDatabase(settings.database_path)
        self.graph = GraphClient(settings)
        self.teams = TeamsNotifier(settings, self._teams_graph_client())

    def initialize(self) -> None:
        self.db.initialize()
        if self.settings.save_email_html:
            self.settings.email_snapshot_dir.mkdir(parents=True, exist_ok=True)

    def scan_once(self) -> int:
        return self.scan_once_summary().new_payment_count

    def scan_once_summary(self) -> PaymentScanSummary:
        self.initialize()
        messages = self.graph.find_payment_messages()
        LOGGER.info("Found %s candidate payment email(s)", len(messages))
        processed_count = 0
        processed_total_cents = 0
        teams_attempted = False
        teams_sent = False
        teams_failed = False

        for message in messages:
            message_id = message["id"]
            internet_message_id = message.get("internetMessageId")
            message_notification_required = False
            if self.db.is_processed_email(message_id, internet_message_id):
                LOGGER.info("Duplicate payment email detected; skipping notification")
                self._cleanup_email(message_id, DUPLICATE_PAYMENTS_FOLDER)
                continue

            try:
                payment = self._message_to_payment(message)
                if self.db.is_duplicate_payment(payment, internet_message_id):
                    LOGGER.info("Duplicate payment detected; skipping notification")
                    self._cleanup_email(message_id, DUPLICATE_PAYMENTS_FOLDER)
                    continue
                self.db.save_payment(
                    payment,
                    internet_message_id=internet_message_id,
                    processed_at=datetime.now(timezone.utc),
                )
                processed_count += 1
                processed_total_cents += payment.payment_amount_cents
                LOGGER.info("Processed payment email.")
                notification_required = self.settings.realtime_enabled or self.settings.teams_post_method == "graph_chat"
                if notification_required:
                    message_notification_required = True
                    teams_attempted = True
                    self.teams.send(build_realtime_alert(payment))
                    teams_sent = True
                    self._cleanup_email(message_id, PROCESSED_PAYMENTS_FOLDER)
            except Exception as exc:
                if message_notification_required:
                    teams_failed = True
                LOGGER.warning("payment_message_processing_failed error_class=%s", type(exc).__name__)

        return PaymentScanSummary(
            new_payment_count=processed_count,
            new_payment_total_cents=processed_total_cents,
            teams_update_status=_teams_update_status(
                processed_count,
                dry_run=self.settings.dry_run,
                attempted=teams_attempted,
                sent=teams_sent,
                failed=teams_failed,
            ),
            scanned_at=datetime.now(timezone.utc).isoformat(),
        )

    def send_daily_report(self) -> None:
        self.initialize()
        report_date = today_in_timezone(self.settings.timezone)
        rows = self.db.payments_for_local_date(report_date)
        self.teams.send(build_daily_report(rows, report_date))

    def _message_to_payment(self, message: dict) -> PaymentRecord:
        body = message.get("body", {})
        content = body.get("content") or ""
        content_type = (body.get("contentType") or "").lower()
        text = html_to_text(content) if content_type == "html" else content
        parsed = parse_payment_email(text)
        snapshot_path = self._save_snapshot(message) if self.settings.save_email_html else None
        sender = self._sender_email(message)
        return PaymentRecord(
            message_id=message["id"],
            account_number=parsed.account_number,
            payment_type=parsed.payment_type,
            note=parsed.note,
            payment_date=parsed.payment_date,
            payment_amount_cents=parsed.payment_amount_cents,
            email_received_at=message["receivedDateTime"],
            email_subject=message.get("subject", ""),
            sender_email=sender,
            snapshot_path=snapshot_path,
        )

    def _save_snapshot(self, message: dict) -> str:
        safe_id = sanitize_filename(message["id"])
        path = self.settings.email_snapshot_dir / f"{safe_id}.html"
        body = message.get("body", {}).get("content") or ""
        path.write_text(body, encoding="utf-8")
        return str(path)

    def _sender_email(self, message: dict) -> str:
        return (
            message.get("from", {})
            .get("emailAddress", {})
            .get("address", "")
        )

    def _teams_graph_client(self) -> MicrosoftGraphClient | None:
        if self.settings.teams_post_method != "graph_chat":
            return self.graph
        return MicrosoftGraphClient(
            tenant_id=self.settings.teams_graph_tenant_id,
            client_id=self.settings.teams_graph_client_id,
            client_secret=self.settings.teams_graph_client_secret,
            delegated_token_cache_path=self.settings.teams_graph_token_cache_path,
        )

    def _cleanup_email(self, message_id: str, folder_name: str) -> None:
        try:
            self.graph.mark_user_message_read(self.settings.mailbox_user_id, message_id)
            self.graph.move_user_message(self.settings.mailbox_user_id, message_id, folder_name)
            if folder_name == PROCESSED_PAYMENTS_FOLDER:
                LOGGER.info("Email moved to Processed Payments")
            elif folder_name == DUPLICATE_PAYMENTS_FOLDER:
                LOGGER.info("Duplicate moved to Duplicate Payments")
        except Exception:
            LOGGER.exception("Email cleanup failed for folder %s", folder_name)


def _teams_update_status(
    new_payment_count: int,
    *,
    dry_run: bool,
    attempted: bool,
    sent: bool,
    failed: bool,
) -> str:
    if new_payment_count < 1:
        return "not_applicable"
    if dry_run and attempted:
        return "dry_run"
    if failed:
        return "failed"
    if sent:
        return "sent"
    if attempted:
        return "unknown"
    return "not_configured"
