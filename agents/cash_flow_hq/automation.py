from __future__ import annotations

import logging
from dataclasses import dataclass

from .email_scan import CashFlowEmailScanner, CashFlowScanResult
from .graph_client import CashFlowGraphClient
from .payment_scan import CashFlowPaymentScanner, PaymentScanResult
from .service import CashFlowHQService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CashFlowAutomationResult:
    bill_scan: CashFlowScanResult
    payment_scan: PaymentScanResult


def run_cash_flow_automation_once(
    service: CashFlowHQService,
    graph: CashFlowGraphClient,
    days: int,
    limit: int,
    dry_run: bool = False,
    debug: bool = False,
) -> CashFlowAutomationResult:
    bill_result = CashFlowEmailScanner(service, graph).scan(
        days=max(days, 1),
        limit=max(limit, 1),
        dry_run=dry_run,
        debug=debug,
    )
    payment_result = CashFlowPaymentScanner(service, graph).scan(
        days=max(days, 1),
        limit=max(limit, 1),
        dry_run=dry_run,
        debug=debug,
    )
    LOGGER.info(
        (
            "Cash Flow HQ automation run complete. "
            "Bills imported=%s bill_needs_review=%s bill_skipped=%s "
            "payments_marked=%s payment_needs_review=%s payment_skipped=%s errors=%s"
        ),
        len(bill_result.imported),
        len(bill_result.flagged),
        len(bill_result.skipped),
        len(payment_result.marked_paid),
        len(payment_result.needs_review),
        len(payment_result.skipped),
        len(bill_result.errors) + len(payment_result.errors),
    )
    return CashFlowAutomationResult(bill_result, payment_result)
