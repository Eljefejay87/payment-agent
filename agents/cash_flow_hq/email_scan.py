from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .graph_client import CashFlowGraphClient
from .models import BillCandidate
from .parser import parse_bill_candidate
from .service import CashFlowHQService

LOGGER = logging.getLogger(__name__)


@dataclass
class CashFlowScanResult:
    would_import: list[BillCandidate] = field(default_factory=list)
    imported: list[BillCandidate] = field(default_factory=list)
    skipped: list[BillCandidate] = field(default_factory=list)
    flagged: list[BillCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class CashFlowEmailScanner:
    def __init__(self, cash_flow: CashFlowHQService, graph: CashFlowGraphClient) -> None:
        self.cash_flow = cash_flow
        self.graph = graph

    def scan(self, days: int, limit: int, dry_run: bool = False, debug: bool = False) -> CashFlowScanResult:
        result = CashFlowScanResult()
        foundation = self.cash_flow.get_existing_foundation() if dry_run else self.cash_flow.ensure_runtime_foundation()
        data_source_id = foundation["data_source_id"]
        vendor_rules_foundation = (
            self.cash_flow.get_existing_vendor_rules_foundation()
            if dry_run
            else foundation.get("vendor_rules_foundation")
        )
        vendor_rules = (
            self.cash_flow.list_vendor_rules(vendor_rules_foundation["data_source_id"])
            if vendor_rules_foundation
            else []
        )
        messages = self.graph.find_bill_messages(days=days, limit=limit)
        LOGGER.info("Cash Flow HQ found %s bill-related Outlook email(s).", len(messages))

        for message in messages:
            try:
                candidate = parse_bill_candidate(message)
                candidate = self.cash_flow.apply_vendor_rules(candidate, message, vendor_rules)
                if debug:
                    LOGGER.info(
                        (
                            "Candidate subject=%r sender=%r body_source=%s attachments=%s "
                            "vendor=%r amount=%s amount_source=%s due_date=%s due_date_source=%s "
                            "status=%s needs_review_reason=%s"
                        ),
                        candidate.expense_name,
                        message.sender_email,
                        message.body_source,
                        [attachment.name for attachment in message.attachments],
                        candidate.vendor_payee,
                        candidate.amount,
                        candidate.field_sources.get("amount") or "none",
                        candidate.due_date,
                        candidate.field_sources.get("due_date") or "none",
                        candidate.status,
                        candidate.review_reason_text or "none",
                    )
                if self.cash_flow.email_bill_exists(data_source_id, candidate):
                    result.skipped.append(candidate)
                    LOGGER.info("Skipped duplicate bill email: %s", candidate.expense_name)
                    continue
                if candidate.status == "Needs Review":
                    result.flagged.append(candidate)
                    LOGGER.info(
                        "Flagged bill email for review: %s. Needs Review reason: %s",
                        candidate.expense_name,
                        candidate.review_reason_text or "low confidence",
                    )
                if dry_run:
                    if candidate.status != "Needs Review":
                        result.would_import.append(candidate)
                        LOGGER.info("Dry run would import bill email: %s", candidate.expense_name)
                    continue
                self.cash_flow.create_email_bill(data_source_id, candidate)
                result.imported.append(candidate)
                LOGGER.info("Imported bill email into Cash Flow HQ: %s", candidate.expense_name)
            except Exception as exc:
                result.errors.append(f"{message.subject}: {exc}")
                LOGGER.exception("Could not process bill email: %s", message.subject)
        return result


# TODO Phase 3: add payment confirmation detection as a separate pass that can
# propose Payment Date updates for review without automatically marking rows Paid.

# TODO Daily schedule: wire this scanner into the existing scheduler at 10:00 AM
# after the user approves recurring Cash Flow HQ automation.
