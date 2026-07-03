from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.database import SQLiteDatabase

from .models import ExtractedReport, SavedScreenshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    image_id TEXT NOT NULL,
    report_date TEXT NOT NULL,
    created_at_teams TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(message_id, image_id),
    UNIQUE(sha256)
);

CREATE TABLE IF NOT EXISTS ops_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    screenshot_hash TEXT NOT NULL,
    screenshot_path TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    collector_totals_json TEXT NOT NULL,
    ocr_text TEXT NOT NULL,
    missing_fields_json TEXT NOT NULL,
    manual_review_notes_json TEXT NOT NULL,
    summary_text TEXT,
    posted_to_teams INTEGER NOT NULL DEFAULT 0,
    original_metrics_json TEXT,
    original_collector_totals_json TEXT,
    original_ocr_text TEXT,
    manual_review INTEGER,
    manually_edited_fields_json TEXT,
    approved_at TEXT,
    last_reprocessed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(screenshot_hash)
);

CREATE INDEX IF NOT EXISTS idx_ops_screenshots_report_date
ON ops_screenshots(report_date);

CREATE INDEX IF NOT EXISTS idx_ops_reports_report_date
ON ops_reports(report_date);
"""


class OperationsDatabase(SQLiteDatabase):
    def initialize(self) -> None:
        self.initialize_schema(SCHEMA)
        self._migrate_ops_reports()

    def _migrate_ops_reports(self) -> None:
        columns = {
            "original_metrics_json": "TEXT",
            "original_collector_totals_json": "TEXT",
            "original_ocr_text": "TEXT",
            "manual_review": "INTEGER",
            "manually_edited_fields_json": "TEXT",
            "approved_at": "TEXT",
            "last_reprocessed_at": "TEXT",
        }
        with self.connect() as conn:
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(ops_reports)").fetchall()}
            for column, column_type in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE ops_reports ADD COLUMN {column} {column_type}")

    def screenshot_exists(self, message_id: str, image_id: str, sha256: str | None = None) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM ops_screenshots
                WHERE (message_id = ? AND image_id = ?) OR (? IS NOT NULL AND sha256 = ?)
                """,
                (message_id, image_id, sha256, sha256),
            ).fetchone()
            return row is not None

    def save_screenshot(self, screenshot: SavedScreenshot) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO ops_screenshots
                (message_id, image_id, report_date, created_at_teams, file_path, sha256, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    screenshot.message_id,
                    screenshot.image_id,
                    screenshot.report_date,
                    screenshot.created_at,
                    str(screenshot.path),
                    screenshot.sha256,
                    "saved",
                    now,
                    now,
                ),
            )

    def screenshots_for_date(self, report_date: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ops_screenshots
                WHERE report_date = ?
                ORDER BY created_at_teams ASC, created_at ASC
                """,
                (report_date,),
            ).fetchall()
            return [dict(row) for row in rows]

    def report_exists_for_hash(self, screenshot_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM ops_reports WHERE screenshot_hash = ?",
                (screenshot_hash,),
            ).fetchone()
            return row is not None

    def report_by_hash(self, screenshot_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ops_reports WHERE screenshot_hash = ?",
                (screenshot_hash,),
            ).fetchone()
            return _row_to_report(row)

    def save_report(self, report: ExtractedReport, summary_text: str) -> None:
        now = _utc_now()
        metrics = {field: metric.to_dict() for field, metric in report.metrics.items()}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ops_reports
                (report_date, screenshot_hash, screenshot_path, metrics_json, collector_totals_json,
                 ocr_text, missing_fields_json, manual_review_notes_json, summary_text,
                 posted_to_teams, manual_review, manually_edited_fields_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT posted_to_teams FROM ops_reports WHERE screenshot_hash = ?), 0),
                        ?,
                        COALESCE((SELECT manually_edited_fields_json FROM ops_reports WHERE screenshot_hash = ?), '[]'),
                        COALESCE((SELECT created_at FROM ops_reports WHERE screenshot_hash = ?), ?), ?)
                """,
                (
                    report.report_date,
                    report.screenshot_hash,
                    str(report.screenshot_path),
                    json.dumps(metrics, sort_keys=True),
                    json.dumps(report.collector_totals, sort_keys=True),
                    report.ocr_text,
                    json.dumps(report.missing_fields),
                    json.dumps(report.manual_review_notes),
                    summary_text,
                    report.screenshot_hash,
                    0 if report.passes_quality_gate else 1,
                    report.screenshot_hash,
                    report.screenshot_hash,
                    now,
                    now,
                ),
            )

    def report_by_id(self, report_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ops_reports WHERE id = ?", (report_id,)).fetchone()
            return _row_to_report(row)

    def save_manual_corrections(
        self,
        report_id: int,
        metrics: dict[str, Any],
        collector_totals: list[dict[str, Any]],
        edited_fields: list[str],
    ) -> None:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ops_reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Operations report {report_id} was not found.")
            existing_edited = _json_load(row["manually_edited_fields_json"], [])
            merged_fields = sorted({*existing_edited, *edited_fields})
            conn.execute(
                """
                UPDATE ops_reports
                SET metrics_json = ?,
                    collector_totals_json = ?,
                    original_metrics_json = COALESCE(original_metrics_json, ?),
                    original_collector_totals_json = COALESCE(original_collector_totals_json, ?),
                    original_ocr_text = COALESCE(original_ocr_text, ?),
                    manually_edited_fields_json = ?,
                    manual_review = 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(metrics, sort_keys=True),
                    json.dumps(collector_totals, sort_keys=True),
                    row["metrics_json"],
                    row["collector_totals_json"],
                    row["ocr_text"],
                    json.dumps(merged_fields, sort_keys=True),
                    now,
                    report_id,
                ),
            )

    def approve_report(self, report_id: int) -> None:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ops_reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Operations report {report_id} was not found.")
            conn.execute(
                """
                UPDATE ops_reports
                SET missing_fields_json = '[]',
                    manual_review_notes_json = '[]',
                    original_metrics_json = COALESCE(original_metrics_json, ?),
                    original_collector_totals_json = COALESCE(original_collector_totals_json, ?),
                    original_ocr_text = COALESCE(original_ocr_text, ?),
                    manual_review = 0,
                    approved_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    row["metrics_json"],
                    row["collector_totals_json"],
                    row["ocr_text"],
                    now,
                    now,
                    report_id,
                ),
            )

    def replace_reprocessed_report(self, report_id: int, report: ExtractedReport, summary_text: str) -> None:
        now = _utc_now()
        metrics = {field: metric.to_dict() for field, metric in report.metrics.items()}
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ops_reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise ValueError(f"Operations report {report_id} was not found.")
            conn.execute(
                """
                UPDATE ops_reports
                SET screenshot_hash = ?,
                    screenshot_path = ?,
                    metrics_json = ?,
                    collector_totals_json = ?,
                    ocr_text = ?,
                    missing_fields_json = ?,
                    manual_review_notes_json = ?,
                    summary_text = ?,
                    original_metrics_json = COALESCE(original_metrics_json, ?),
                    original_collector_totals_json = COALESCE(original_collector_totals_json, ?),
                    original_ocr_text = COALESCE(original_ocr_text, ?),
                    manual_review = ?,
                    last_reprocessed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    report.screenshot_hash,
                    str(report.screenshot_path),
                    json.dumps(metrics, sort_keys=True),
                    json.dumps(report.collector_totals, sort_keys=True),
                    report.ocr_text,
                    json.dumps(report.missing_fields),
                    json.dumps(report.manual_review_notes),
                    summary_text,
                    row["metrics_json"],
                    row["collector_totals_json"],
                    row["ocr_text"],
                    0 if report.passes_quality_gate else 1,
                    now,
                    now,
                    report_id,
                ),
            )

    def mark_report_posted(self, screenshot_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE ops_reports SET posted_to_teams = 1, updated_at = ? WHERE screenshot_hash = ?",
                (_utc_now(), screenshot_hash),
            )

    def latest_report(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ops_reports ORDER BY report_date DESC, created_at DESC LIMIT 1"
            ).fetchone()
            return _row_to_report(row)

    def reports_between(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ops_reports WHERE 1 = 1"
        params: list[str] = []
        if start_date:
            query += " AND report_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND report_date <= ?"
            params.append(end_date)
        query += " ORDER BY report_date ASC, created_at ASC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [report for row in rows if (report := _row_to_report(row))]

    def previous_report(self, report_date: str, screenshot_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ops_reports
                WHERE screenshot_hash <> ? AND report_date <= ?
                ORDER BY report_date DESC, created_at DESC
                LIMIT 1
                """,
                (screenshot_hash, report_date),
            ).fetchone()
            return _row_to_report(row)

    def historical_reports_before(self, report_date: str, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ops_reports
                WHERE report_date < ?
                ORDER BY report_date DESC, created_at DESC
                LIMIT ?
                """,
                (report_date, limit),
            ).fetchall()
            reports = [report for row in rows if (report := _row_to_report(row))]
            return list(reversed(reports))


def _row_to_report(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["metrics"] = json.loads(data.pop("metrics_json"))
    data["collector_totals"] = json.loads(data.pop("collector_totals_json"))
    data["missing_fields"] = json.loads(data.pop("missing_fields_json"))
    data["manual_review_notes"] = json.loads(data.pop("manual_review_notes_json"))
    data["original_metrics"] = _json_load(data.pop("original_metrics_json", None), None)
    data["original_collector_totals"] = _json_load(data.pop("original_collector_totals_json", None), None)
    data["manually_edited_fields"] = _json_load(data.pop("manually_edited_fields_json", None), [])
    data["manual_review"] = None if data.get("manual_review") is None else bool(data["manual_review"])
    return data


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
