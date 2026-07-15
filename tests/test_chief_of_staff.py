from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agents.chief_of_staff.adapters import (
    CashFlowHQStatusAdapter,
    VoicemailTrackerStatusAdapter,
)
from agents.chief_of_staff.callbacks import CallbackResolutionError, CallbackResolutionService
from agents.chief_of_staff.data_sources import (
    ReadOnlySQLiteStatusSource,
    ReadOnlyVoicemailStatusSource,
)
from agents.chief_of_staff.main import format_status
from agents.chief_of_staff.models import AgentStatus, OverallStatus, StoredStatusSnapshot
from agents.chief_of_staff.registry import build_agent_registry
from agents.voicemail_tracker_agent.models import (
    VoicemailRunOutcome,
    VoicemailStatusSnapshot,
)
from agents.voicemail_tracker_agent.runtime_store import (
    CallbackState,
    VoicemailRuntimeState,
    VoicemailRuntimeStore,
)
from agents.voicemail_tracker_agent.status_store import VoicemailStatusStore


class FakeStatusSource:
    def __init__(
        self,
        cash_flow: StoredStatusSnapshot | None = None,
        voicemail: StoredStatusSnapshot | None = None,
    ) -> None:
        self.cash_flow = cash_flow or StoredStatusSnapshot()
        self.voicemail = voicemail or StoredStatusSnapshot()

    def cash_flow_snapshot(self) -> StoredStatusSnapshot:
        return self.cash_flow

    def voicemail_snapshot(self) -> StoredStatusSnapshot:
        return self.voicemail


class ChiefOfStaffRegistryTests(unittest.TestCase):
    def test_inventory_includes_local_and_external_components(self) -> None:
        registrations = build_agent_registry()
        by_name = {item.name: item for item in registrations}

        self.assertEqual(by_name["Cash Flow HQ"].availability, "local")
        self.assertEqual(by_name["Attendance Tracker"].availability, "external")
        self.assertEqual(by_name["Manager Monitoring"].availability, "external")

    def test_status_is_explicitly_read_only(self) -> None:
        status = format_status(build_agent_registry())

        self.assertIn("persisted status only", status)
        self.assertIn("no scans, jobs, network calls, or production writes", status)
        self.assertIn("cash-flow-preview", status)
        self.assertNotIn("cash-flow-mark-paid", status)

    def test_status_output_aggregates_typed_agent_status(self) -> None:
        status = format_status(
            build_agent_registry(),
            (
                AgentStatus(
                    agent_name="Cash Flow HQ",
                    overall_status=OverallStatus.WARNING,
                    last_attempted_run=datetime(2026, 7, 13, 11, 59, tzinfo=timezone.utc),
                    last_successful_run=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                    last_run_outcome="Success",
                    summary_metrics={"needs_review": 2},
                ),
            ),
        )

        self.assertIn("Cash Flow HQ: Warning", status)
        self.assertIn("Last attempted run: 2026-07-13T11:59:00+00:00", status)
        self.assertIn("Last successful run: 2026-07-13T12:00:00+00:00", status)
        self.assertIn("Last run outcome: Success", status)
        self.assertIn("Needs Review: 2", status)

    def test_cash_flow_adapter_reports_warning_for_needs_review(self) -> None:
        source = FakeStatusSource(
            cash_flow=StoredStatusSnapshot(
                last_successful_run=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                summary_metrics={"total_bills": 9, "needs_review": 2},
            )
        )

        status = CashFlowHQStatusAdapter(source).get_status()

        self.assertEqual(status.overall_status, OverallStatus.WARNING)
        self.assertEqual(status.summary_metrics["needs_review"], 2)
        self.assertIsNone(status.error_message)

    def test_voicemail_adapter_does_not_invent_unavailable_callback_data(self) -> None:
        source = FakeStatusSource(
            voicemail=StoredStatusSnapshot(
                summary_metrics={"tracked_voicemails": 0, "pending_callbacks": None}
            )
        )

        status = VoicemailTrackerStatusAdapter(source).get_status()

        self.assertEqual(status.overall_status, OverallStatus.WARNING)
        self.assertIsNone(status.last_successful_run)
        self.assertIsNone(status.summary_metrics["pending_callbacks"])

    def test_adapter_contains_source_errors(self) -> None:
        class BrokenSource(FakeStatusSource):
            def cash_flow_snapshot(self) -> StoredStatusSnapshot:
                raise sqlite3.OperationalError("status database unavailable")

        status = CashFlowHQStatusAdapter(BrokenSource()).get_status()

        self.assertEqual(status.overall_status, OverallStatus.ERROR)
        self.assertEqual(status.error_message, "status database unavailable")

    def test_sqlite_source_reads_without_changing_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared.sqlite3"
            self._create_status_database(path)
            before = path.read_bytes()

            snapshot = ReadOnlySQLiteStatusSource(path).cash_flow_snapshot()

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(snapshot.summary_metrics["total_bills"], 2)
            self.assertEqual(snapshot.summary_metrics["needs_review"], 1)
            self.assertIsNotNone(snapshot.last_successful_run)

    def test_callback_completion_updates_existing_runtime_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime_path = Path(directory) / "runtime.json"
            status_path = Path(directory) / "status.json"
            voicemail_id = "message-1"
            VoicemailRuntimeStore(runtime_path).write(
                VoicemailRuntimeState(
                    callbacks={voicemail_id: CallbackState(voicemail_id=voicemail_id)}
                )
            )
            now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            VoicemailStatusStore(status_path).write(
                VoicemailStatusSnapshot(
                    last_attempted_run=now,
                    last_successful_run=now,
                    last_run_outcome=VoicemailRunOutcome.SUCCESS,
                    pending_callback_count=1,
                    total_records_processed=1,
                )
            )

            callback, changed = CallbackResolutionService(
                VoicemailRuntimeStore(runtime_path),
                VoicemailStatusStore(status_path),
            ).complete(voicemail_id)

            self.assertTrue(changed)
            self.assertEqual(callback.status, "completed")
            self.assertIsNotNone(callback.completed_at)
            self.assertEqual(
                len(VoicemailRuntimeStore(runtime_path).read().pending_callbacks()), 0
            )
            self.assertEqual(
                VoicemailStatusStore(status_path).read().pending_callback_count,
                0,
            )
            self.assertEqual(
                ReadOnlyVoicemailStatusSource(status_path, runtime_path)
                .voicemail_snapshot()
                .summary_metrics["pending_callbacks"],
                0,
            )

    def test_callback_completion_rejects_unknown_record_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime_path = Path(directory) / "runtime.json"
            state = VoicemailRuntimeState(
                callbacks={"message-1": CallbackState(voicemail_id="message-1")}
            )
            store = VoicemailRuntimeStore(runtime_path)
            store.write(state)
            before = runtime_path.read_bytes()
            service = CallbackResolutionService(store, VoicemailStatusStore(Path(directory) / "status.json"))

            with self.assertRaisesRegex(CallbackResolutionError, "not found"):
                service.complete("missing-message")

            self.assertEqual(runtime_path.read_bytes(), before)

    @staticmethod
    def _create_status_database(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.executescript(
                """
                CREATE TABLE shared_records (
                    record_type TEXT, status TEXT, review_status TEXT,
                    action_required TEXT, confidence REAL
                );
                CREATE TABLE shared_agent_runs (
                    agent_name TEXT, started_at TEXT, completed_at TEXT,
                    status TEXT, error_message TEXT, external_services_json TEXT
                );
                INSERT INTO shared_records VALUES
                    ('bill', 'upcoming', 'not_required', NULL, 0.99),
                    ('bill', 'needs_review', 'pending', 'Check amount', 0.50);
                INSERT INTO shared_agent_runs VALUES
                    ('shared_data_sync', '2026-07-13T11:59:00+00:00',
                     '2026-07-13T12:00:00+00:00', 'completed', NULL,
                     '["sqlite", "notion"]');
                """
            )


if __name__ == "__main__":
    unittest.main()
