from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from agents.cash_flow_hq.config import CashFlowHQSettings
from agents.cash_flow_hq.service import CashFlowHQService
from agents.cash_flow_hq.weekly_planner import WeeklyCashPlannerService
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
        planner: WeeklyCashPlannerService | None = None,
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
        self.planner = planner or WeeklyCashPlannerService(
            cash_flow_settings.cash_flow_planner_database_path,
            remit_settings.database_path,
        )

    def import_file(
        self,
        file_path: Path,
        liquidation_file: Path,
        dry_run: bool = False,
        planner_only: bool = False,
    ) -> ICRRemitResult:
        result = parse_icr_remit_file(file_path, self.remit_settings.broker_name, "Jim")
        if not liquidation_file.is_file():
            raise ValueError(f"ICR liquidation report was not found: {liquidation_file}")
        if dry_run:
            LOGGER.info("Dry run ICR remit import: Due to Client=%s", result.due_to_client)
            return result
        self.db.initialize()
        import_exists = self.db.import_exists(result.broker, result.remit_week.isoformat(), result.file_path.name)
        if import_exists:
            if planner_only:
                LOGGER.info("Planner-only ICR remit import already exists: %s", result.file_path.name)
                self.planner.create_plan_from_remit(result)
                return result
            raise RuntimeError(f"Duplicate ICR remit import for {result.file_path.name} week {result.remit_week}.")
        if planner_only:
            self.db.save_import(result)
            self.planner.create_plan_from_remit(result)
            LOGGER.info("Planner-only ICR remit import complete for %s", result.file_path.name)
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
            due_date=jim_remit_due_date(result).isoformat(),
            vendor_payee="ICR",
            category="Broker Remit",
            source="Jim Remit",
        )
        payload["Payment Type"] = {"select": {"name": "Manual"}}
        payload["Notes"] = {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": (
                            f"Due to Agency: ${result.due_to_agency:,.2f} | "
                            f"Due to Client (owed to Jim): ${result.due_to_client:,.2f} | "
                            f"Total Collected: ${result.total_collected:,.2f} | "
                            "ACH should be sent by Wednesday for Thursday arrival."
                        )
                    },
                }
            ]
        }
        self.cash_flow.notion.request("POST", "/pages", json={"parent": {"data_source_id": data_source_id}, "properties": payload})
        self.db.save_import(result)
        self.planner.create_plan_from_remit(result)
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


def jim_remit_due_date(result: ICRRemitResult) -> date:
    return result.remit_week + timedelta(days=3)
