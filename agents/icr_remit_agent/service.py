from __future__ import annotations

import logging
from pathlib import Path

from agents.cash_flow_hq.config import CashFlowHQSettings
from agents.cash_flow_hq.service import CashFlowHQService
from agents.weekly_remit_agent.config import RemitSettings
from shared.integrations.microsoft_graph import GraphClient

from .database import ICRRemitDatabase
from .models import ICRRemitResult
from .parser import parse_icr_remit_file

LOGGER = logging.getLogger(__name__)


class ICRRemitImportService:
    def __init__(
        self,
        remit_settings: RemitSettings,
        cash_flow_settings: CashFlowHQSettings,
        cash_flow: CashFlowHQService | None = None,
        graph: GraphClient | None = None,
    ) -> None:
        self.remit_settings = remit_settings
        self.cash_flow_settings = cash_flow_settings
        self.db = ICRRemitDatabase(remit_settings.database_path)
        self.cash_flow = cash_flow or CashFlowHQService(cash_flow_settings)
        self.graph = graph or GraphClient(
            tenant_id=remit_settings.graph_tenant_id,
            client_id=remit_settings.graph_client_id,
            client_secret=remit_settings.graph_client_secret,
        )

    def import_file(self, file_path: Path, liquidation_file: Path, dry_run: bool = False) -> ICRRemitResult:
        result = parse_icr_remit_file(file_path, self.remit_settings.broker_name, "Jim")
        if not liquidation_file.is_file():
            raise ValueError(f"ICR liquidation report was not found: {liquidation_file}")
        self.db.initialize()
        if self.db.import_exists(result.broker, result.remit_week.isoformat(), result.file_path.name):
            raise RuntimeError(f"Duplicate ICR remit import for {result.file_path.name} week {result.remit_week}.")
        if dry_run:
            LOGGER.info("Dry run ICR remit import: Due to Client=%s", result.due_to_client)
            return result
        data_source_id = self.cash_flow_settings.cash_flow_data_source_id
        if not data_source_id:
            foundation = self.cash_flow.find_cash_flow_foundation()
            if foundation is None:
                raise RuntimeError("Cash Flow HQ foundation was not found. Run cash-flow-init before importing an ICR remit.")
            data_source_id = foundation["data_source_id"]
        payload = self.cash_flow.create_manual_expense_payload(
            expense_name=f"ICR Weekly Remit - {result.remit_week.isoformat()}",
            amount=float(result.due_to_client),
            due_date=result.remit_week.isoformat(),
            vendor_payee="ICR",
            category="Broker Remit",
            source="Jim Remit",
        )
        payload["Payment Type"] = {"select": {"name": "Manual"}}
        payload["Notes"] = {"rich_text": [{"type": "text", "text": {"content": "Weekly ICR remit owed to Jim"}}]}
        self.cash_flow.notion.request("POST", "/pages", json={"parent": {"data_source_id": data_source_id}, "properties": payload})
        self.db.save_import(result)
        self.create_email_draft(result, liquidation_file)
        LOGGER.info("ICR remit import complete for %s", result.file_path.name)
        return result

    def create_email_draft(self, result: ICRRemitResult, liquidation_file: Path) -> dict:
        if not self.remit_settings.broker_email:
            raise RuntimeError("REMIT_BROKER_EMAIL is required to create the ICR draft.")
        subject = f"Weekly ICR Remit - {result.week_ending.isoformat()}"
        body = (
            "<p>Hi Jim,</p>"
            "<p>Attached are United Capital Management's weekly ICR remit report and "
            f"liquidation report for the week of {result.remit_week.isoformat()}.</p>"
            "<p><strong>Attached files:</strong></p>"
            f"<p>{result.file_path.name}<br>{liquidation_file.name}</p>"
            "<p>Please let us know if you need anything else.</p>"
            "<p>Thank you,<br>United Capital Management</p>"
        )
        return self.graph.create_user_mail_draft(
            mailbox_user_id=self.remit_settings.mailbox_user_id,
            to_recipients=[self.remit_settings.broker_email],
            subject=subject,
            html_content=body,
            attachments=[result.file_path, liquidation_file],
        )
