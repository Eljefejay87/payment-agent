"""Static registry for the first read-only Chief of Staff phase."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRegistration:
    """Describe an existing UCM component without running it."""

    name: str
    location: str
    availability: str
    read_only_commands: tuple[str, ...] = ()
    notes: str = ""


def build_agent_registry() -> tuple[AgentRegistration, ...]:
    """Return the audited UCM agent inventory.

    This registry is intentionally static in Phase 1. Building it performs no
    configuration loading, database access, network calls, or agent actions.
    """

    return (
        AgentRegistration(
            name="Payment Agent",
            location="agents/payment_agent",
            availability="local",
            read_only_commands=("debug-mail-search", "debug-list-teams-chats"),
            notes="Production scan/report commands are not exposed to Chief of Staff.",
        ),
        AgentRegistration(
            name="Cash Flow HQ",
            location="agents/cash_flow_hq",
            availability="local",
            read_only_commands=(
                "cash-flow-preview",
                "cash-flow-list-notion-data-sources",
                "cash-flow-debug-action-required",
                "cash-flow-diagnose-action-required",
            ),
            notes="Commands that update Notion or send notifications remain unavailable.",
        ),
        AgentRegistration(
            name="Voicemail Tracker",
            location="agents/voicemail_tracker_agent",
            availability="local",
            read_only_commands=("voicemail-test-sample", "voicemail-scan-once"),
            notes="Inbox scanning is not called by the status command.",
        ),
        AgentRegistration(
            name="Weekly Remit Agent",
            location="agents/weekly_remit_agent",
            availability="local",
            read_only_commands=("debug-remit-files",),
            notes="Broker email and scheduled send commands remain unavailable.",
        ),
        AgentRegistration(
            name="ICR Remit Import",
            location="agents/icr_remit_agent",
            availability="local",
            read_only_commands=("icr-remit-import --dry-run",),
            notes="Dry-run still initializes SQLite and is not called by status.",
        ),
        AgentRegistration(
            name="Operations Intelligence",
            location="agents/operations_intelligence_agent",
            availability="local",
            read_only_commands=("ops-check-setup", "ops-debug-image", "ops-audit-images"),
            notes="Teams posting and production processing commands remain unavailable.",
        ),
        AgentRegistration(
            name="Shared Data Layer",
            location="shared/data_layer",
            availability="local",
            read_only_commands=("shared-data-status", "shared-data-sync"),
            notes="Shared-data sync is dry-run unless explicit apply confirmation is supplied.",
        ),
        AgentRegistration(
            name="UCM Admin Dashboard",
            location="agents/dashboard",
            availability="local interface",
            notes="Read views exist, but the dashboard also has separately protected owner actions.",
        ),
        AgentRegistration(
            name="Attendance Tracker",
            location="external / not found in this repository",
            availability="external",
            notes="Requires a future adapter after its real interface is identified.",
        ),
        AgentRegistration(
            name="Manager Monitoring",
            location="external / not found in this repository",
            availability="external",
            notes="Requires a future adapter after its real interface is identified.",
        ),
    )
