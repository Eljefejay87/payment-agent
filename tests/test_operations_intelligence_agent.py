from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.operations_intelligence_agent.database import OperationsDatabase
from agents.operations_intelligence_agent.classifier import OperationsScreenshotClassifier, ScreenshotClassification
from agents.operations_intelligence_agent.history import (
    build_historical_context,
    build_historical_summary,
    build_historical_trend_analysis,
)
from agents.operations_intelligence_agent.models import ExtractedReport, MetricValue, SavedScreenshot, TeamsImage
from agents.operations_intelligence_agent.ocr import OcrRegion, ScreenshotOcrExtractor
from agents.operations_intelligence_agent.reports import build_operations_message, build_summary_text
from agents.operations_intelligence_agent.service import OperationsIntelligenceAgent
from agents.operations_intelligence_agent.setup_check import format_setup_checks, run_setup_checks


class OperationsOcrTests(unittest.TestCase):
    def test_extracts_known_scollect_metrics_from_ocr_text(self) -> None:
        extractor = FakeExtractor(
            """
            Accounts Worked: 123
            Attempts: 456
            Live Contacts: 78
            Contact Rate: 17.11%
            Close Rate: 5.20%
            Posted Cash: $1,200.50
            Posted Fees: $30.00
            Pending Cash: $400.00
            Future Scheduled Cash: $900.00
            CSOLO $700.00
            KMAD $800.00
            """
        )

        report = extractor.extract(Path("sample.png"), "2026-07-02", "hash-1")

        self.assertEqual(report.metric_value("accounts_worked"), 123)
        self.assertEqual(report.metric_value("attempts"), 456)
        self.assertEqual(report.metric_value("live_contacts"), 78)
        self.assertEqual(report.metric_value("contact_rate"), 17.11)
        self.assertEqual(report.metric_value("posted_cash"), 1200.50)
        self.assertTrue(report.collector_totals)

    def test_missing_fields_are_flagged_for_manual_review(self) -> None:
        extractor = FakeExtractor("Attempts: 200")

        report = extractor.extract(Path("sample.png"), "2026-07-02", "hash-1")

        self.assertIn("accounts_worked", report.missing_fields)
        self.assertTrue(report.needs_manual_review)

    def test_accounts_label_counts_as_accounts_worked(self) -> None:
        extractor = FakeExtractor(
            """
            Accounts: 101
            Attempts: 604
            Live Contacts: 7
            Contact Rate: 1.16%
            """
        )

        report = extractor.extract(Path("sample.png"), "2026-07-13", "hash-1")

        self.assertEqual(report.metric_value("accounts_worked"), 101)
        self.assertEqual(report.metric_value("attempts"), 604)
        self.assertEqual(report.metric_value("contact_rate"), 1.16)

    def test_required_fields_from_region_ocr_pass_quality_gate(self) -> None:
        extractor = RegionFakeExtractor(
            {
                "overview_cards": "Accounts Attempts RPC Contact Rate Close Rate $ Per Contact\n110 735 7 0.95% 57.14% 509.57",
                "activity_section": "Activated Moved to Hot Live Message No Answer Left Message Average / Agent\n53 0 21 3 277 245.00",
                "money_section": "Posted 3964.42 1549.40 4\nTotal Future 2838.00 1135.00 17",
                "whiteboard": "CSOLO 40.00 40.00 0.00\nKMAD 500.82 0.00 0.00",
            }
        )

        report = extractor.extract(Path("sample.png"), "2026-07-01", "hash-1")

        self.assertTrue(report.passes_quality_gate)
        self.assertEqual(report.metric_value("accounts_worked"), 110)
        self.assertEqual(report.metric_value("attempts"), 735)
        self.assertEqual(report.metric_value("live_contacts"), 21)
        self.assertEqual(report.metric_value("contact_rate"), 0.95)
        self.assertEqual(report.metric_value("posted_cash"), 3964.42)

    def test_collector_extraction_requires_whiteboard_and_allowlist(self) -> None:
        extractor = RegionFakeExtractor(
            {
                "full_image": "Atlanta, GA 30328.00\nCSOLO 999.00",
                "whiteboard": "CSOLO 40.00 40.00 0.00\nKMAD 500.82 0.00 0.00\nATLANTA GA 30328.00",
            }
        )

        report = extractor.extract(Path("sample.png"), "2026-07-01", "hash-1")

        self.assertEqual(
            [row["collector"] for row in report.collector_totals],
            ["CSOLO", "KMAD"],
        )


class OperationsDatabaseTests(unittest.TestCase):
    def test_screenshot_hash_prevents_duplicate_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = OperationsDatabase(Path(temp_dir) / "ops.sqlite3")
            db.initialize()
            screenshot = SavedScreenshot(
                message_id="message-1",
                image_id="image-1",
                created_at="2026-07-02T21:20:00Z",
                report_date="2026-07-02",
                path=Path(temp_dir) / "shot.png",
                sha256="abc123",
            )

            db.save_screenshot(screenshot)

            self.assertTrue(db.screenshot_exists("message-1", "image-1"))
            self.assertTrue(db.screenshot_exists("other-message", "other-image", "abc123"))

    def test_previous_report_returns_prior_available_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = OperationsDatabase(Path(temp_dir) / "ops.sqlite3")
            db.initialize()
            older = build_report("2026-07-01", "hash-old", posted_cash=100)
            newer = build_report("2026-07-02", "hash-new", posted_cash=200)
            db.save_report(older, "older")
            db.save_report(newer, "newer")

            previous = db.previous_report("2026-07-02", "hash-new")

            self.assertIsNotNone(previous)
            self.assertEqual(previous["screenshot_hash"], "hash-old")


class OperationsReportTests(unittest.TestCase):
    def test_summary_includes_snapshot_trends_and_quality(self) -> None:
        report = build_report("2026-07-02", "hash-new", posted_cash=200, attempts=150, live_contacts=30)
        previous = {
            "metrics": {
                "posted_cash": {"value": 100},
                "posted_fees": {"value": 0},
                "green_cleared_cash": {"value": 0},
                "attempts": {"value": 125},
                "live_contacts": {"value": 25},
                "future_scheduled_cash": {"value": 300},
                "future_scheduled_fees": {"value": 0},
            }
        }

        summary = build_summary_text(report, previous)

        self.assertIn("Total Collected: $200.00", summary)
        self.assertIn("Collections: Up $100.00", summary)
        self.assertIn("Attempts: Up 25", summary)
        self.assertIn("Manual review needed: No", summary)
        self.assertIn("UCM Daily Operations Detail", summary)

    def test_operations_message_uses_ucm_executive_brief_branding(self) -> None:
        report = build_report("2026-07-02", "hash-new", posted_cash=200, attempts=150, live_contacts=30)

        message = build_operations_message(report, None)

        self.assertEqual(message.title, "UCM Daily Operations Brief")
        self.assertIn("📊 UCM Daily Operations Brief", message.text)
        self.assertIn("Thursday, July 2, 2026", message.text)
        self.assertIn("💰 Money", message.text)
        self.assertIn("🤖 AI Confidence", message.text)
        self.assertNotIn("".join(["U", "A", "S"]), message.text)

    def test_atlanta_ga_is_never_top_collector(self) -> None:
        report = build_report("2026-07-02", "hash-new", posted_cash=200)
        report = ExtractedReport(
            report_date=report.report_date,
            screenshot_hash=report.screenshot_hash,
            screenshot_path=report.screenshot_path,
            ocr_text=report.ocr_text,
            metrics=report.metrics,
            collector_totals=[
                {"collector": "Atlanta, GA", "total": 30328.00, "source": "whiteboard"},
                {"collector": "CSOLO", "total": 40.00, "source": "whiteboard"},
            ],
            missing_fields=[],
            manual_review_notes=[],
        )

        summary = build_summary_text(report, None)

        self.assertIn("Top Collector: Manual review", summary)
        self.assertNotIn("Atlanta, GA ($30,328.00)", summary)

    def test_low_quality_report_includes_quality_scores(self) -> None:
        report = build_report("2026-07-02", "hash-new", posted_cash=200)
        report.metrics["attempts"] = MetricValue(None, "", 0.0)

        summary = build_summary_text(report, None)

        self.assertIn("Completeness score:", summary)
        self.assertIn("Confidence score:", summary)
        self.assertIn("Quality gate passed: No", summary)

    def test_daily_brief_output_is_unchanged_by_historical_context(self) -> None:
        report = build_report("2026-07-08", "hash-new", posted_cash=200, attempts=150, live_contacts=30)
        previous = report_row(build_report("2026-07-07", "hash-prev", posted_cash=100, attempts=120, live_contacts=20))
        history = {
            "rolling_7_collections": 10000.0,
            "rolling_30_collections": 10000.0,
            "same_weekday_collections": 10000.0,
            "rolling_7_attempts": 999.0,
            "rolling_7_live_contacts": 999.0,
            "rolling_7_contact_rate": 99.0,
        }

        without_history = build_operations_message(report, previous).text
        with_history = build_operations_message(report, previous, history=history).text

        self.assertEqual(with_history, without_history)


class OperationsServiceTests(unittest.TestCase):
    def test_online_payment_screenshot_is_rejected(self) -> None:
        classifier = OperationsScreenshotClassifier("fake-tesseract")

        result = classifier.classify_text(
            "Online Payment - Account C205815 Credit or Debit Card From support@unitedaccountservices.com"
        )

        self.assertFalse(result.is_operations_dashboard)
        self.assertIn("online_payment", result.rejected_indicators)

    def test_scollect_dashboard_screenshot_is_accepted(self) -> None:
        classifier = OperationsScreenshotClassifier("fake-tesseract")

        result = classifier.classify_text(
            "SCollect Admin Tools Accounts Worked Attempts RPC Contact Rate Close Rate "
            "Collections in Range Posted Cash Pending Cash Future Scheduled Cash Whiteboard"
        )

        self.assertTrue(result.is_operations_dashboard)
        self.assertIn("overview_cards", result.matched_indicators)

    def test_scollect_lower_dashboard_screenshot_is_accepted(self) -> None:
        classifier = OperationsScreenshotClassifier("fake-tesseract")

        result = classifier.classify_text(
            "Run During Time Posted Pending Total Run Rate Collections By Portfolio "
            "portfolio accounts_worked attempts calls_per_act rpc leftmsglive lftmsgmach no_answer"
        )

        self.assertTrue(result.is_operations_dashboard)
        self.assertIn("portfolio_table", result.matched_indicators)

    def test_scollect_whiteboard_run_during_time_screenshot_is_accepted(self) -> None:
        classifier = OperationsScreenshotClassifier("fake-tesseract")

        result = classifier.classify_text(
            "Run During Time Posted Pending Total Run Rate Collections in Range Whiteboard "
            "Current Month Future Cash Fee Count"
        )

        self.assertTrue(result.is_operations_dashboard)
        self.assertIn("whiteboard", result.matched_indicators)

    def test_local_image_is_processed_once_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = base / "screenshot.png"
            source.write_bytes(b"fake-image")
            settings = build_settings(base)
            agent = build_test_agent(settings)

            first = agent.process_local_image(source, "2026-07-02")
            second = agent.process_local_image(source, "2026-07-02")

            self.assertEqual(agent.ocr.calls, 1)
            self.assertEqual(first, second)

    def test_low_quality_ocr_posts_manual_review_alert_not_normal_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = base / "screenshot.png"
            source.write_bytes(b"fake-image")
            settings = build_settings(base)
            agent = build_test_agent(settings, ocr=LowQualityExtractor())

            agent.process_local_image(source, "2026-07-02")

            self.assertEqual(agent.teams.sent_count, 1)
            self.assertIn("Manual Review Required", agent.teams.messages[0].text)
            self.assertNotIn("GOOD DAY", agent.teams.messages[0].text)

    def test_daily_scan_skips_prior_day_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base)
            image = build_teams_image("message-1", "image-1", "2026-07-02T21:20:00Z", b"old-image")
            agent = build_test_agent(settings, graph=FakeHistoryGraph([image]), ocr=PassingExtractor())
            agent._inside_daily_window = lambda: True
            agent._today = lambda: "2026-07-03"

            processed = agent.scan_once()

            self.assertEqual(processed, 0)
            self.assertEqual(agent.ocr.calls, 0)
            self.assertEqual(agent.teams.sent_count, 0)

    def test_daily_scan_posts_only_once_per_report_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base, dry_run=False)
            images = [
                build_teams_image("message-1", "image-1", "2026-07-03T21:01:00Z", b"image-1"),
                build_teams_image("message-2", "image-2", "2026-07-03T21:02:00Z", b"image-2"),
            ]
            agent = build_test_agent(settings, graph=FakeHistoryGraph(images), ocr=LowQualityExtractor())
            agent._inside_daily_window = lambda: True
            agent._today = lambda: "2026-07-03"

            processed = agent.scan_once()

            self.assertEqual(processed, 2)
            self.assertEqual(agent.ocr.calls, 2)
            self.assertEqual(agent.teams.sent_count, 1)
            self.assertTrue(agent.db.report_posted_for_date("2026-07-03"))

    def test_history_import_is_idempotent_and_skips_duplicate_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base)
            image = build_teams_image("message-1", "image-1", "2026-07-01T21:20:00Z", b"same-image")
            agent = build_test_agent(settings, graph=FakeHistoryGraph([image]), ocr=PassingExtractor())

            first = agent.import_history(30)
            second = agent.import_history(30)

            self.assertEqual(first.successfully_imported, 1)
            self.assertEqual(second.duplicates_skipped, 1)
            self.assertEqual(agent.ocr.calls, 1)
            self.assertEqual(len(agent.db.reports_between()), 1)

    def test_history_import_dry_run_creates_no_database_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base)
            image = build_teams_image("message-1", "image-1", "2026-07-01T21:20:00Z", b"dry-run")
            agent = build_test_agent(settings, graph=FakeHistoryGraph([image]), ocr=PassingExtractor())

            summary = agent.import_history(30, dry_run=True)

            self.assertEqual(summary.screenshots_found, 1)
            self.assertEqual(len(agent.db.reports_between()), 0)
            self.assertEqual(agent.db.screenshots_for_date("2026-07-01"), [])
            self.assertEqual(agent.ocr.calls, 0)

    def test_low_quality_history_import_is_saved_as_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base)
            image = build_teams_image("message-1", "image-1", "2026-07-01T21:20:00Z", b"low-quality")
            agent = build_test_agent(settings, graph=FakeHistoryGraph([image]), ocr=LowQualityExtractor())

            summary = agent.import_history(30)

            self.assertEqual(summary.manual_review_required, 1)
            self.assertEqual(agent.teams.sent_count, 0)
            reports = agent.db.reports_between()
            self.assertEqual(len(reports), 1)
            self.assertIsNone(reports[0]["metrics"]["attempts"]["value"])

    def test_history_import_never_posts_to_teams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base)
            image = build_teams_image("message-1", "image-1", "2026-07-01T21:20:00Z", b"post-guard")
            agent = build_test_agent(settings, graph=FakeHistoryGraph([image]), ocr=PassingExtractor())

            agent.import_history(30)

            self.assertEqual(agent.teams.sent_count, 0)

    def test_history_import_skips_non_operations_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = build_settings(base)
            image = build_teams_image("message-1", "image-1", "2026-07-01T21:20:00Z", b"payment")
            agent = build_test_agent(
                settings,
                graph=FakeHistoryGraph([image]),
                ocr=PassingExtractor(),
                classifier=FakeClassifier(False),
            )

            summary = agent.import_history(30)

            self.assertEqual(summary.non_operations_skipped, 1)
            self.assertEqual(agent.ocr.calls, 0)
            self.assertEqual(len(agent.db.reports_between()), 0)

    def test_non_operations_reports_do_not_affect_history_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = OperationsDatabase(Path(temp_dir) / "ops.sqlite3")
            db.initialize()
            valid = build_report("2026-07-01", "valid", posted_cash=100)
            bad = build_report("2026-07-02", "bad", posted_cash=9999)
            db.save_report(valid, "valid")
            db.save_report(bad, "bad")
            db.mark_non_operations_report(
                2,
                ScreenshotClassification(False, "Online Payment screenshot", [], ["online_payment"]).to_dict(),
            )

            reports = db.reports_between()
            summary = build_historical_summary(reports)

            self.assertEqual(len(reports), 1)
            self.assertEqual(summary.total_collected, 100)

    def test_historical_summary_calculations(self) -> None:
        reports = [
            report_row(build_report("2026-07-01", "hash-1", posted_cash=100, attempts=10, live_contacts=2)),
            report_row(build_report("2026-07-02", "hash-2", posted_cash=300, attempts=30, live_contacts=6)),
            report_row(build_report("2026-07-03", "hash-3", posted_cash=50, attempts=20, live_contacts=4)),
        ]
        reports[0]["collector_totals"] = [{"collector": "CSOLO", "total": 25.0, "source": "whiteboard"}]
        reports[1]["collector_totals"] = [{"collector": "KMAD", "total": 75.0, "source": "whiteboard"}]

        summary = build_historical_summary(reports)

        self.assertEqual(summary.total_collected, 450)
        self.assertEqual(summary.average_daily_collections, 150)
        self.assertEqual(summary.average_calls, 20)
        self.assertEqual(summary.average_live_contacts, 4)
        self.assertEqual(summary.best_collection_day, ("2026-07-02", 300))
        self.assertEqual(summary.lowest_collection_day, ("2026-07-03", 50))
        self.assertEqual(summary.top_collector, ("KMAD", 75.0))
        self.assertEqual(summary.quality_passing_reports, 3)

    def test_historical_trend_analysis_is_additive(self) -> None:
        previous_reports = [
            report_row(build_report("2026-07-01", "hash-1", posted_cash=100, attempts=10, live_contacts=2)),
            report_row(build_report("2026-07-02", "hash-2", posted_cash=300, attempts=30, live_contacts=6)),
        ]
        current = report_row(build_report("2026-07-03", "hash-3", posted_cash=500, attempts=40, live_contacts=10))

        context = build_historical_context("2026-07-03", previous_reports)
        analysis = build_historical_trend_analysis(current, context)

        self.assertEqual(context.rolling_7_collections, 200)
        self.assertEqual(analysis.collections_vs_7_day_average, 300)
        self.assertEqual(analysis.attempts_vs_7_day_average, 20)
        self.assertEqual(analysis.live_contacts_vs_7_day_average, 6)


class OperationsSetupCheckTests(unittest.TestCase):
    def test_setup_check_reports_missing_auth_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = build_settings(Path(temp_dir))
            checks = run_setup_checks(settings)
            output = format_setup_checks(checks)

            self.assertIn("[FAIL] TEAMS_GRAPH_TENANT_ID or MS_GRAPH_TENANT_ID", output)
            self.assertNotIn("TEAMS_GRAPH_CLIENT_SECRET", output)
            self.assertIn("python main.py ops-auth", output)
            self.assertTrue((Path(temp_dir) / "ops.sqlite3").exists())

    def test_setup_check_passes_when_required_values_and_token_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            token = base / "token.bin"
            token.write_text("token")
            settings = build_settings(
                base,
                graph_tenant_id="tenant",
                graph_client_id="client",
                graph_client_secret="secret",
                leadership_chat_id="chat",
                teams_graph_token_cache_path=token,
                ocr_command=python_version_command(),
            )

            checks = run_setup_checks(settings)

            self.assertTrue(all(check.passed for check in checks))


class FakeExtractor(ScreenshotOcrExtractor):
    def __init__(self, text: str, confidence: float = 0.9) -> None:
        super().__init__("fake-tesseract", 0.72, ("CSOLO", "KMAD", "UNITED HOUSE"))
        self.text = text
        self.confidence = confidence

    def _run_region_ocr(self, image_path: Path, temp_dir: Path):
        return (
            [
                OcrRegion(
                    name="full_image",
                    path=image_path,
                    coordinates=(0, 0, 100, 100),
                    relative={"x": 0, "y": 0, "w": 1, "h": 1},
                    text=self.text,
                    confidence=self.confidence,
                ),
                OcrRegion(
                    name="whiteboard",
                    path=image_path,
                    coordinates=(0, 50, 100, 50),
                    relative={"x": 0, "y": 0.5, "w": 1, "h": 0.5},
                    text=self.text,
                    confidence=self.confidence,
                ),
            ],
            (100, 100),
        )


class RegionFakeExtractor(ScreenshotOcrExtractor):
    def __init__(self, region_text: dict[str, str], confidence: float = 0.9) -> None:
        super().__init__("fake-tesseract", 0.72, ("CSOLO", "KMAD", "UNITED HOUSE"))
        self.region_text = region_text
        self.confidence = confidence

    def _run_region_ocr(self, image_path: Path, temp_dir: Path):
        regions = []
        for index, (name, text) in enumerate(self.region_text.items()):
            regions.append(
                OcrRegion(
                    name=name,
                    path=image_path,
                    coordinates=(0, index * 10, 100, 10),
                    relative={"x": 0, "y": index / 10, "w": 1, "h": 0.1},
                    text=text,
                    confidence=self.confidence,
                )
            )
        return regions, (100, 100)


class CountingExtractor(FakeExtractor):
    def __init__(self, text: str | None = None) -> None:
        super().__init__(text or "Accounts Worked: 10\nAttempts: 20\nLive Contacts: 5\nPosted Cash: $100.00")
        self.calls = 0

    def extract(self, image_path: Path, report_date: str, screenshot_hash: str, debug_dir=None) -> ExtractedReport:
        self.calls += 1
        return super().extract(image_path, report_date, screenshot_hash, debug_dir=debug_dir)


class FakeTeams:
    def __init__(self) -> None:
        self.sent_count = 0
        self.messages = []

    def send(self, message) -> None:
        self.sent_count += 1
        self.messages.append(message)


class FakeHistoryGraph:
    def __init__(self, images: list[TeamsImage]) -> None:
        self.images = images

    def find_recent_images(self) -> list[TeamsImage]:
        return self.images

    def find_images_for_days(self, days: int) -> list[TeamsImage]:
        return self.images


class PassingExtractor(CountingExtractor):
    def __init__(self) -> None:
        super().__init__(
            "Accounts Worked: 10\nAttempts: 20\nLive Contacts: 5\n"
            "Contact Rate: 25.00%\nClose Rate: 10.00%\nPosted Cash: $100.00\n"
            "CSOLO $40.00\nKMAD $60.00"
        )


class LowQualityExtractor(CountingExtractor):
    def __init__(self) -> None:
        super().__init__("Unreadable dashboard text")


class FakeClassifier:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted

    def classify_image(self, image_path: Path, *, existing_text: str = "") -> ScreenshotClassification:
        return ScreenshotClassification(
            self.accepted,
            "Accepted test dashboard" if self.accepted else "Rejected test image",
            ["overview_cards"] if self.accepted else [],
            [] if self.accepted else ["online_payment"],
        )


def build_settings(base: Path, **overrides) -> SimpleNamespace:
    values = dict(
        dry_run=True,
        graph_tenant_id="",
        graph_client_id="",
        graph_client_secret="",
        leadership_chat_id="",
        teams_graph_token_cache_path=base / "teams-token.bin",
        ocr_command="fake-tesseract",
    )
    values.update(overrides)
    return SimpleNamespace(
        dry_run=values["dry_run"],
        database_path=base / "ops.sqlite3",
        timezone="America/New_York",
        screenshots_dir=base / "screenshots",
        reports_dir=base / "reports",
        ocr_command=values["ocr_command"],
        ocr_min_confidence=0.72,
        post_summary_to_teams=True,
        ocr_debug=False,
        low_quality_action="alert",
        collector_codes=("CSOLO", "KMAD", "UNITED HOUSE"),
        graph_tenant_id=values["graph_tenant_id"],
        graph_client_id=values["graph_client_id"],
        graph_client_secret=values["graph_client_secret"],
        leadership_chat_id=values["leadership_chat_id"],
        teams_graph_token_cache_path=values["teams_graph_token_cache_path"],
    )


def build_test_agent(
    settings: SimpleNamespace,
    *,
    graph=None,
    ocr: CountingExtractor | None = None,
    classifier=None,
) -> OperationsIntelligenceAgent:
    agent = OperationsIntelligenceAgent.__new__(OperationsIntelligenceAgent)
    agent.settings = settings
    agent.db = OperationsDatabase(settings.database_path)
    agent.graph = graph
    agent.ocr = ocr or CountingExtractor()
    agent.classifier = classifier or FakeClassifier(True)
    agent.teams = FakeTeams()
    return agent


def build_teams_image(message_id: str, image_id: str, created_at: str, content: bytes) -> TeamsImage:
    return TeamsImage(
        message_id=message_id,
        image_id=image_id,
        created_at=created_at,
        file_name=f"{image_id}.png",
        content_type="image/png",
        content=content,
    )


def build_report(
    report_date: str,
    screenshot_hash: str,
    *,
    posted_cash: float,
    attempts: int = 20,
    live_contacts: int = 5,
) -> ExtractedReport:
    metrics = {
        "posted_cash": MetricValue(posted_cash, str(posted_cash), 0.95),
        "posted_fees": MetricValue(0, "0", 0.95),
        "green_cleared_cash": MetricValue(0, "0", 0.95),
        "pending_cash": MetricValue(25, "25", 0.95),
        "pending_fees": MetricValue(0, "0", 0.95),
        "future_scheduled_cash": MetricValue(500, "500", 0.95),
        "future_scheduled_fees": MetricValue(0, "0", 0.95),
        "accounts_worked": MetricValue(10, "10", 0.95),
        "attempts": MetricValue(attempts, str(attempts), 0.95),
        "live_contacts": MetricValue(live_contacts, str(live_contacts), 0.95),
        "contact_rate": MetricValue(25.0, "25%", 0.95),
        "close_rate": MetricValue(10.0, "10%", 0.95),
    }
    return ExtractedReport(
        report_date=report_date,
        screenshot_hash=screenshot_hash,
        screenshot_path=Path("shot.png"),
        ocr_text="ocr",
        metrics=metrics,
        collector_totals=[{"collector": "Jane Collector", "total": posted_cash}],
        missing_fields=[],
        manual_review_notes=[],
    )


def report_row(report: ExtractedReport) -> dict:
    return {
        "report_date": report.report_date,
        "screenshot_hash": report.screenshot_hash,
        "metrics": {field: metric.to_dict() for field, metric in report.metrics.items()},
        "collector_totals": report.collector_totals,
        "missing_fields": report.missing_fields,
        "manual_review_notes": report.manual_review_notes,
    }


def python_version_command() -> str:
    import sys

    return sys.executable
