"""Typed status contracts for read-only Chief of Staff adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OverallStatus(str, Enum):
    HEALTHY = "Healthy"
    WARNING = "Warning"
    ERROR = "Error"


@dataclass(frozen=True)
class StoredStatusSnapshot:
    """Existing persisted state used by an adapter; never produced by a job run."""

    last_attempted_run: datetime | None = None
    last_successful_run: datetime | None = None
    last_run_outcome: str = "Not Yet Run"
    summary_metrics: dict[str, int | None] = field(default_factory=dict)
    current_error: str | None = None


@dataclass(frozen=True)
class AgentStatus:
    """The only status fields exposed by a Chief of Staff agent adapter."""

    agent_name: str
    overall_status: OverallStatus
    last_attempted_run: datetime | None = None
    last_successful_run: datetime | None = None
    last_run_outcome: str = "Not Yet Run"
    summary_metrics: dict[str, int | None] = field(default_factory=dict)
    error_message: str | None = None
