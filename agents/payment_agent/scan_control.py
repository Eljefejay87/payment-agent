from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .service import PaymentAgent

LOGGER = logging.getLogger(__name__)


class PaymentScanController:
    """Coordinate explicit payment scans without duplicating scan logic."""

    def __init__(
        self,
        agent: PaymentAgent,
        *,
        cooldown_seconds: int = 120,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.agent = agent
        self.cooldown = timedelta(seconds=max(1, cooldown_seconds))
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._running = False
        self._last_started_at: datetime | None = None

    def run(self) -> dict[str, Any]:
        started_at = self.now()
        with self._lock:
            if self._running:
                LOGGER.info("payment_scan_control result=already_running")
                return {
                    "status": "already_running",
                    "scan_timestamp": started_at.isoformat(),
                }
            if self._last_started_at and started_at - self._last_started_at < self.cooldown:
                retry_after = max(1, int((self.cooldown - (started_at - self._last_started_at)).total_seconds()))
                LOGGER.info("payment_scan_control result=cooldown")
                return {
                    "status": "cooldown",
                    "retry_after_seconds": retry_after,
                    "scan_timestamp": started_at.isoformat(),
                }
            self._running = True
            self._last_started_at = started_at

        try:
            summary = self.agent.scan_once_summary()
            LOGGER.info(
                "payment_scan_control result=completed new_payment_count=%s teams_update_status=%s",
                summary.new_payment_count,
                summary.teams_update_status,
            )
            return {
                "status": "completed",
                "new_payment_count": summary.new_payment_count,
                "new_payment_total": round(summary.new_payment_total_cents / 100, 2),
                "teams_update_status": summary.teams_update_status,
                "scan_timestamp": summary.scanned_at,
            }
        except Exception as exc:
            LOGGER.warning("payment_scan_control result=failed error_class=%s", type(exc).__name__)
            return {
                "status": "failed",
                "error": "payment_scan_failed",
                "scan_timestamp": self.now().isoformat(),
            }
        finally:
            with self._lock:
                self._running = False

    def run_scheduled(self) -> int:
        result = self.run()
        if result.get("status") == "completed":
            return int(result.get("new_payment_count") or 0)
        if result.get("status") in {"cooldown", "already_running"}:
            return 0
        raise RuntimeError("Payment scan failed.")
