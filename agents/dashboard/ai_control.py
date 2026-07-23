"""Local, guarded controls and read-only status for the AI Control Center."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from shared.ai_budget import AIBudgetGuard, BudgetStatus


DEFAULT_AUDIT_PATH = (
    Path.home() / "Library/Application Support/UCM/payment-agent/ai_control_audit.sqlite3"
)
APPROVED_LAUNCH_AGENTS = (
    "com.ucm.payment-agent",
    "com.ucm.cash-flow-hq",
    "com.ucm.operations-intelligence-agent",
    "com.ucm.shared-data-sync",
)


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    name: str
    launch_agent: str | None
    schedule: str
    command: tuple[str, ...]


SERVICE_SPECS = (
    ServiceSpec("payment", "Payment Agent", "com.ucm.payment-agent", "Every 15 minutes", ("scan-once",)),
    ServiceSpec("operations", "Operations Intelligence", "com.ucm.operations-intelligence-agent", "Weekdays 5:00 PM, 5:45 PM, 6:00 PM", ("ops-scan-once",)),
    ServiceSpec("cash_flow", "Cash Flow HQ", "com.ucm.cash-flow-hq", "Weekdays 10:00 AM and 5:00 PM", ("cashflow-run", "--days", "1", "--limit", "50")),
    ServiceSpec("shared_sync", "Shared Data Sync", "com.ucm.shared-data-sync", "Hourly during business hours", ("shared-data-sync-once", "--limit", "100")),
    ServiceSpec("voicemail", "Voicemail Tracker", None, "Every 15 minutes; due retries only", ("voicemail-scan-once",)),
)
SERVICE_BY_KEY = {spec.key: spec for spec in SERVICE_SPECS}


@dataclass(frozen=True)
class ControlResult:
    ok: bool
    message: str
    duplicate: bool = False


class AIControlCenter:
    """Local-only control plane with confirmation and durable duplicate protection."""

    def __init__(
        self,
        budget_guard: AIBudgetGuard,
        *,
        audit_path: Path | str | None = DEFAULT_AUDIT_PATH,
        project_root: Path | str | None = None,
        launch_agents_directory: Path | str | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        now_provider: Callable[[], datetime] | None = None,
        user_id: int | None = None,
        health_paths: dict[str, Path | str] | None = None,
        voicemail_runtime_state_path: Path | str | None = None,
    ) -> None:
        self.budget_guard = budget_guard
        self.audit_path = Path(audit_path or DEFAULT_AUDIT_PATH).expanduser()
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self.launch_agents_directory = Path(
            launch_agents_directory or Path.home() / "Library/LaunchAgents"
        )
        self.command_runner = command_runner
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.user_id = user_id if user_id is not None else os.getuid()
        self.health_paths = {
            key: Path(value).expanduser() for key, value in (health_paths or {}).items()
        }
        self.voicemail_runtime_state_path = Path(
            voicemail_runtime_state_path
            or Path.home() / "Library/Application Support/UCM/payment-agent/voicemail_runtime_state.json"
        ).expanduser()
        self._initialize_audit_store()

    def snapshot(self) -> dict:
        status = self.budget_guard.status()
        return {
            "budget": _budget_payload(status),
            "warning_state": budget_warning_state(status),
            "agents": self.service_statuses(status),
            "spending_chart": self.spending_chart(),
            "activity": self.activity_feed(),
            "controls": {
                "resume_all_services_enabled": False,
                "resume_all_services_message": "Event-driven schedules must be approved before services can be resumed.",
                "one_time_confirmation": "RUN ONE-TIME JOB",
                "service_pause_confirmation": "PAUSE ALL SERVICES",
            },
        }

    def pause_ai_usage(self, *, request_id: str, initiator: str = "local-dashboard") -> ControlResult:
        return self._once(request_id, "pause_ai_usage", "AI Budget Guard", initiator, self._pause)

    def resume_ai_usage(self, *, request_id: str, initiator: str = "local-dashboard") -> ControlResult:
        return self._once(request_id, "resume_ai_usage", "AI Budget Guard", initiator, self._resume)

    def pause_all_services(
        self, *, request_id: str, confirmation: str, initiator: str = "local-dashboard"
    ) -> ControlResult:
        if confirmation != "PAUSE ALL SERVICES":
            return ControlResult(False, "Explicit confirmation is required before pausing services.")

        def action() -> ControlResult:
            results = []
            for label in APPROVED_LAUNCH_AGENTS:
                completed = self.command_runner(
                    ["launchctl", "bootout", f"gui/{self.user_id}/{label}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                if completed.returncode in {0, 3, 113}:
                    results.append(label)
                else:
                    return ControlResult(False, "One or more approved UCM services could not be paused.")
            return ControlResult(True, f"Paused {len(results)} approved UCM service(s).")

        return self._once(request_id, "pause_all_services", "approved-launchagents", initiator, action)

    def resume_all_services(self, *, request_id: str, initiator: str = "local-dashboard") -> ControlResult:
        return self._once(
            request_id,
            "resume_all_services",
            "approved-launchagents",
            initiator,
            lambda: ControlResult(False, "Event-driven schedules must be approved before services can be resumed."),
        )

    def run_one_time_job(
        self,
        job_key: str,
        *,
        request_id: str,
        confirmation: str,
        initiator: str = "local-dashboard",
    ) -> ControlResult:
        if confirmation != "RUN ONE-TIME JOB":
            return ControlResult(False, "Explicit confirmation is required before running a one-time job.")
        spec = SERVICE_BY_KEY.get(job_key)
        if spec is None:
            return ControlResult(False, "Unsupported one-time job.")

        def action() -> ControlResult:
            completed = self.command_runner(
                [sys.executable, "main.py", *spec.command],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            if completed.returncode == 0:
                return ControlResult(True, f"{spec.name} one-time job completed.")
            return ControlResult(False, f"{spec.name} one-time job failed with exit code {completed.returncode}.")

        return self._once(request_id, "run_one_time_job", spec.name, initiator, action)

    def service_statuses(self, budget_status: BudgetStatus | None = None) -> list[dict]:
        budget_status = budget_status or self.budget_guard.status()
        disabled = self._disabled_labels()
        usage = budget_status.usage_by_agent
        agents = []
        for spec in SERVICE_SPECS:
            health = self._health_summary(spec.key)
            agents.append(
                {
                "key": spec.key,
                "name": spec.name,
                "state": self._service_state(spec, disabled),
                "last_run": health["last_run"],
                "last_successful_run": health["last_successful_run"],
                "last_failure": health["last_failure"],
                "last_run_duration": "Unknown",
                "last_exit_code": self._last_exit_code(spec),
                "openai_spend_today": "$0.00",
                "openai_spend_month": _money_label(usage[spec.name].estimated_cost),
                "next_scheduled_run": spec.schedule,
            }
            )
        return agents

    def spending_chart(self, days: int = 30) -> list[dict]:
        days = max(1, min(30, days))
        today = self.now_provider().astimezone(self.budget_guard.timezone).date()
        totals = {today - timedelta(days=index): Decimal("0") for index in range(days)}
        for timestamp, _agent, cost, status in self._budget_rows(days):
            if status != "allowed":
                continue
            try:
                local_date = datetime.fromisoformat(timestamp).astimezone(self.budget_guard.timezone).date()
            except ValueError:
                continue
            if local_date in totals:
                totals[local_date] += Decimal(str(cost))
        return [
            {"date": day.isoformat(), "spend": _money_label(totals[day]), "amount": float(totals[day])}
            for day in sorted(totals)
        ]

    def activity_feed(self, limit: int = 30) -> list[dict]:
        events: list[dict] = []
        for timestamp, agent, cost, status in self._budget_rows(30):
            events.append(
                {
                    "timestamp": timestamp,
                    "type": "budget",
                    "status": status,
                    "message": f"{agent}: OpenAI request {status} ({_money_label(Decimal(str(cost)))})",
                }
            )
        with self._connect_audit() as connection:
            rows = connection.execute(
                """
                SELECT completed_at, action, target, status, message
                FROM control_actions ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        for timestamp, action, target, status, message in rows:
            events.append(
                {
                    "timestamp": timestamp or "",
                    "type": "control",
                    "status": status,
                    "message": _safe_message(action, target, message),
                }
            )
        events.extend(self._agent_run_events())
        events.extend(self._transcription_retry_events())
        return sorted(events, key=lambda event: event["timestamp"], reverse=True)[:limit]

    def _pause(self) -> ControlResult:
        self.budget_guard.pause()
        return ControlResult(True, "AI usage paused. OpenAI-powered work is blocked.")

    def _resume(self) -> ControlResult:
        self.budget_guard.resume()
        return ControlResult(True, "AI usage resumed; the monthly hard limit remains enforced.")

    def _once(
        self,
        request_id: str,
        action: str,
        target: str,
        initiator: str,
        operation: Callable[[], ControlResult],
    ) -> ControlResult:
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", request_id):
            return ControlResult(False, "A valid control request ID is required.")
        now = self.now_provider().astimezone(timezone.utc).isoformat()
        with self._connect_audit() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status, message FROM control_actions WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing:
                connection.commit()
                return ControlResult(existing[0] == "succeeded", existing[1], duplicate=True)
            connection.execute(
                """
                INSERT INTO control_actions (
                    request_id, action, target, initiated_by, created_at, completed_at, status, message
                ) VALUES (?, ?, ?, ?, ?, NULL, 'started', 'Control action started.')
                """,
                (request_id, action, target, _safe_initiator(initiator), now),
            )
            connection.commit()
        try:
            result = operation()
        except Exception:
            result = ControlResult(False, "Control action failed without starting a service.")
        completed = self.now_provider().astimezone(timezone.utc).isoformat()
        with self._connect_audit() as connection:
            connection.execute(
                """
                UPDATE control_actions SET completed_at = ?, status = ?, message = ?
                WHERE request_id = ?
                """,
                (completed, "succeeded" if result.ok else "failed", _safe_message(action, target, result.message), request_id),
            )
        return result

    def _disabled_labels(self) -> set[str]:
        try:
            completed = self.command_runner(
                ["launchctl", "print-disabled", f"user/{self.user_id}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        return set(re.findall(r'"(com\.ucm\.[^"]+)"\s*=>\s*disabled', completed.stdout))

    def _service_state(self, spec: ServiceSpec, disabled: set[str]) -> str:
        if spec.launch_agent is None:
            return "Stopped"
        if spec.launch_agent in disabled:
            return "Stopped"
        try:
            completed = self.command_runner(
                ["launchctl", "print", f"gui/{self.user_id}/{spec.launch_agent}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "Unknown"
        if completed.returncode == 0:
            return "Running"
        if (self.launch_agents_directory / f"{spec.launch_agent}.plist").is_file():
            return "Scheduled"
        return "Unknown"

    def _last_exit_code(self, spec: ServiceSpec) -> str:
        if spec.launch_agent is None:
            return "Unknown"
        try:
            completed = self.command_runner(
                ["launchctl", "print", f"gui/{self.user_id}/{spec.launch_agent}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "Unknown"
        match = re.search(r"last exit code\s*=\s*(\d+)", completed.stdout, re.I)
        return match.group(1) if match else "Unknown"

    def _health_summary(self, key: str) -> dict[str, str]:
        default = {
            "last_run": "Unknown",
            "last_successful_run": "Unknown",
            "last_failure": "None recorded",
        }
        path = self.health_paths.get(key)
        if path is None or not path.is_file():
            return default
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {**default, "last_failure": "Recorded status is unavailable"}
        if not isinstance(payload, dict):
            return {**default, "last_failure": "Recorded status is unavailable"}
        if key == "payment":
            last_run = _safe_timestamp(payload.get("updated_at"))
            last_success = _safe_timestamp(payload.get("last_successful_run"))
            failure = "Recorded failure" if payload.get("last_error") else "None recorded"
        elif key == "voicemail":
            last_run = _safe_timestamp(payload.get("last_attempted_run"))
            last_success = _safe_timestamp(payload.get("last_successful_run"))
            failure = (
                "Recorded failure"
                if str(payload.get("last_run_outcome", "")).lower() == "error"
                else "None recorded"
            )
        else:
            return default
        return {
            "last_run": last_run,
            "last_successful_run": last_success,
            "last_failure": failure,
        }

    def _agent_run_events(self) -> list[dict]:
        events: list[dict] = []
        for spec in SERVICE_SPECS:
            health = self._health_summary(spec.key)
            timestamp = health["last_run"]
            if timestamp == "Unknown":
                continue
            failed = health["last_failure"] != "None recorded"
            events.append(
                {
                    "timestamp": timestamp,
                    "type": "agent-run",
                    "status": "failed" if failed else "succeeded",
                    "message": f"{spec.name}: latest recorded run {'failed' if failed else 'succeeded'}.",
                }
            )
        return events

    def _transcription_retry_events(self) -> list[dict]:
        if not self.voicemail_runtime_state_path.is_file():
            return []
        try:
            payload = json.loads(self.voicemail_runtime_state_path.read_text(encoding="utf-8"))
            jobs = payload.get("transcription_jobs", {}) if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return []
        if not isinstance(jobs, dict):
            return []
        events = []
        for job in jobs.values():
            if not isinstance(job, dict) or job.get("status") != "Pending":
                continue
            retry_at = _safe_timestamp(job.get("next_retry_at"))
            if retry_at == "Unknown":
                continue
            events.append(
                {
                    "timestamp": retry_at,
                    "type": "transcription-retry",
                    "status": "pending",
                    "message": "Voicemail transcription retry is pending.",
                }
            )
        return events

    def _budget_rows(self, days: int) -> list[tuple[str, str, str, str]]:
        if not self.budget_guard.database_path.exists():
            return []
        try:
            with sqlite3.connect(f"file:{self.budget_guard.database_path}?mode=ro", uri=True) as connection:
                return connection.execute(
                    """
                    SELECT timestamp, agent, estimated_cost, status
                    FROM ai_budget_usage ORDER BY timestamp DESC
                    """
                ).fetchall()
        except sqlite3.Error:
            return []

    def _initialize_audit_store(self) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect_audit() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS control_actions (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    initiated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_control_actions_created ON control_actions(created_at)"
            )
        os.chmod(self.audit_path, 0o600)

    def _connect_audit(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.audit_path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection


def budget_warning_state(status: BudgetStatus) -> str:
    if status.manually_paused or status.spend_this_month >= Decimal("20"):
        return "blocked"
    if status.spend_this_month >= Decimal("18"):
        return "red"
    if status.spend_this_month >= Decimal("15"):
        return "orange"
    if status.spend_this_month >= Decimal("10"):
        return "yellow"
    return "green"


def _budget_payload(status: BudgetStatus) -> dict:
    return {
        "spend_today": _money_label(status.spend_today),
        "spend_this_month": _money_label(status.spend_this_month),
        "monthly_budget": _money_label(status.monthly_budget),
        "remaining_budget": _money_label(status.remaining_budget),
        "percentage_used": f"{status.percentage_used:.2f}%",
        "kill_switch": "Active" if status.kill_switch_active else "Inactive",
        "manual_pause": "Active" if status.manually_paused else "Inactive",
    }


def _money_label(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def _safe_initiator(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]", "", value or "local-dashboard").strip()
    return (cleaned or "local-dashboard")[:80]


def _safe_message(action: str, target: str, message: str) -> str:
    del target
    cleaned = re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|bearer\s+\S+|token\s*=\s*\S+)", "[redacted]", message)
    return f"{action}: {cleaned}"[:240]


def _safe_timestamp(value: object) -> str:
    if not isinstance(value, str):
        return "Unknown"
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return "Unknown"
