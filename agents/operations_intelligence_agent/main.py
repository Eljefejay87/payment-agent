from __future__ import annotations

import argparse
import json
import logging
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.logging import configure_logging
from shared.scheduler import AgentScheduler

from .config import load_operations_settings, validate_operations_settings
from .classifier import OperationsScreenshotClassifier
from .database import OperationsDatabase
from .graph_client import save_delegated_token
from .models import ExtractedReport, MetricValue
from .ocr import ScreenshotOcrExtractor
from .reports import build_detailed_report_text, build_operations_message
from .service import OperationsIntelligenceAgent
from .setup_check import format_setup_checks, run_setup_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="UCM Operations Intelligence Agent")
    parser.add_argument(
        "command",
        choices=[
            "ops-auth",
            "ops-init-db",
            "ops-check-setup",
            "ops-scan-once",
            "ops-run",
            "ops-process-image",
            "ops-debug-image",
            "ops-post-image",
            "ops-reprocess-date",
            "ops-post-report",
            "ops-import-history",
            "ops-audit-images",
        ],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    parser.add_argument("--force", action="store_true", help="Run outside the daily screenshot window.")
    parser.add_argument("--image", default="", help="Local screenshot path for ops-process-image.")
    parser.add_argument("--report-date", default="", help="Report date for local image processing, YYYY-MM-DD.")
    parser.add_argument("--date", default="", help="Report date for ops-reprocess-date or ops-post-report, YYYY-MM-DD.")
    parser.add_argument("--corrections-json", default="", help="Manual correction JSON for ops-reprocess-date without Teams posting.")
    parser.add_argument("--approve-corrections", action="store_true", help="Approve a manually corrected Operations report.")
    parser.add_argument("--dry-run", action="store_true", help="Do not post to Teams during reprocessing.")
    parser.add_argument("--days", type=int, default=30, help="Number of days for ops-import-history.")
    parser.add_argument("--debug", action="store_true", help="Save OCR debug files during history import.")
    parser.add_argument("--force-reprocess", action="store_true", help="Reprocess imported screenshots even if a report exists.")
    parser.add_argument("--mark-non-operations", action="store_true", help="Mark audited non-operations images as excluded.")
    args = parser.parse_args()

    settings = load_operations_settings(args.env_file)
    configure_logging(settings.log_level)

    if args.command == "ops-init-db":
        OperationsDatabase(settings.database_path).initialize()
        logging.info("Operations Intelligence database initialized at %s", settings.database_path)
        return 0

    if args.command == "ops-check-setup":
        checks = run_setup_checks(settings)
        print(format_setup_checks(checks))
        return 0 if all(check.passed for check in checks) else 2

    offline_image_mode = args.command in {"ops-process-image", "ops-debug-image", "ops-reprocess-date", "ops-audit-images"}
    errors = validate_operations_settings(settings, offline_image_mode=offline_image_mode)
    if args.command in {"ops-process-image", "ops-debug-image", "ops-post-image"} and not args.image:
        errors.append("--image is required.")
    if args.command in {"ops-reprocess-date", "ops-post-report"} and not args.date:
        errors.append(f"--date is required for {args.command}.")
    if args.command == "ops-import-history" and args.days < 1:
        errors.append("--days must be at least 1.")
    if args.command == "ops-audit-images" and args.days < 1:
        errors.append("--days must be at least 1.")
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    if args.command == "ops-auth":
        save_delegated_token(settings)
        logging.info("Teams delegated token saved at %s", settings.teams_graph_token_cache_path)
        return 0

    agent = OperationsIntelligenceAgent(settings)

    if args.command == "ops-process-image":
        summary = agent.process_local_image(Path(args.image), args.report_date or None)
        print(summary)
        return 0

    if args.command == "ops-post-image":
        summary = agent.process_local_image(Path(args.image), args.report_date or args.date or None)
        print(summary)
        return 0

    if args.command == "ops-debug-image":
        report_date = args.report_date or args.date or agent._today()
        debug_dir = _debug_dir(settings, report_date, Path(args.image).stem)
        extractor = _debug_extractor(settings)
        report = extractor.extract(Path(args.image), report_date, Path(args.image).stem, debug_dir=debug_dir)
        print(debug_dir)
        print(f"Quality gate passed: {report.passes_quality_gate}")
        print(f"Missing required fields: {', '.join(report.missing_quality_fields) or 'none'}")
        return 0

    if args.command == "ops-reprocess-date":
        if args.corrections_json:
            report_id = _apply_report_corrections(
                settings,
                args.date,
                Path(args.corrections_json),
                approve=args.approve_corrections,
            )
            print(f"Applied manual corrections to Operations report {report_id} for {args.date}. Nothing was posted to Teams.")
            return 0
        count, _ = _reprocess_date(settings, args.date)
        print(f"Reprocessed {count} screenshot(s) for {args.date} without posting to Teams.")
        return 0

    if args.command == "ops-post-report":
        count, posted = _reprocess_date(settings, args.date, post_to_teams=True)
        print(f"Reprocessed {count} screenshot(s) for {args.date}. Posted {posted} report(s) to Teams.")
        return 0

    if args.command == "ops-import-history":
        summary = agent.import_history(
            args.days,
            dry_run=args.dry_run,
            debug=args.debug,
            force_reprocess=args.force_reprocess,
        )
        print(summary.format())
        return 0

    if args.command == "ops-audit-images":
        print(_audit_images(settings, args.days, mark_non_operations=args.mark_non_operations))
        return 0

    if args.command == "ops-scan-once":
        agent.scan_once(force=args.force)
        return 0

    if args.command == "ops-run":
        scheduler = AgentScheduler()
        agent.initialize()
        logging.info(
            "Operations Intelligence Agent running. Checks every %s minute(s) between %s and %s.",
            settings.scan_interval_minutes,
            settings.daily_scan_start,
            settings.daily_scan_end,
        )
        scheduler.every_minutes(settings.scan_interval_minutes, agent.scan_once)
        agent.scan_once()
        scheduler.run_forever()

    return 0

def _debug_extractor(settings):
    return ScreenshotOcrExtractor(
        settings.ocr_command,
        settings.ocr_min_confidence,
        settings.collector_codes,
        debug_enabled=True,
        debug_root=settings.reports_dir / "debug",
    )


def _debug_dir(settings, report_date: str, name: str) -> Path:
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in name)[:80]
    return settings.reports_dir / "debug" / report_date / safe_name


def _apply_report_corrections(settings, report_date: str, corrections_path: Path, *, approve: bool = False) -> int:
    payload = json.loads(corrections_path.read_text())
    db = OperationsDatabase(settings.database_path)
    db.initialize()
    report_id = int(payload.get("report_id") or 0)
    if not report_id:
        reports = [
            report
            for report in db.reports_between(report_date, report_date)
            if report.get("is_operations_dashboard") is not False
        ]
        if not reports:
            raise ValueError(f"No Operations report found for {report_date}.")
        report_id = max(reports, key=lambda report: report.get("updated_at") or report.get("created_at") or "")["id"]
    report = db.report_by_id(report_id)
    if not report:
        raise ValueError(f"Operations report {report_id} was not found.")
    if report["report_date"] != report_date:
        raise ValueError(f"Operations report {report_id} is for {report['report_date']}, not {report_date}.")

    metrics = json.loads(json.dumps(report.get("metrics") or {}))
    edited_fields: list[str] = []
    for field, value in (payload.get("metrics") or {}).items():
        metrics[field] = {
            "value": value,
            "raw_text": payload.get("source_note", "Manual correction"),
            "confidence": 1.0,
            "needs_review": False,
            "manually_edited": True,
        }
        edited_fields.append(field)

    collector_totals = payload.get("collector_totals")
    if collector_totals is not None:
        edited_fields.append("collector_totals")
    else:
        collector_totals = report.get("collector_totals") or []

    db.save_manual_corrections(report_id, metrics, collector_totals, edited_fields)
    if approve:
        db.approve_report(report_id)
    corrected = db.report_by_id(report_id)
    if not corrected:
        raise ValueError(f"Operations report {report_id} was not found after correction.")
    _refresh_corrected_report_text(settings, db, corrected)
    return report_id


def _refresh_corrected_report_text(settings, db: OperationsDatabase, report: dict) -> None:
    metric_values = {
        field: MetricValue(metric.get("value"), metric.get("raw_text", ""), float(metric.get("confidence") or 0))
        for field, metric in (report.get("metrics") or {}).items()
    }
    extracted = ExtractedReport(
        report_date=report["report_date"],
        screenshot_hash=report["screenshot_hash"],
        screenshot_path=Path(report["screenshot_path"]),
        ocr_text=report.get("ocr_text") or "",
        metrics=metric_values,
        collector_totals=report.get("collector_totals") or [],
        missing_fields=report.get("missing_fields") or [],
        manual_review_notes=report.get("manual_review_notes") or [],
    )
    previous = db.previous_report(extracted.report_date, extracted.screenshot_hash)
    message = build_operations_message(extracted, previous)
    detailed = build_detailed_report_text(extracted, previous)
    now = datetime.now(ZoneInfo("UTC")).isoformat()
    with sqlite3.connect(settings.database_path) as conn:
        conn.execute(
            "UPDATE ops_reports SET summary_text = ?, updated_at = ? WHERE id = ?",
            (message.text, now, report["id"]),
        )
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / f"{extracted.report_date}.txt").write_text(detailed)


def _reprocess_date(settings, report_date: str, *, post_to_teams: bool = False) -> tuple[int, int]:
    db = OperationsDatabase(settings.database_path)
    db.initialize()
    extractor = _debug_extractor(settings)
    rows = db.screenshots_for_date(report_date)
    postable: list[tuple[float, float, str, object, object, object]] = []
    last_report = None
    last_message = None
    last_previous = None
    for row in rows:
        screenshot_path = Path(row["file_path"])
        debug_dir = _debug_dir(settings, report_date, row["sha256"][:12])
        report = extractor.extract(screenshot_path, report_date, row["sha256"], debug_dir=debug_dir)
        previous = db.previous_report(report.report_date, report.screenshot_hash)
        message = build_operations_message(report, previous)
        db.save_report(report, message.text)
        last_report = report
        last_message = message
        last_previous = previous
        if report.passes_quality_gate:
            postable.append(
                (
                    report.completeness_score,
                    report.confidence_score,
                    str(row.get("created_at_teams") or ""),
                    report,
                    previous,
                    message,
                )
            )

    selected = max(postable, default=None, key=lambda item: (item[0], item[1], item[2]))
    if selected is not None:
        _, _, _, selected_report, selected_previous, selected_message = selected
    else:
        selected_report, selected_previous, selected_message = last_report, last_previous, last_message

    if selected_report is not None:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        (settings.reports_dir / f"{selected_report.report_date}.txt").write_text(
            build_detailed_report_text(selected_report, selected_previous)
        )

    posted = 0
    if post_to_teams:
        if selected is None:
            logging.error("No quality-passing Operations report found for %s. Nothing was posted.", report_date)
        elif db.report_posted_for_date(report_date):
            logging.warning("Operations report for %s was already posted to Teams; skipping duplicate post.", report_date)
        else:
            agent = OperationsIntelligenceAgent(settings)
            agent.teams.send(selected_message)
            if not settings.dry_run:
                db.mark_report_posted(selected_report.screenshot_hash)
            posted = 1
            logging.info("Posted corrected Operations report for %s to Teams", report_date)
    return len(rows), posted


def _audit_images(settings, days: int, *, mark_non_operations: bool = False) -> str:
    db = OperationsDatabase(settings.database_path)
    db.initialize()
    classifier = OperationsScreenshotClassifier(settings.ocr_command)
    cutoff = (datetime.now(ZoneInfo(settings.timezone)).date() - timedelta(days=days - 1)).isoformat()
    valid = 0
    non_operations = 0
    excluded: list[str] = []
    with sqlite3.connect(settings.database_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT s.sha256, s.report_date, s.file_path, r.id AS report_id, r.ocr_text
            FROM ops_screenshots s
            LEFT JOIN ops_reports r ON r.screenshot_hash = s.sha256
            WHERE s.report_date >= ?
            ORDER BY s.report_date ASC, s.created_at ASC
            """,
            (cutoff,),
        ).fetchall()
    for row in rows:
        image_path = Path(row["file_path"])
        existing_text = row["ocr_text"] or ""
        classification = classifier.classify_image(image_path, existing_text=existing_text)
        if classification.is_operations_dashboard:
            valid += 1
        else:
            non_operations += 1
            label = f"{row['report_date']} screenshot={row['sha256'][:12]}"
            if row["report_id"]:
                label += f" report_id={row['report_id']}"
            label += f" reason={classification.reason}"
            excluded.append(label)
            if mark_non_operations:
                db.update_screenshot_classification(row["sha256"], classification.to_dict())
                if row["report_id"]:
                    db.mark_non_operations_report(int(row["report_id"]), classification.to_dict())
        if classification.is_operations_dashboard and mark_non_operations:
            db.update_screenshot_classification(row["sha256"], classification.to_dict())
            if row["report_id"]:
                db.update_report_classification(row["sha256"], classification.to_dict())

    lines = [
        "Operations image audit",
        f"- Days scanned: {days}",
        f"- Stored screenshots scanned: {len(rows)}",
        f"- Valid operations screenshots: {valid}",
        f"- Non-operations screenshots: {non_operations}",
        f"- Marked non-operations: {'Yes' if mark_non_operations else 'No'}",
    ]
    if excluded:
        lines.append("- Excluded candidates:")
        lines.extend(f"  - {item}" for item in excluded)
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
