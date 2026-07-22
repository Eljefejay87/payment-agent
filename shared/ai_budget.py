"""Shared local budget guard for OpenAI-powered UCM work."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


MONTHLY_BUDGET = Decimal("20.00")
WARNING_LEVELS = (
    (Decimal("20.00"), "hard stop"),
    (Decimal("18.00"), "90% warning"),
    (Decimal("15.00"), "75% warning"),
    (Decimal("10.00"), "50% warning"),
)
AGENTS = (
    "Payment Agent",
    "Operations Intelligence",
    "Cash Flow HQ",
    "Voicemail Tracker",
    "Shared Data Sync",
    "Other/Unknown",
)
DEFAULT_DATABASE_PATH = (
    "~/Library/Application Support/UCM/payment-agent/ai_budget.sqlite3"
)
TRANSCRIPTION_RATES = {
    "gpt-4o-mini-transcribe": Decimal("0.003"),
    "gpt-4o-transcribe": Decimal("0.006"),
    "whisper-1": Decimal("0.006"),
}
UNKNOWN_TRANSCRIPTION_RATE = Decimal("0.01")


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    estimated_cost: Decimal
    spend_this_month: Decimal
    remaining_budget: Decimal
    warning: str | None
    reason: str | None = None


@dataclass(frozen=True)
class AgentUsage:
    estimated_cost: Decimal = Decimal("0")
    request_count: int = 0
    blocked_count: int = 0


@dataclass(frozen=True)
class BudgetStatus:
    month: str
    spend_today: Decimal
    spend_this_month: Decimal
    monthly_budget: Decimal
    remaining_budget: Decimal
    percentage_used: Decimal
    kill_switch_active: bool
    manually_paused: bool
    usage_by_agent: dict[str, AgentUsage]


class AIBudgetGuard:
    """Atomically reserve estimated OpenAI cost before a request is sent."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        timezone_name: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        configured_path = database_path or os.getenv(
            "AI_BUDGET_DATABASE_PATH", DEFAULT_DATABASE_PATH
        )
        self.database_path = Path(configured_path).expanduser()
        self.timezone = ZoneInfo(timezone_name or os.getenv("TIMEZONE", "America/New_York"))
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def reserve(
        self,
        *,
        agent: str,
        estimated_cost: Decimal | str | float,
        model: str,
    ) -> BudgetDecision:
        """Record one allowed or blocked attempt and return its admission decision."""
        cost = _money(estimated_cost)
        if cost < 0:
            raise ValueError("estimated_cost cannot be negative")
        normalized_agent = agent if agent in AGENTS else "Other/Unknown"
        now = self._now()
        month = now.astimezone(self.timezone).strftime("%Y-%m")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT paused, reset_at FROM ai_budget_state WHERE id = 1"
            ).fetchone()
            spend = self._allowed_spend(connection, month, state[1])
            remaining = max(Decimal("0"), MONTHLY_BUDGET - spend)
            reason = None
            if bool(state[0]):
                reason = "manual pause is active"
            elif spend >= MONTHLY_BUDGET:
                reason = "monthly hard limit has been reached"
            elif cost > remaining:
                reason = "estimated request cost exceeds the remaining budget"
            allowed = reason is None
            connection.execute(
                """
                INSERT INTO ai_budget_usage (
                    month, agent, estimated_cost, request_count, timestamp, model, status
                ) VALUES (?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    month,
                    normalized_agent,
                    format(cost, "f"),
                    now.astimezone(timezone.utc).isoformat(),
                    model or "unknown",
                    "allowed" if allowed else "blocked",
                ),
            )
            if allowed:
                spend += cost
                remaining = max(Decimal("0"), MONTHLY_BUDGET - spend)
            connection.commit()

        return BudgetDecision(
            allowed=allowed,
            estimated_cost=cost,
            spend_this_month=spend,
            remaining_budget=remaining,
            warning=warning_for_spend(spend),
            reason=reason,
        )

    def status(self) -> BudgetStatus:
        now = self._now()
        local_now = now.astimezone(self.timezone)
        month = local_now.strftime("%Y-%m")
        today = local_now.date()
        with self._connect() as connection:
            state = connection.execute(
                "SELECT paused, reset_at FROM ai_budget_state WHERE id = 1"
            ).fetchone()
            rows = self._active_rows(connection, month, state[1])

        spend_today = Decimal("0")
        spend_month = Decimal("0")
        usage = {agent: AgentUsage() for agent in AGENTS}
        for row in rows:
            agent, cost_text, request_count, timestamp_text, row_status = row
            cost = Decimal(str(cost_text))
            prior = usage[agent]
            if row_status == "allowed":
                spend_month += cost
                if datetime.fromisoformat(timestamp_text).astimezone(self.timezone).date() == today:
                    spend_today += cost
                usage[agent] = AgentUsage(
                    estimated_cost=prior.estimated_cost + cost,
                    request_count=prior.request_count + int(request_count),
                    blocked_count=prior.blocked_count,
                )
            else:
                usage[agent] = AgentUsage(
                    estimated_cost=prior.estimated_cost,
                    request_count=prior.request_count,
                    blocked_count=prior.blocked_count + int(request_count),
                )
        remaining = max(Decimal("0"), MONTHLY_BUDGET - spend_month)
        paused = bool(state[0])
        return BudgetStatus(
            month=month,
            spend_today=spend_today,
            spend_this_month=spend_month,
            monthly_budget=MONTHLY_BUDGET,
            remaining_budget=remaining,
            percentage_used=(spend_month / MONTHLY_BUDGET * 100).quantize(
                Decimal("0.01")
            ),
            kill_switch_active=paused or spend_month >= MONTHLY_BUDGET,
            manually_paused=paused,
            usage_by_agent=usage,
        )

    def pause(self) -> None:
        self._update_state(paused=True, reset=False)

    def resume(self) -> None:
        self._update_state(paused=False, reset=False)

    def reset(self) -> None:
        """Start a new local budget window while retaining the audit rows."""
        self._update_state(paused=False, reset=True)

    def _update_state(self, *, paused: bool, reset: bool) -> None:
        now = self._now().astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ai_budget_state
                SET paused = ?, reset_at = CASE WHEN ? THEN ? ELSE reset_at END,
                    updated_at = ?
                WHERE id = 1
                """,
                (int(paused), int(reset), now, now),
            )

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_budget_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                agent TEXT NOT NULL,
                estimated_cost NUMERIC NOT NULL,
                request_count INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('allowed', 'blocked'))
            );
            CREATE INDEX IF NOT EXISTS idx_ai_budget_usage_month_timestamp
                ON ai_budget_usage (month, timestamp);
            CREATE TABLE IF NOT EXISTS ai_budget_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                paused INTEGER NOT NULL DEFAULT 0,
                reset_at TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO ai_budget_state (id, paused, reset_at, updated_at)
            VALUES (1, 0, NULL, ?)
            """,
            (self._now().astimezone(timezone.utc).isoformat(),),
        )
        connection.commit()
        os.chmod(self.database_path, 0o600)
        return connection

    @staticmethod
    def _allowed_spend(
        connection: sqlite3.Connection, month: str, reset_at: str | None
    ) -> Decimal:
        return sum(
            (Decimal(str(row[0])) for row in AIBudgetGuard._active_rows(
                connection, month, reset_at, status="allowed"
            )),
            Decimal("0"),
        )

    @staticmethod
    def _active_rows(
        connection: sqlite3.Connection,
        month: str,
        reset_at: str | None,
        *,
        status: str | None = None,
    ) -> list[sqlite3.Row | tuple]:
        fields = "estimated_cost" if status else (
            "agent, estimated_cost, request_count, timestamp, status"
        )
        clauses = ["month = ?"]
        parameters: list[str] = [month]
        if reset_at:
            clauses.append("timestamp > ?")
            parameters.append(reset_at)
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        query = f"SELECT {fields} FROM ai_budget_usage WHERE {' AND '.join(clauses)}"
        return connection.execute(query, parameters).fetchall()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("budget clock must return a timezone-aware datetime")
        return value


def warning_for_spend(spend: Decimal | str | float) -> str | None:
    amount = Decimal(str(spend))
    for threshold, label in WARNING_LEVELS:
        if amount >= threshold:
            return label
    return None


def estimate_transcription_cost(
    duration: str,
    model: str,
    rate_per_minute: str | None = None,
) -> Decimal:
    """Estimate transcription cost from HH:MM:SS duration, rounded up to $0.000001."""
    configured_rate = rate_per_minute or os.getenv(
        "AI_BUDGET_TRANSCRIPTION_RATE_USD_PER_MINUTE", ""
    ).strip()
    rate = (
        Decimal(configured_rate)
        if configured_rate
        else TRANSCRIPTION_RATES.get(model, UNKNOWN_TRANSCRIPTION_RATE)
    )
    try:
        hours, minutes, seconds = (int(part) for part in duration.split(":"))
        total_seconds = hours * 3600 + minutes * 60 + seconds
        if total_seconds <= 0:
            raise ValueError
        estimate = Decimal(total_seconds) / Decimal(60) * rate
    except (AttributeError, ValueError):
        estimate = Decimal(os.getenv("AI_BUDGET_UNKNOWN_REQUEST_ESTIMATE_USD", "0.01"))
    return estimate.quantize(Decimal("0.000001"), rounding=ROUND_UP)


def _money(value: Decimal | str | float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_UP)
