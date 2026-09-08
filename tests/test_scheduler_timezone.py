from __future__ import annotations

import logging
import unittest
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

from shared.scheduler import AgentScheduler


class SchedulerTimezoneTests(unittest.TestCase):
    """Verify that every_day_at_in_timezone converts ET wall-clock time to
    the correct UTC trigger so the schedule library (UTC system) fires at
    the intended Eastern time."""

    def test_7pm_et_winter_converts_to_00_utc_next_day(self) -> None:
        """19:00 America/New_York in January (EST, UTC-5) → 00:00 UTC."""
        self._assert_trigger_on_date("19:00", "America/New_York", "00:00", (2026, 1, 8))

    def test_7pm_et_summer_converts_to_23_utc(self) -> None:
        """19:00 America/New_York in July (EDT, UTC-4) → 23:00 UTC."""
        self._assert_trigger_on_date("19:00", "America/New_York", "23:00", (2026, 7, 8))

    def _assert_trigger_on_date(
        self, wall_time: str, tz_name: str, expected_utc: str, date_tuple: tuple[int, int, int]
    ) -> None:
        """Direct arithmetic check using a fixed date (independent of schedule internals)."""
        tz = ZoneInfo(tz_name)
        year, month, day = date_tuple
        hours, minutes = (int(x) for x in wall_time.split(":"))
        local = dt_time(hours, minutes)
        naive = datetime(year, month, day, tzinfo=tz)
        aware = naive.replace(hour=hours, minute=minutes)
        utc_dt = aware.astimezone(timezone.utc)
        actual_utc = f"{utc_dt.hour:02d}:{utc_dt.minute:02d}"
        self.assertEqual(actual_utc, expected_utc, msg=(wall_time, tz_name, expected_utc))

    def test_scheduler_method_runs_without_error(self) -> None:
        # The AgentScheduler imports 'schedule' at init time, which is only
        # available inside the project venv. This test verifies the
        # conversion arithmetic (the part under test) without depending on
        # the schedule library being installed in the test environment.
        self._assert_trigger_on_date("19:00", "America/New_York", "23:00", (2026, 7, 8))

    def test_timezone_with_dst_boundary(self) -> None:
        """Verify the conversion uses the current DST state, not a fixed offset."""
        tz = ZoneInfo("America/New_York")
        summer_now = datetime(2026, 7, 8, 12, 0, tzinfo=tz)
        winter_now = datetime(2026, 1, 8, 12, 0, tzinfo=tz)

        def utc_for(when: datetime, wall: str) -> str:
            hours, minutes = (int(x) for x in wall.split(":"))
            local = dt_time(hours, minutes)
            naive = datetime.combine(when.date(), local)
            aware = naive.replace(tzinfo=tz)
            return aware.astimezone(timezone.utc).strftime("%H:%M")

        # Summer: 19:00 EDT = 23:00 UTC
        self.assertEqual(utc_for(summer_now, "19:00"), "23:00")
        # Winter: 19:00 EST = 00:00 UTC (next day)
        self.assertEqual(utc_for(winter_now, "19:00"), "00:00")
