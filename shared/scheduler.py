from __future__ import annotations

import logging
import time
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)


class AgentScheduler:
    def __init__(self) -> None:
        import schedule

        self._schedule = schedule

    def every_minutes(self, minutes: int, job: Callable[[], object]) -> None:
        self._schedule.every(minutes).minutes.do(job)

    def every_day_at(self, run_time: str, job: Callable[[], object]) -> None:
        self._schedule.every().day.at(run_time).do(job)

    def run_forever(self) -> None:
        LOGGER.info("Scheduler started")
        while True:
            self._schedule.run_pending()
            time.sleep(1)
