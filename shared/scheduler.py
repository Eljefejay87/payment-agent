from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)


class AgentScheduler:
    def __init__(self) -> None:
        import schedule

        self._schedule = schedule
        self._running = False

    def every_minutes(self, minutes: int, job: Callable[[], object]) -> None:
        self._schedule.every(minutes).minutes.do(job)

    def every_day_at(self, run_time: str, job: Callable[[], object]) -> None:
        self._schedule.every().day.at(run_time).do(job)

    def every_day_at_in_timezone(
        self, run_time: str, job: Callable[[], object], timezone_name: str
    ) -> None:
        """Schedule a daily job at *run_time* in the given IANA timezone.

        The ``schedule`` library's ``.at()`` uses system local time (UTC on
        Railway). This method converts the wall-clock time in *timezone_name*
        to the corresponding UTC hour/minute and schedules via ``schedule`` so
        the job fires at the correct Eastern wall-clock time across DST changes.
        """
        tz = ZoneInfo(timezone_name)
        hours, minutes = (int(x) for x in run_time.split(":"))
        local = dt_time(hours, minutes)

        # Current offset in the target timezone (handles DST correctly).
        now_in_tz = datetime.now(tz)
        offset = now_in_tz.utcoffset()
        if offset is None:
            raise ValueError(f"Timezone {timezone_name} has no UTC offset")

        # Convert the target wall-clock time to UTC today.
        naive_today = datetime.combine(now_in_tz.date(), local)
        aware_today = naive_today.replace(tzinfo=tz)
        utc_today = aware_today.astimezone(timezone.utc)

        utc_hour = utc_today.hour
        utc_minute = utc_today.minute

        # If the UTC time is 00:xx (midnight), schedule at 00:xx.
        # (19:00 ET in EST = 00:00 UTC next day — valid for schedule.)
        trigger = f"{utc_hour:02d}:{utc_minute:02d}"
        LOGGER.info(
            "Scheduling daily report at %s %s → trigger time %s UTC (today's offset: %s)",
            run_time,
            timezone_name,
            trigger,
            offset,
        )
        self._schedule.every().day.at(trigger).do(job)

    def stop(self) -> None:
        self._running = False

    def run_forever(self) -> None:
        LOGGER.info("Scheduler started")
        self._running = True
        while self._running:
            self._schedule.run_pending()
            time.sleep(1)
        LOGGER.info("Scheduler stopped")
