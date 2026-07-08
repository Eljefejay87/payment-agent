from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config import load_environment

NOTION_SETUP_MESSAGE = (
    "Cash Flow HQ Notion setup is incomplete. Add the missing Notion values "
    "to .env before running cashflow-scan-email."
)


@dataclass(frozen=True)
class CashFlowHQSettings:
    log_level: str
    notion_api_key: str
    notion_parent_page_id: str
    notion_version: str
    database_name: str
    mailbox_user_id: str
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str


def load_cash_flow_settings(env_file: str | None = None) -> CashFlowHQSettings:
    load_environment(env_file)
    return CashFlowHQSettings(
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        notion_api_key=os.getenv("NOTION_API_KEY", ""),
        notion_parent_page_id=os.getenv("CASH_FLOW_HQ_PARENT_PAGE_ID", ""),
        notion_version=os.getenv("NOTION_VERSION", "2026-03-11"),
        database_name=os.getenv("CASH_FLOW_HQ_DATABASE_NAME", "Cash Flow HQ"),
        mailbox_user_id=os.getenv("CASH_FLOW_HQ_MAILBOX_USER_ID", os.getenv("MAILBOX_USER_ID", "")),
        graph_tenant_id=os.getenv("MS_GRAPH_TENANT_ID", ""),
        graph_client_id=os.getenv("MS_GRAPH_CLIENT_ID", ""),
        graph_client_secret=os.getenv("MS_GRAPH_CLIENT_SECRET", ""),
    )


def validate_cash_flow_settings(settings: CashFlowHQSettings, include_graph: bool = False) -> list[str]:
    errors: list[str] = []
    if not settings.notion_api_key:
        errors.append("NOTION_API_KEY is required. Use the Notion integration or personal access token.")
    if not settings.notion_parent_page_id:
        errors.append("CASH_FLOW_HQ_PARENT_PAGE_ID is required. Use the Notion parent page ID shared with the integration.")
    if not settings.database_name:
        errors.append("CASH_FLOW_HQ_DATABASE_NAME is required. Default is Cash Flow HQ.")
    if include_graph:
        if not settings.mailbox_user_id:
            errors.append("CASH_FLOW_HQ_MAILBOX_USER_ID or MAILBOX_USER_ID is required.")
        if not settings.graph_tenant_id:
            errors.append("MS_GRAPH_TENANT_ID is required.")
        if not settings.graph_client_id:
            errors.append("MS_GRAPH_CLIENT_ID is required.")
        if not settings.graph_client_secret:
            errors.append("MS_GRAPH_CLIENT_SECRET is required.")
    return errors
