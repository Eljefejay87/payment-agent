"""Command-line entry point for the local read-only Chief of Staff."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agents.dashboard.config import load_dashboard_settings
from agents.voicemail_tracker_agent.config import load_settings as load_voicemail_settings
from agents.voicemail_tracker_agent.runtime_store import VoicemailRuntimeStore
from agents.voicemail_tracker_agent.status_store import VoicemailStatusStore

from .adapters import CashFlowHQStatusAdapter, VoicemailTrackerStatusAdapter
from .callbacks import CallbackResolutionError, CallbackResolutionService
from .data_sources import ReadOnlySQLiteStatusSource, ReadOnlyVoicemailStatusSource
from .models import AgentStatus
from .registry import AgentRegistration, build_agent_registry


def format_status(
    registrations: Sequence[AgentRegistration],
    statuses: Sequence[AgentStatus] = (),
) -> str:
    """Format persisted agent statuses followed by the existing inventory."""

    local_count = sum(item.availability.startswith("local") for item in registrations)
    external_count = len(registrations) - local_count
    lines = [
        "UCM Chief of Staff - local read-only scaffold",
        "Mode: persisted status only; no scans, jobs, network calls, or production writes",
        "",
    ]
    if statuses:
        lines.append("Agent status:")
        for status in statuses:
            lines.append(f"- {status.agent_name}: {status.overall_status.value}")
            attempted_run = (
                status.last_attempted_run.isoformat()
                if status.last_attempted_run is not None
                else "Unavailable"
            )
            lines.append(f"  Last attempted run: {attempted_run}")
            last_run = (
                status.last_successful_run.isoformat()
                if status.last_successful_run is not None
                else "Unavailable"
            )
            lines.append(f"  Last successful run: {last_run}")
            lines.append(f"  Last run outcome: {status.last_run_outcome}")
            for name, value in status.summary_metrics.items():
                label = name.replace("_", " ").title()
                lines.append(f"  {label}: {value if value is not None else 'Unavailable'}")
            if status.error_message:
                lines.append(f"  Current error: {status.error_message}")
        lines.append("")
    lines.append(f"Inventory: {local_count} local components, {external_count} external components")
    lines.append("")
    for item in registrations:
        lines.append(f"- {item.name} [{item.availability}]")
        lines.append(f"  Location: {item.location}")
        if item.read_only_commands:
            lines.append(f"  Audited read-only commands: {', '.join(item.read_only_commands)}")
        else:
            lines.append("  Audited read-only commands: none registered")
        if item.notes:
            lines.append(f"  Note: {item.notes}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="UCM Chief of Staff local read-only tools")
    parser.add_argument(
        "command",
        choices=["status", "callbacks", "complete-callback"],
        help="Chief of Staff action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    parser.add_argument("--voicemail-id", help="Existing voicemail ID to resolve.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required before changing callback state.",
    )
    args = parser.parse_args()

    voicemail_settings = load_voicemail_settings(args.env_file)
    if args.command == "status":
        settings = load_dashboard_settings(args.env_file)
        cash_flow_source = ReadOnlySQLiteStatusSource(settings.shared_database_path)
        voicemail_source = ReadOnlyVoicemailStatusSource(
            voicemail_settings.status_path,
            voicemail_settings.runtime_state_path,
        )
        statuses = (
            CashFlowHQStatusAdapter(cash_flow_source).get_status(),
            VoicemailTrackerStatusAdapter(voicemail_source).get_status(),
        )
        print(format_status(build_agent_registry(), statuses))
        return 0
    service = CallbackResolutionService(
        VoicemailRuntimeStore(voicemail_settings.runtime_state_path),
        VoicemailStatusStore(voicemail_settings.status_path),
    )
    if args.command == "callbacks":
        callbacks = service.list_pending()
        if not callbacks:
            print("No pending callback records.")
            return 0
        print("Pending callback records:")
        for callback in callbacks:
            print(f"- ID: {callback.voicemail_id}")
            print(f"  Created: {callback.created_at}")
        return 0
    if not args.confirm:
        parser.error("complete-callback requires --confirm.")
    try:
        callback, changed = service.complete(args.voicemail_id or "")
    except CallbackResolutionError as exc:
        parser.error(str(exc))
    if changed:
        print(f"Callback marked complete: {callback.voicemail_id}")
    else:
        print(f"Callback was already complete: {callback.voicemail_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
