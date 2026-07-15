"""Side-effect-free status adapters for existing UCM agents."""

from __future__ import annotations

from .data_sources import CashFlowStatusSource, VoicemailStatusSource
from .models import AgentStatus, OverallStatus, StoredStatusSnapshot


class CashFlowHQStatusAdapter:
    def __init__(self, source: CashFlowStatusSource) -> None:
        self.source = source

    def get_status(self) -> AgentStatus:
        try:
            snapshot = self.source.cash_flow_snapshot()
        except Exception as exc:
            return _error_status("Cash Flow HQ", exc)
        if snapshot.current_error:
            overall = OverallStatus.ERROR
        elif snapshot.last_successful_run is None or snapshot.summary_metrics.get("needs_review", 0):
            overall = OverallStatus.WARNING
        else:
            overall = OverallStatus.HEALTHY
        return _status("Cash Flow HQ", overall, snapshot)


class VoicemailTrackerStatusAdapter:
    def __init__(self, source: VoicemailStatusSource) -> None:
        self.source = source

    def get_status(self) -> AgentStatus:
        try:
            snapshot = self.source.voicemail_snapshot()
        except Exception as exc:
            return _error_status("Voicemail Tracker", exc)
        if snapshot.current_error:
            overall = OverallStatus.ERROR
        elif snapshot.last_run_outcome == "Not Yet Run":
            overall = OverallStatus.WARNING
        else:
            overall = OverallStatus.HEALTHY
        return _status("Voicemail Tracker", overall, snapshot)


def _status(
    agent_name: str,
    overall_status: OverallStatus,
    snapshot: StoredStatusSnapshot,
) -> AgentStatus:
    return AgentStatus(
        agent_name=agent_name,
        overall_status=overall_status,
        last_attempted_run=snapshot.last_attempted_run,
        last_successful_run=snapshot.last_successful_run,
        last_run_outcome=snapshot.last_run_outcome,
        summary_metrics=dict(snapshot.summary_metrics),
        error_message=snapshot.current_error,
    )


def _error_status(agent_name: str, error: Exception) -> AgentStatus:
    return AgentStatus(
        agent_name=agent_name,
        overall_status=OverallStatus.ERROR,
        last_attempted_run=None,
        last_successful_run=None,
        last_run_outcome="Error",
        summary_metrics={},
        error_message=str(error),
    )
