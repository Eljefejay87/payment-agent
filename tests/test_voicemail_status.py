from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from stat import S_IMODE
import tempfile
import unittest
from unittest.mock import patch

from agents.chief_of_staff.adapters import VoicemailTrackerStatusAdapter
from agents.chief_of_staff.data_sources import ReadOnlyVoicemailStatusSource
from agents.chief_of_staff.models import OverallStatus
from agents.voicemail_tracker_agent.config import Settings
from agents.voicemail_tracker_agent.models import VoicemailStatusSnapshot
from agents.voicemail_tracker_agent.runtime_store import VoicemailRuntimeStore
from agents.voicemail_tracker_agent.sample_data import SAMPLE_VOICEMAIL_MESSAGES
from agents.voicemail_tracker_agent.service import VoicemailTrackerAgent
from agents.voicemail_tracker_agent.status_store import (
    VoicemailStatusStateError,
    VoicemailStatusStore,
)


class FakeGraph:
    def __init__(self, messages: list[dict] | None = None, error: Exception | None = None) -> None:
        self.messages = messages or []
        self.error = error
        self.calls = 0

    def find_voicemail_messages(self) -> list[dict]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.messages


class BrokenWriteStatusStore(VoicemailStatusStore):
    def write(self, snapshot: VoicemailStatusSnapshot) -> None:
        raise OSError("status write failed")


class VoicemailStatusPersistenceTests(unittest.TestCase):
    def test_first_run_with_no_state_reports_not_yet_run_without_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            source = ReadOnlyVoicemailStatusSource(path)

            with patch.object(VoicemailTrackerAgent, "scan_once") as scan_once:
                status = VoicemailTrackerStatusAdapter(source).get_status()

            scan_once.assert_not_called()
            self.assertEqual(status.overall_status, OverallStatus.WARNING)
            self.assertEqual(status.last_run_outcome, "Not Yet Run")
            self.assertIsNone(status.last_attempted_run)
            self.assertIsNone(status.summary_metrics["pending_callbacks"])
            self.assertFalse(path.exists())

    def test_successful_scan_persists_operational_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            graph = FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)
            agent = self._agent(path, graph)

            records = agent.scan_once()
            snapshot = VoicemailStatusStore(path).read()

            self.assertEqual(len(records), 1)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.last_run_outcome.value, "Success")
            self.assertIsNotNone(snapshot.last_attempted_run)
            self.assertIsNotNone(snapshot.last_successful_run)
            self.assertEqual(snapshot.total_records_processed, 1)
            self.assertIsNone(snapshot.last_error_message)
            self.assertEqual(S_IMODE(path.stat().st_mode), 0o600)

    def test_successful_scan_persists_runtime_state_for_railway_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            runtime_path = Path(directory) / "runtime.json"
            graph = FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)
            agent = self._agent(status_path, graph, runtime_path)

            records = agent.scan_once()
            state = VoicemailRuntimeStore(runtime_path).read()

            self.assertEqual(len(records), 1)
            self.assertIn("<sample-message-1@vaspian>", state.processed_voicemail_ids)
            self.assertIsNotNone(state.last_successful_scan)
            self.assertEqual(
                [callback.voicemail_id for callback in state.pending_callbacks()],
                ["<sample-message-1@vaspian>"],
            )
            self.assertEqual(
                state.callbacks["<sample-message-1@vaspian>"].status,
                "pending",
            )
            self.assertEqual(S_IMODE(runtime_path.stat().st_mode), 0o600)

    def test_restart_skips_already_processed_voicemails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            runtime_path = Path(directory) / "runtime.json"

            first = self._agent(status_path, FakeGraph(SAMPLE_VOICEMAIL_MESSAGES), runtime_path)
            second = self._agent(status_path, FakeGraph(SAMPLE_VOICEMAIL_MESSAGES), runtime_path)

            first_records = first.scan_once()
            second_records = second.scan_once()
            snapshot = VoicemailStatusStore(status_path).read()
            state = VoicemailRuntimeStore(runtime_path).read()

            self.assertEqual(len(first_records), 1)
            self.assertEqual(second_records, [])
            self.assertEqual(snapshot.total_records_processed, 0)
            self.assertEqual(snapshot.pending_callback_count, 1)
            self.assertEqual(len(state.processed_voicemail_ids), 1)
            self.assertEqual(len(state.pending_callbacks()), 1)

    def test_same_scan_skips_duplicate_voicemail_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            runtime_path = Path(directory) / "runtime.json"
            duplicate = dict(SAMPLE_VOICEMAIL_MESSAGES[0])
            messages = [SAMPLE_VOICEMAIL_MESSAGES[0], duplicate]

            records = self._agent(status_path, FakeGraph(messages), runtime_path).scan_once()
            state = VoicemailRuntimeStore(runtime_path).read()

            self.assertEqual(len(records), 1)
            self.assertEqual(len(state.processed_voicemail_ids), 1)
            self.assertEqual(len(state.pending_callbacks()), 1)

    def test_failed_scan_persists_failure_and_preserves_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            self._agent(path, FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)).scan_once()
            successful = VoicemailStatusStore(path).read()
            failing = self._agent(path, FakeGraph(error=RuntimeError("account B123456 failed")))

            with self.assertRaisesRegex(RuntimeError, "account B123456 failed"):
                failing.scan_once()
            snapshot = VoicemailStatusStore(path).read()

            self.assertEqual(snapshot.last_run_outcome.value, "Error")
            self.assertEqual(snapshot.last_successful_run, successful.last_successful_run)
            self.assertEqual(snapshot.pending_callback_count, 1)
            self.assertEqual(snapshot.total_records_processed, 0)
            self.assertEqual(snapshot.last_error_message, "RuntimeError: Voicemail scan failed.")

    def test_pending_callback_count_matches_latest_successful_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            second = dict(SAMPLE_VOICEMAIL_MESSAGES[0])
            second["id"] = "sample-message-2"
            second["internetMessageId"] = "<sample-message-2@vaspian>"
            messages = [SAMPLE_VOICEMAIL_MESSAGES[0], second]

            self._agent(path, FakeGraph(messages)).scan_once()
            snapshot = VoicemailStatusStore(path).read()

            self.assertEqual(snapshot.pending_callback_count, 2)
            self.assertEqual(snapshot.total_records_processed, 2)

    def test_atomic_write_failure_preserves_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            store = VoicemailStatusStore(path)
            self._agent(path, FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)).scan_once()
            before = path.read_bytes()
            replacement = store.read()

            with patch(
                "agents.voicemail_tracker_agent.status_store.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    store.write(replacement)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_state_is_reported_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(VoicemailStatusStateError):
                VoicemailStatusStore(path).read()
            status = VoicemailTrackerStatusAdapter(ReadOnlyVoicemailStatusSource(path)).get_status()

            self.assertEqual(status.overall_status, OverallStatus.ERROR)
            self.assertEqual(status.last_run_outcome, "Error")
            self.assertIn("corrupt or unreadable", status.error_message)

    def test_snapshot_does_not_store_sensitive_voicemail_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"

            self._agent(path, FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)).scan_once()
            raw = path.read_text(encoding="utf-8")

            for sensitive_value in (
                "555",
                "B123456",
                "account number",
                "voicemail-5551234567.wav",
                "sample-message-1@vaspian",
            ):
                self.assertNotIn(sensitive_value, raw)

    def test_chief_of_staff_reads_snapshot_without_change_or_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            graph = FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)
            self._agent(path, graph).scan_once()
            before = path.read_bytes()

            status = VoicemailTrackerStatusAdapter(ReadOnlyVoicemailStatusSource(path)).get_status()

            self.assertEqual(graph.calls, 1)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(status.overall_status, OverallStatus.HEALTHY)
            self.assertEqual(status.last_run_outcome, "Success")
            self.assertEqual(status.summary_metrics["pending_callbacks"], 1)
            self.assertEqual(status.summary_metrics["records_processed"], 1)

    def test_status_write_failure_does_not_change_successful_scan_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            graph = FakeGraph(SAMPLE_VOICEMAIL_MESSAGES)
            settings = self._settings(path)
            agent = VoicemailTrackerAgent(
                settings,
                graph=graph,
                status_store=BrokenWriteStatusStore(path),
            )

            records = agent.scan_once()

            self.assertEqual(len(records), 1)
            self.assertFalse(path.exists())

    def test_sample_mode_does_not_create_status_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            agent = self._agent(path, FakeGraph())

            with redirect_stdout(StringIO()):
                records = agent.scan_sample(SAMPLE_VOICEMAIL_MESSAGES)

            self.assertEqual(len(records), 1)
            self.assertFalse(path.exists())

    def test_health_command_reads_status_without_validation_or_scan(self) -> None:
        from agents.voicemail_tracker_agent.main import main

        with tempfile.TemporaryDirectory() as directory:
            health_path = Path(directory) / "health.json"
            health_path.write_text(
                '{"service":"voicemail_tracker_agent","status":"running","last_successful_scan":"2026-07-14T12:00:00+00:00","last_error":null}',
                encoding="utf-8",
            )

            with patch(
                "sys.argv",
                [
                    "main.py",
                    "health",
                    "--env-file",
                    str(Path(directory) / "missing.env"),
                ],
            ), patch.dict(
                "os.environ",
                {"VOICEMAIL_HEALTH_PATH": str(health_path)},
                clear=False,
            ), patch.object(VoicemailTrackerAgent, "scan_once") as scan_once, redirect_stdout(StringIO()) as output:
                result = main()

            scan_once.assert_not_called()
            self.assertEqual(result, 0)
            self.assertIn('"status": "running"', output.getvalue())

    def test_load_settings_uses_voicemail_data_dir_for_runtime_paths(self) -> None:
        from agents.voicemail_tracker_agent.config import load_settings

        with patch.dict(
            "os.environ",
            {
                "VOICEMAIL_DATA_DIR": "/data",
                "VOICEMAIL_STATUS_PATH": "",
                "VOICEMAIL_RUNTIME_STATE_PATH": "",
                "VOICEMAIL_HEALTH_PATH": "",
            },
            clear=False,
        ):
            settings = load_settings()

        self.assertEqual(settings.status_path, Path("/data/voicemail_status.json"))
        self.assertEqual(settings.runtime_state_path, Path("/data/voicemail_runtime_state.json"))
        self.assertEqual(settings.health_path, Path("/data/voicemail_health.json"))

    @staticmethod
    def _agent(
        path: Path,
        graph: FakeGraph,
        runtime_path: Path | None = None,
    ) -> VoicemailTrackerAgent:
        settings = VoicemailStatusPersistenceTests._settings(path)
        return VoicemailTrackerAgent(
            settings,
            graph=graph,
            status_store=VoicemailStatusStore(path),
            runtime_store=VoicemailRuntimeStore(runtime_path or path.with_name("runtime.json")),
        )

    @staticmethod
    def _settings(path: Path) -> Settings:
        return Settings(
            dry_run=True,
            log_level="INFO",
            timezone="America/New_York",
            mailbox_user_id="voicemail@example.com",
            graph_tenant_id="tenant",
            graph_client_id="client",
            graph_client_secret="secret",
            sender_email="voicemail@vaspian.com",
            subject_contains="voicemail",
            lookback_hours=48,
            scan_interval_minutes=15,
            summary_time="08:50",
            run_startup_scan=True,
            status_path=path,
            runtime_state_path=path.with_name("runtime.json"),
            health_path=path.with_name("health.json"),
        )


if __name__ == "__main__":
    unittest.main()
