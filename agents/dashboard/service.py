from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.data_layer.repository import InMemorySharedRecordRepository, SharedRecordRepository
from shared.data_layer.sqlite_repository import SQLiteSharedRecordRepository

from .shared_data import ReadOnlyDashboardDataService
from .review_actions import ReviewActionService

from agents.cash_flow_hq.config import load_cash_flow_settings
from agents.cash_flow_hq.service import CashFlowHQService, plain_rich_text, plain_title
from agents.dashboard.config import DashboardSettings
from agents.operations_intelligence_agent.config import load_operations_settings
from agents.operations_intelligence_agent.database import OperationsDatabase
from agents.operations_intelligence_agent.history import (
    build_historical_context,
    build_historical_summary,
    build_historical_trend_analysis,
)
from agents.operations_intelligence_agent.ocr import ScreenshotOcrExtractor
from agents.operations_intelligence_agent.reports import build_operations_message
from agents.payment_agent.config import Settings as PaymentSettings
from agents.payment_agent.database import PaymentDatabase
from agents.payment_agent.parser import cents_to_currency
from agents.payment_agent.reports import today_in_timezone
from agents.payment_agent.service import PaymentAgent
from agents.weekly_remit_agent.config import RemitSettings
from agents.weekly_remit_agent.database import RemitDatabase
from agents.weekly_remit_agent.file_detector import RemitFileValidationError, find_required_remit_files
from agents.weekly_remit_agent.service import WeeklyRemitAgent


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def empty_cash_flow_dashboard(message: str = "") -> dict:
    return {
        "status": "Unavailable" if message else "Ready",
        "message": message,
        "summary": {
            "due_this_week_total": "$0.00",
            "needs_review_count": 0,
            "upcoming_count": 0,
            "past_due_count": 0,
            "paid_count": 0,
        },
        "needs_attention": [],
        "upcoming_bills": [],
        "forecast": empty_cash_flow_forecast(),
    }


def empty_cash_flow_forecast() -> dict:
    periods = {
        key: {"total": "$0.00", "amount": 0.0, "count": 0, "progress": 0}
        for key in ("past_due", "due_today", "next_7_days", "next_30_days", "this_month")
    }
    return {
        "periods": periods,
        "payment_types": {
            "autopay": {"total": "$0.00", "amount": 0.0},
            "manual": {"total": "$0.00", "amount": 0.0},
        },
        "top_upcoming": [],
        "filters": {"categories": [], "vendors": [], "statuses": []},
    }


def build_cash_flow_dashboard(rows: list[dict], today: date | str) -> dict:
    if isinstance(today, str):
        today = date.fromisoformat(today)
    summary = empty_cash_flow_dashboard()["summary"]
    due_this_week_total = 0.0
    for row in rows:
        status = row.get("status", "")
        due_date = row.get("due_date")
        amount = row.get("amount")
        if status == "Needs Review":
            summary["needs_review_count"] += 1
        if status == "Upcoming":
            summary["upcoming_count"] += 1
        if status == "Paid":
            summary["paid_count"] += 1
        if due_date and due_date < today and status != "Paid":
            summary["past_due_count"] += 1
        if due_date and 0 <= (due_date - today).days <= 7 and isinstance(amount, (int, float)):
            due_this_week_total += float(amount)
    summary["due_this_week_total"] = format_cash_flow_money(due_this_week_total)
    needs_attention = [
        row
        for row in rows
        if row.get("status") == "Needs Review" or str(row.get("due_status", "")).startswith("Past Due")
    ]
    upcoming = sorted(
        [row for row in rows if row.get("status") == "Upcoming" and row.get("due_date")],
        key=lambda row: row["due_date"],
    )[:10]
    return {
        "status": "Ready",
        "message": "",
        "summary": summary,
        "needs_attention": needs_attention,
        "upcoming_bills": upcoming,
        "forecast": build_cash_flow_forecast(rows, today),
    }


def build_cash_flow_forecast(rows: list[dict], today: date) -> dict:
    forecast = empty_cash_flow_forecast()
    periods = forecast["periods"]
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    unpaid_rows = [row for row in rows if str(row.get("status", "")).strip().lower() != "paid"]

    def add_period(name: str, row: dict) -> None:
        periods[name]["count"] += 1
        amount = row.get("amount")
        if isinstance(amount, (int, float)):
            periods[name]["amount"] += float(amount)

    for row in unpaid_rows:
        due_date = row.get("due_date")
        if not isinstance(due_date, date):
            continue
        if due_date < today:
            add_period("past_due", row)
        if due_date == today:
            add_period("due_today", row)
        if today < due_date <= today + timedelta(days=7):
            add_period("next_7_days", row)
        if today < due_date <= today + timedelta(days=30):
            add_period("next_30_days", row)
        if today <= due_date <= month_end:
            add_period("this_month", row)

        amount = row.get("amount")
        if isinstance(amount, (int, float)):
            payment_type = str(row.get("payment_type", "")).strip().lower()
            key = "autopay" if payment_type in {"auto pay", "autopay"} else "manual"
            forecast["payment_types"][key]["amount"] += float(amount)

    max_amount = max((period["amount"] for period in periods.values()), default=0.0)
    for period in periods.values():
        period["total"] = format_cash_flow_money(period["amount"])
        period["progress"] = round((period["amount"] / max_amount) * 100) if max_amount else 0
    for payment_type in forecast["payment_types"].values():
        payment_type["total"] = format_cash_flow_money(payment_type["amount"])

    top_upcoming = sorted(
        [row for row in unpaid_rows if isinstance(row.get("due_date"), date)],
        key=lambda row: (row["due_date"], row.get("vendor") or row.get("expense_name") or ""),
    )[:10]
    forecast["top_upcoming"] = [
        {**row, "forecast_status": cash_flow_forecast_status(row, today)}
        for row in top_upcoming
    ]
    forecast["filters"] = {
        "categories": sorted({str(row.get("category", "")).strip() for row in rows if row.get("category")}),
        "vendors": sorted(
            {
                str(row.get("vendor") or row.get("expense_name") or "").strip()
                for row in rows
                if row.get("vendor") or row.get("expense_name")
            }
        ),
        "statuses": sorted({str(row.get("status", "")).strip() for row in rows if row.get("status")}),
    }
    return forecast


def cash_flow_forecast_status(row: dict, today: date) -> str:
    if str(row.get("status", "")).strip().lower() == "paid":
        return "Paid"
    due_date = row.get("due_date")
    if not isinstance(due_date, date):
        return "Upcoming"
    if due_date < today:
        return "Past Due"
    if due_date <= today + timedelta(days=7):
        return "Due Soon"
    return "Upcoming"


def cash_flow_notion_rows(pages: list[dict]) -> list[dict]:
    return [cash_flow_row_from_page(page) for page in pages]


def cash_flow_row_from_page(page: dict) -> dict:
    properties = page.get("properties", {})
    return {
        "vendor": plain_rich_text(properties.get("Vendor / Payee", {})),
        "expense_name": plain_title(properties.get("Expense Name", {}).get("title", [])),
        "amount": number_property(properties.get("Amount", {})),
        "due_date": date_property(properties.get("Due Date", {})),
        "due_status": formula_string(properties.get("Due Status", {})),
        "notes": plain_rich_text(properties.get("Notes", {})),
        "status": select_property_name(properties.get("Status", {})),
        "category": select_property_name(properties.get("Category", {})),
        "payment_type": select_property_name(properties.get("Payment Type", {})),
        "action_required": formula_string(properties.get("Action Required", {})),
    }


def number_property(property_value: dict) -> float | None:
    value = property_value.get("number")
    return float(value) if isinstance(value, (int, float)) else None


def date_property(property_value: dict) -> date | None:
    start = (property_value.get("date") or {}).get("start")
    if not start:
        return None
    try:
        return date.fromisoformat(start[:10])
    except ValueError:
        return None


def select_property_name(property_value: dict) -> str:
    return (property_value.get("select") or {}).get("name") or ""


def formula_string(property_value: dict) -> str:
    formula = property_value.get("formula") or {}
    if formula.get("type") == "string":
        return formula.get("string") or ""
    return ""


def format_cash_flow_money(value: float | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def format_cash_flow_date(value: date | None) -> str:
    return "" if value is None else value.isoformat()


class DashboardService:
    def __init__(
        self,
        payment_settings: PaymentSettings,
        remit_settings: RemitSettings,
        dashboard_settings: DashboardSettings,
        shared_repository: SharedRecordRepository | None = None,
    ) -> None:
        self.payment_settings = payment_settings
        self.remit_settings = remit_settings
        self.dashboard_settings = dashboard_settings
        if shared_repository is not None:
            repository = shared_repository
        elif dashboard_settings.shared_database_path:
            repository = SQLiteSharedRecordRepository(dashboard_settings.shared_database_path)
            repository.initialize()
        else:
            repository = InMemorySharedRecordRepository()
        self.shared_data = ReadOnlyDashboardDataService(repository)
        self.review_actions = ReviewActionService(repository)
        self.review_csrf_token = secrets.token_urlsafe(32)

    def snapshot(self) -> dict:
        return {
            "payment": self.payment_snapshot(),
            "remit": self.remit_snapshot(),
            "cash_flow": self.cash_flow_snapshot(),
            "manager_checklist": self.manager_checklist_snapshot(),
            "operations": self.operations_snapshot(),
            "shared_dashboard": self.shared_data.summary(),
            "future_agents": [
                {"name": "Placement Agent", "status": "Planned", "priority": "High"},
                {"name": "Compliance Agent", "status": "Planned", "priority": "High"},
                {"name": "Finance Agent", "status": "Planned", "priority": "Medium"},
                {"name": "Executive Dashboard", "status": "Planned", "priority": "Medium"},
            ],
        }

    def operations_snapshot(self) -> dict:
        reports = self._operations_reports()
        if not reports:
            return {
                "status": "No Report",
                "has_report": False,
                "message": "No operations report available yet.",
                "latest": None,
                "card": {
                    "performance_score": "No report",
                    "collected_today": "Manual review",
                    "future_payments": "Manual review",
                    "pending_payments": "Manual review",
                    "calls": "Manual review",
                    "live_contacts": "Manual review",
                    "accounts_worked": "Manual review",
                    "takeaway": "No operations report available yet.",
                    "last_updated": "Not available",
                    "confidence": "No report",
                    "quality": "warn",
                },
                "detail": self._empty_operations_detail(),
            }

        latest = reports[-1]
        quality_passed = self._operations_quality_passed(latest)
        card = self._operations_card(latest, quality_passed)
        return {
            "status": "Ready" if quality_passed else "Manual Review",
            "has_report": True,
            "message": "",
            "latest": latest,
            "card": card,
            "detail": self._operations_detail(reports),
        }

    def manager_checklist_snapshot(self) -> dict:
        return {
            "status": "Ready" if self.dashboard_settings.manager_checklist_url else "Needs URL",
            "detail": "Daily checklist for C Solo. Reports and alerts send to Jaye.",
            "url": self.dashboard_settings.manager_checklist_url,
            "sheet_url": self.dashboard_settings.manager_checklist_sheet_url,
            "schedule": "Mon-Thu 5:00 PM, Fri 3:30 PM",
        }

    def payment_snapshot(self) -> dict:
        db = PaymentDatabase(self.payment_settings.database_path)
        today = today_in_timezone(self.payment_settings.timezone)
        try:
            db.initialize()
            rows = db.payments_for_local_date(today)
            recent = self._recent_payments(db.path)
        except sqlite3.Error as exc:
            return {
                "status": "Needs Attention",
                "today_count": 0,
                "today_total": "$0.00",
                "recent": [],
                "detail": f"Database error: {exc}",
            }

        total_cents = sum(row["payment_amount_cents"] for row in rows)
        return {
            "status": "Ready",
            "today_count": len(rows),
            "today_total": cents_to_currency(total_cents),
            "recent": recent,
            "detail": f"Last checked {self._local_time_label()}",
        }

    def remit_snapshot(self) -> dict:
        settings = self.remit_settings
        current_week_start = self._current_remit_week_start()
        latest_batch = self._latest_remit_batch(settings.database_path, settings.broker_name)
        if latest_batch and latest_batch["week_start"] == current_week_start:
            return {
                "status": "Sent",
                "broker": settings.broker_name,
                "incoming_folder": str(settings.incoming_folder),
                "detail": "Weekly remit has been sent and archived for this week.",
                "files": [latest_batch["remit_file_name"], latest_batch["liquidation_file_name"]],
                "last_sent": self._format_remit_sent(latest_batch),
                "send_deadline": f"{settings.run_day.title()} by {settings.send_deadline}",
            }

        try:
            files = find_required_remit_files(
                settings.incoming_folder,
                settings.remit_filename_contains,
                settings.liquidation_filename_contains,
                settings.allowed_extensions,
            )
            file_status = "Ready"
            detail = "Both ICR remit files are ready."
            filenames = [files.remit.name, files.liquidation.name]
        except RemitFileValidationError as exc:
            file_status = "Waiting"
            detail = str(exc)
            filenames = []

        last_sent = self._format_remit_sent(latest_batch) if latest_batch else "Never"
        return {
            "status": file_status,
            "broker": settings.broker_name,
            "incoming_folder": str(settings.incoming_folder),
            "detail": detail,
            "files": filenames,
            "last_sent": last_sent,
            "send_deadline": f"{settings.run_day.title()} by {settings.send_deadline}",
        }

    def scan_payments(self) -> ActionResult:
        try:
            count = PaymentAgent(self.payment_settings).scan_once()
        except Exception as exc:
            return ActionResult(False, f"Payment scan failed: {exc}")
        return ActionResult(True, f"Payment scan complete. Processed {count} new payment(s).")

    def send_weekly_remit(self) -> ActionResult:
        try:
            sent = WeeklyRemitAgent(self.remit_settings).scan_once()
        except Exception as exc:
            return ActionResult(False, f"Weekly remit failed: {exc}")
        if sent:
            return ActionResult(True, "Weekly remit sent and archived.")
        return ActionResult(False, "Weekly remit was not sent. Check files, deadline, or duplicate status.")

    def open_remit_folder(self) -> ActionResult:
        folder = self.remit_settings.incoming_folder
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["open", str(folder)], check=False)
        except Exception as exc:
            return ActionResult(False, f"Could not open folder: {exc}")
        return ActionResult(True, f"Opened {folder}.")

    def cash_flow_snapshot(self) -> dict:
        settings = load_cash_flow_settings()
        if not settings.notion_api_key:
            return empty_cash_flow_dashboard("Not configured")
        try:
            cash_flow = CashFlowHQService(settings)
            foundation = cash_flow.get_existing_foundation()
            pages: list[dict] = []
            cursor = ""
            while True:
                query = {"page_size": 100}
                if cursor:
                    query["start_cursor"] = cursor
                response = cash_flow.notion.request(
                    "POST",
                    f"/data_sources/{foundation['data_source_id']}/query",
                    json=query,
                )
                pages.extend(response.get("results", []))
                cursor = response.get("next_cursor") or ""
                if not response.get("has_more") or not cursor:
                    break
            rows = cash_flow_notion_rows(pages)
        except Exception as exc:
            return empty_cash_flow_dashboard(f"Unavailable: {exc}")
        return build_cash_flow_dashboard(rows, today_in_timezone(self.payment_settings.timezone))

    def _operations_reports(self) -> list[dict]:
        try:
            OperationsDatabase(self.payment_settings.database_path).initialize()
            with sqlite3.connect(self.payment_settings.database_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, report_date, screenshot_hash, screenshot_path, metrics_json,
                           collector_totals_json, missing_fields_json,
                           manual_review_notes_json, summary_text, posted_to_teams,
                           ocr_text, original_metrics_json, original_collector_totals_json,
                           original_ocr_text, manual_review, manually_edited_fields_json,
                           approved_at, last_reprocessed_at, classification_json,
                           is_operations_dashboard, excluded_reason, created_at, updated_at
                    FROM ops_reports
                    WHERE COALESCE(is_operations_dashboard, 1) = 1
                    ORDER BY report_date ASC, created_at ASC
                    """
                ).fetchall()
        except sqlite3.Error:
            return []
        return [self._operations_report_from_row(row) for row in rows]

    def _operations_report_from_row(self, row: sqlite3.Row) -> dict:
        metrics = self._json_value(row["metrics_json"], {})
        missing_fields = self._json_value(row["missing_fields_json"], [])
        manual_review_notes = self._json_value(row["manual_review_notes_json"], [])
        return {
            "id": row["id"],
            "report_date": row["report_date"],
            "screenshot_hash": row["screenshot_hash"],
            "screenshot_path": row["screenshot_path"],
            "metrics": metrics,
            "collector_totals": self._json_value(row["collector_totals_json"], []),
            "ocr_text": row["ocr_text"] or "",
            "original_metrics": self._json_value(row["original_metrics_json"], None),
            "original_collector_totals": self._json_value(row["original_collector_totals_json"], None),
            "original_ocr_text": row["original_ocr_text"] or "",
            "manual_review": None if row["manual_review"] is None else bool(row["manual_review"]),
            "manually_edited_fields": self._json_value(row["manually_edited_fields_json"], []),
            "approved_at": row["approved_at"],
            "last_reprocessed_at": row["last_reprocessed_at"],
            "classification": self._json_value(row["classification_json"], None),
            "is_operations_dashboard": None if row["is_operations_dashboard"] is None else bool(row["is_operations_dashboard"]),
            "excluded_reason": row["excluded_reason"] or "",
            "missing_fields": missing_fields,
            "manual_review_notes": manual_review_notes,
            "summary_text": row["summary_text"] or "",
            "posted_to_teams": bool(row["posted_to_teams"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "report_path": str(Path(os.getenv("OPS_REPORTS_DIR", "reports/operations-intelligence")) / f"{row['report_date']}.txt"),
        }

    def _operations_card(self, report: dict, quality_passed: bool) -> dict:
        if not quality_passed:
            return {
                "performance_score": "Manual review needed",
                "collected_today": "Manual review needed",
                "future_payments": "Manual review needed",
                "pending_payments": "Manual review needed",
                "calls": "Manual review needed",
                "live_contacts": "Manual review needed",
                "accounts_worked": "Manual review needed",
                "takeaway": "Manual review needed before dashboard metrics are shown.",
                "last_updated": self._format_timestamp(report.get("updated_at") or report.get("created_at")),
                "confidence": "Manual review",
                "quality": "warn",
            }
        return {
            "performance_score": self._performance_score(report),
            "collected_today": self._money(self._metric_sum(report, "posted_cash", "posted_fees", "green_cleared_cash")),
            "future_payments": self._money(self._metric_sum(report, "future_scheduled_cash", "future_scheduled_fees")),
            "pending_payments": self._money(self._metric_sum(report, "pending_cash", "pending_fees")),
            "calls": self._number(self._metric(report, "attempts")),
            "live_contacts": self._number(self._metric(report, "live_contacts")),
            "accounts_worked": self._number(self._metric(report, "accounts_worked")),
            "takeaway": self._first_ai_takeaway(report),
            "last_updated": self._format_timestamp(report.get("updated_at") or report.get("created_at")),
            "confidence": self._confidence_label(report),
            "quality": self._confidence_quality(report),
        }

    def _operations_detail(self, reports: list[dict]) -> dict:
        authoritative_reports = self._authoritative_reports(reports)
        latest = authoritative_reports[-1]
        passing_reports = [report for report in authoritative_reports if self._operations_quality_passed(report)]
        manual_review = [report for report in authoritative_reports if not self._operations_quality_passed(report)]
        return {
            "executive_kpis": self._operations_executive_kpis(latest),
            "latest_brief": latest["summary_text"] or "No daily brief saved yet.",
            "trend_7_day": self._trend_summary(passing_reports, days=7),
            "summary_30_day": self._history_summary_text(passing_reports[-30:]),
            "historical_trends": self._historical_trends(latest, passing_reports),
            "trend_cards": self._trend_cards(passing_reports[-30:]),
            "charts": self._operation_charts(authoritative_reports[-30:]),
            "executive_insights": self._executive_insights(authoritative_reports),
            "duplicate_audit": self._duplicate_audit(reports, authoritative_reports),
            "manual_review_queue": self._manual_review_queue(authoritative_reports),
            "historical_reports": list(reversed(authoritative_reports[-30:])),
            "manual_review_reports": list(reversed(manual_review[-30:])),
        }

    def _empty_operations_detail(self) -> dict:
        return {
            "executive_kpis": self._empty_executive_kpis(),
            "latest_brief": "No operations report available yet.",
            "trend_7_day": "No 7-day trend available yet.",
            "summary_30_day": "No 30-day summary available yet.",
            "historical_trends": self._empty_historical_trends(),
            "trend_cards": self._empty_trend_cards(),
            "charts": self._empty_charts(),
            "executive_insights": ["Not enough historical data yet."],
            "duplicate_audit": "No duplicate report dates found.",
            "manual_review_queue": [],
            "historical_reports": [],
            "manual_review_reports": [],
        }

    def _authoritative_reports(self, reports: list[dict]) -> list[dict]:
        by_date: dict[str, list[dict]] = defaultdict(list)
        for report in reports:
            by_date[report["report_date"]].append(report)
        selected = [self._authoritative_report_for_date(rows) for rows in by_date.values()]
        return sorted(selected, key=lambda report: (report["report_date"], report.get("created_at") or ""))

    def _authoritative_report_for_date(self, reports: list[dict]) -> dict:
        return max(
            reports,
            key=lambda report: (
                1 if self._operations_quality_passed(report) else 0,
                report.get("updated_at") or report.get("created_at") or "",
            ),
        )

    def _duplicate_audit(self, reports: list[dict], authoritative_reports: list[dict]) -> str:
        duplicate_dates = []
        by_date: dict[str, list[dict]] = defaultdict(list)
        for report in reports:
            by_date[report["report_date"]].append(report)
        authoritative_hashes = {report["screenshot_hash"] for report in authoritative_reports}
        for report_date, rows in sorted(by_date.items()):
            if len(rows) <= 1:
                continue
            quality_count = sum(1 for row in rows if self._operations_quality_passed(row))
            manual_count = len(rows) - quality_count
            selected = next((row for row in rows if row["screenshot_hash"] in authoritative_hashes), rows[-1])
            selected_status = "Ready" if self._operations_quality_passed(selected) else "Manual review"
            duplicate_dates.append(
                f"{report_date}: {len(rows)} records ({quality_count} ready, {manual_count} manual review); "
                f"displaying {selected_status.lower()} record."
            )
        if not duplicate_dates:
            return "No duplicate report dates found."
        return "Multiple records can come from debug, reprocess, local test, or failed OCR attempts. " + " ".join(duplicate_dates)

    def _manual_review_queue(self, authoritative_reports: list[dict]) -> list[dict]:
        queue = []
        for report in reversed(authoritative_reports):
            if report.get("is_operations_dashboard") is False:
                continue
            if self._operations_quality_passed(report):
                continue
            if not self._is_owner_facing_report(report):
                continue
            queue.append(
                {
                    "id": report["id"],
                    "report_date": report["report_date"],
                    "reason": self._review_reason(report),
                    "missing_fields": self._missing_fields_label(report),
                    "confidence": self._confidence_label(report),
                    "screenshot_hash": report["screenshot_hash"],
                }
            )
        return queue

    def _is_owner_facing_report(self, report: dict) -> bool:
        path = str(report.get("screenshot_path") or "").lower()
        name = Path(path).name.lower()
        if "/debug/" in path or "\\debug\\" in path:
            return False
        if "test" in name or name.startswith("local-"):
            return False
        return True

    def _review_reason(self, report: dict) -> str:
        missing = report.get("missing_fields") or []
        notes = report.get("manual_review_notes") or []
        quality_missing = self._quality_missing_fields(report)
        if quality_missing:
            return "Required fields missing"
        if missing:
            return "Fields need review"
        if notes:
            return notes[0]
        return "Manual review needed"

    def _missing_fields_label(self, report: dict) -> str:
        fields = self._quality_missing_fields(report) or report.get("missing_fields") or []
        return ", ".join(self._field_label(field) for field in fields[:8]) or "None listed"

    def _quality_missing_fields(self, report: dict) -> list[str]:
        missing = [
            field
            for field in ("accounts_worked", "attempts", "live_contacts", "contact_rate")
            if self._metric(report, field) is None
        ]
        if self._metric(report, "posted_cash") is None and self._metric(report, "future_scheduled_cash") is None:
            missing.append("posted_cash_or_future_scheduled_cash")
        return missing

    def _operations_quality_passed(self, report: dict) -> bool:
        if report.get("manual_review") is False and report.get("approved_at"):
            return True
        required = ("accounts_worked", "attempts", "live_contacts", "contact_rate")
        has_required = all(self._metric(report, field) is not None for field in required)
        has_money = self._metric(report, "posted_cash") is not None or self._metric(report, "future_scheduled_cash") is not None
        return has_required and has_money

    def operations_review_report(self, report_id: int) -> dict | None:
        reports = self._operations_reports()
        return next((report for report in reports if report["id"] == report_id), None)

    def save_operations_corrections(self, report_id: int, form: dict[str, str]) -> ActionResult:
        report = self.operations_review_report(report_id)
        if not report:
            return ActionResult(False, "Operations report was not found.")
        errors, updates, collector_totals, edited = self._validated_corrections(report, form)
        if errors:
            return ActionResult(False, " ".join(errors))
        metrics = json.loads(json.dumps(report.get("metrics", {})))
        for field, value in updates.items():
            current = metrics.get(field, {})
            metrics[field] = {
                "value": value,
                "raw_text": str(form.get(field, "")),
                "confidence": 1.0,
                "needs_review": False,
                "manually_edited": True,
            }
            if current.get("raw_text") and not metrics[field]["raw_text"]:
                metrics[field]["raw_text"] = current.get("raw_text")
        if collector_totals:
            edited.append("collector_totals")
        OperationsDatabase(self.payment_settings.database_path).save_manual_corrections(
            report_id,
            metrics,
            collector_totals or report.get("collector_totals", []),
            edited,
        )
        return ActionResult(True, "Manual corrections saved.")

    def approve_operations_report(self, report_id: int) -> ActionResult:
        if not self.operations_review_report(report_id):
            return ActionResult(False, "Operations report was not found.")
        OperationsDatabase(self.payment_settings.database_path).approve_report(report_id)
        return ActionResult(True, "Report approved. It will be used for dashboard trends without posting to Teams.")

    def reprocess_operations_report(self, report_id: int) -> ActionResult:
        report = self.operations_review_report(report_id)
        if not report:
            return ActionResult(False, "Operations report was not found.")
        screenshot_path = Path(report["screenshot_path"])
        if not screenshot_path.exists():
            return ActionResult(False, f"Screenshot is missing: {screenshot_path}")
        settings = load_operations_settings()
        extractor = ScreenshotOcrExtractor(
            settings.ocr_command,
            settings.ocr_min_confidence,
            settings.collector_codes,
            settings.ocr_debug,
            settings.reports_dir / "debug",
        )
        extracted = extractor.extract(screenshot_path, report["report_date"], report["screenshot_hash"])
        previous = OperationsDatabase(self.payment_settings.database_path).previous_report(
            extracted.report_date,
            extracted.screenshot_hash,
        )
        message = build_operations_message(extracted, previous)
        OperationsDatabase(self.payment_settings.database_path).replace_reprocessed_report(report_id, extracted, message.text)
        return ActionResult(True, "OCR reprocessed from the existing screenshot. Nothing was posted to Teams.")

    def _validated_corrections(self, report: dict, form: dict[str, str]) -> tuple[list[str], dict[str, float | int], list[dict], list[str]]:
        errors: list[str] = []
        updates: dict[str, float | int] = {}
        edited: list[str] = []
        money_fields = ("posted_cash", "future_scheduled_cash", "pending_cash")
        count_fields = ("attempts", "live_contacts", "accounts_worked")
        percent_fields = ("contact_rate", "close_rate")
        for field in money_fields:
            raw = form.get(field, "").strip()
            if not raw:
                continue
            value = self._parse_money(raw)
            if value is None:
                errors.append(f"{self._field_label(field)} must be a valid money amount.")
            else:
                updates[field] = value
                edited.append(field)
        for field in count_fields:
            raw = form.get(field, "").strip()
            if not raw:
                continue
            if not re.fullmatch(r"\d+", raw.replace(",", "")):
                errors.append(f"{self._field_label(field)} must be a whole number.")
            else:
                updates[field] = int(raw.replace(",", ""))
                edited.append(field)
        for field in percent_fields:
            raw = form.get(field, "").strip()
            if not raw:
                continue
            value = self._parse_percent(raw)
            if value is None:
                errors.append(f"{self._field_label(field)} must be a valid percentage.")
            else:
                updates[field] = value
                edited.append(field)
        collector_totals = self._parse_collector_totals(form, errors)
        return errors, updates, collector_totals, edited

    def _parse_collector_totals(self, form: dict[str, str], errors: list[str]) -> list[dict]:
        allowed = {item.strip().upper() for item in os.getenv("OPS_COLLECTOR_CODES", "CSOLO,VMAR,KMAD,UNITED HOUSE").split(",") if item.strip()}
        totals: list[dict] = []
        top_code = form.get("top_performer_code", "").strip().upper()
        top_total_raw = form.get("top_performer_total", "").strip()
        if top_code or top_total_raw:
            if allowed and top_code not in allowed:
                errors.append("Top Performer must match an allowed collector code.")
            total = self._parse_money(top_total_raw)
            if total is None:
                errors.append("Top Performer amount must be a valid money amount.")
            elif top_code:
                totals.append({"collector": top_code, "total": total, "source": "manual_review", "manually_edited": True})
        for line in form.get("collector_totals_text", "").splitlines():
            if not line.strip():
                continue
            match = re.match(r"^\s*([A-Za-z0-9 ]+?)\s*[-,:]\s*(.+?)\s*$", line)
            if not match:
                errors.append("Collector totals must use CODE - $amount format.")
                continue
            code = match.group(1).strip().upper()
            if allowed and code not in allowed:
                errors.append(f"{code} is not an allowed collector code.")
                continue
            total = self._parse_money(match.group(2))
            if total is None:
                errors.append(f"{code} total must be a valid money amount.")
                continue
            totals.append({"collector": code, "total": total, "source": "manual_review", "manually_edited": True})
        return totals

    def _parse_money(self, raw: str) -> float | None:
        cleaned = raw.strip().replace("$", "").replace(",", "")
        if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", cleaned):
            return None
        return round(float(cleaned), 2)

    def _parse_percent(self, raw: str) -> float | None:
        cleaned = raw.strip().replace("%", "")
        if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", cleaned):
            return None
        return round(float(cleaned), 2)

    def _field_label(self, field: str) -> str:
        labels = {
            "posted_cash": "Collected Today",
            "future_scheduled_cash": "Future Payments",
            "pending_cash": "Pending Payments",
            "attempts": "Calls",
            "live_contacts": "Live Contacts",
            "accounts_worked": "Accounts Worked",
            "contact_rate": "Contact Rate",
            "close_rate": "Close Rate",
            "posted_cash_or_future_scheduled_cash": "Posted Cash or Future Scheduled Cash",
        }
        return labels.get(field, field.replace("_", " ").title())

    def _trend_summary(self, reports: list[dict], *, days: int) -> str:
        if len(reports) < 2:
            return "No 7-day trend available yet."
        recent = reports[-days:]
        first = self._metric_sum(recent[0], "posted_cash", "posted_fees", "green_cleared_cash")
        last = self._metric_sum(recent[-1], "posted_cash", "posted_fees", "green_cleared_cash")
        calls = self._average(self._metric(report, "attempts") for report in recent)
        contacts = self._average(self._metric(report, "live_contacts") for report in recent)
        if first is None or last is None:
            movement = "Collections trend needs review"
        else:
            delta = last - first
            movement = "Collections are flat" if abs(delta) < 0.01 else f"Collections are {'up' if delta > 0 else 'down'} {self._money(abs(delta))}"
        return f"{movement}. Average calls: {self._number(calls)}. Average live contacts: {self._number(contacts)}."

    def _history_summary_text(self, reports: list[dict]) -> str:
        if not reports:
            return "No 30-day summary available yet."
        summary = build_historical_summary(reports)
        return (
            f"Total collected: {self._money(summary.total_collected)}. "
            f"Average daily collections: {self._money(summary.average_daily_collections)}. "
            f"Average calls: {self._number(summary.average_calls)}. "
            f"Reports passing quality gate: {summary.quality_passing_reports}."
        )

    def _historical_trends(self, latest: dict, passing_reports: list[dict]) -> dict:
        if not self._operations_quality_passed(latest):
            return self._empty_historical_trends()
        previous_reports = [
            report
            for report in passing_reports
            if report.get("screenshot_hash") != latest.get("screenshot_hash")
        ]
        context = build_historical_context(latest["report_date"], previous_reports)
        analysis = build_historical_trend_analysis(latest, context)
        summary = build_historical_summary(passing_reports[-30:])
        return {
            "average_7_day_collections": self._money(context.rolling_7_collections),
            "average_30_day_collections": self._money(context.rolling_30_collections),
            "best_collection_day": self._collection_day(summary.best_collection_day),
            "lowest_collection_day": self._collection_day(summary.lowest_collection_day),
            "same_weekday_average": self._money(context.same_weekday_collections),
            "collection_trend_vs_7_day": self._signed_money(analysis.collections_vs_7_day_average),
            "contact_rate_trend_vs_30_day": self._signed_percent(analysis.contact_rate_vs_30_day_average),
            "forecast": "Beta / Dashboard only",
        }

    def _operations_executive_kpis(self, report: dict) -> list[dict]:
        if not self._operations_quality_passed(report):
            return [
                {"label": "Today's Collections", "value": "Manual review", "tone": "warn"},
                {"label": "Performance Score", "value": "Manual review", "tone": "warn"},
                {"label": "Future Payments", "value": "Manual review", "tone": "warn"},
                {"label": "Live Contacts", "value": "Manual review", "tone": "warn"},
                {"label": "AI Confidence", "value": "Manual review", "tone": "warn"},
            ]
        return [
            {
                "label": "Today's Collections",
                "value": self._money(self._metric_sum(report, "posted_cash", "posted_fees", "green_cleared_cash")),
                "tone": "ready",
            },
            {"label": "Performance Score", "value": self._performance_score(report), "tone": "neutral"},
            {
                "label": "Future Payments",
                "value": self._money(self._metric_sum(report, "future_scheduled_cash", "future_scheduled_fees")),
                "tone": "neutral",
            },
            {"label": "Live Contacts", "value": self._number(self._metric(report, "live_contacts")), "tone": "neutral"},
            {"label": "AI Confidence", "value": self._confidence_label(report), "tone": self._confidence_quality(report)},
        ]

    def _empty_executive_kpis(self) -> list[dict]:
        return [
            {"label": "Today's Collections", "value": "No report", "tone": "warn"},
            {"label": "Performance Score", "value": "No report", "tone": "warn"},
            {"label": "Future Payments", "value": "No report", "tone": "warn"},
            {"label": "Live Contacts", "value": "No report", "tone": "warn"},
            {"label": "AI Confidence", "value": "No report", "tone": "warn"},
        ]

    def _trend_cards(self, reports: list[dict]) -> list[dict]:
        if not reports:
            return self._empty_trend_cards()
        summary = build_historical_summary(reports)
        return [
            {"label": "7-day avg collections", "value": self._money(self._average_collection(reports[-7:]))},
            {"label": "30-day avg collections", "value": self._money(summary.average_daily_collections)},
            {"label": "Best collection day", "value": self._collection_day(summary.best_collection_day)},
            {"label": "Lowest collection day", "value": self._collection_day(summary.lowest_collection_day)},
            {"label": "Reports passing quality gate", "value": self._number(summary.quality_passing_reports)},
            {"label": "Forecast confidence", "value": "Beta / Dashboard only"},
        ]

    def _empty_trend_cards(self) -> list[dict]:
        return [
            {"label": "7-day avg collections", "value": "Manual review"},
            {"label": "30-day avg collections", "value": "Manual review"},
            {"label": "Best collection day", "value": "Manual review"},
            {"label": "Lowest collection day", "value": "Manual review"},
            {"label": "Reports passing quality gate", "value": "0"},
            {"label": "Forecast confidence", "value": "Beta / Dashboard only"},
        ]

    def _operation_charts(self, reports: list[dict]) -> dict:
        return {
            "collections": self._chart_series(reports, "collections"),
            "performance_score": self._chart_series(reports, "performance_score"),
            "contact_rate": self._chart_series(reports, "contact_rate"),
            "calls_vs_collections": [
                {
                    "label": report["report_date"],
                    "x": self._metric(report, "attempts"),
                    "y": self._collection_total(report),
                }
                for report in reports
                if self._operations_quality_passed(report)
            ],
        }

    def _empty_charts(self) -> dict:
        return {
            "collections": [],
            "performance_score": [],
            "contact_rate": [],
            "calls_vs_collections": [],
        }

    def _chart_series(self, reports: list[dict], metric: str) -> list[dict]:
        values = []
        for report in reports:
            if not self._operations_quality_passed(report):
                continue
            if metric == "collections":
                value = self._collection_total(report)
            elif metric == "performance_score":
                value = self._performance_score_number(report)
            elif metric == "contact_rate":
                value = self._metric(report, "contact_rate")
            else:
                value = None
            if isinstance(value, (int, float)):
                values.append({"label": report["report_date"], "value": round(float(value), 2)})
        return values

    def _executive_insights(self, reports: list[dict]) -> list[str]:
        passing = [report for report in reports if self._operations_quality_passed(report)]
        if len(passing) < 3:
            return ["Not enough historical data yet."]
        latest = passing[-1]
        insights: list[str] = []
        latest_collections = self._collection_total(latest)
        avg_30 = self._average_collection(passing[-30:])
        if isinstance(latest_collections, (int, float)) and isinstance(avg_30, (int, float)):
            direction = "above" if latest_collections >= avg_30 else "below"
            insights.append(f"Collections are {direction} the 30-day average.")
        latest_future = self._metric_sum(latest, "future_scheduled_cash", "future_scheduled_fees")
        prior_future_avg = self._average(
            self._metric_sum(report, "future_scheduled_cash", "future_scheduled_fees")
            for report in passing[-8:-1]
        )
        if isinstance(latest_future, (int, float)) and isinstance(prior_future_avg, (int, float)):
            direction = "stronger than" if latest_future >= prior_future_avg else "lighter than"
            insights.append(f"Future payments are {direction} the recent average.")
        recent_contact = [self._metric(report, "contact_rate") for report in passing[-3:]]
        if len(recent_contact) == 3 and all(isinstance(value, (int, float)) for value in recent_contact):
            if recent_contact[-1] < recent_contact[0]:
                insights.append("Contact rate has declined over the last few reports.")
            elif recent_contact[-1] > recent_contact[0]:
                insights.append("Contact rate has improved over the last few reports.")
        strongest_weekday = self._strongest_weekday(passing)
        if strongest_weekday:
            insights.append(f"{strongest_weekday} has been the strongest collection day.")
        return insights[:5] or ["Not enough historical data yet."]

    def _empty_historical_trends(self) -> dict:
        return {
            "average_7_day_collections": "Manual review",
            "average_30_day_collections": "Manual review",
            "best_collection_day": "Manual review",
            "lowest_collection_day": "Manual review",
            "same_weekday_average": "Manual review",
            "collection_trend_vs_7_day": "Manual review",
            "contact_rate_trend_vs_30_day": "Manual review",
            "forecast": "Beta / Dashboard only",
        }

    def _performance_score(self, report: dict) -> str:
        summary = report.get("summary_text") or ""
        for line in summary.splitlines():
            if " / 100" in line:
                return line.strip()
        confidence = self._confidence(report)
        return f"{round(confidence * 100)} / 100"

    def _performance_score_number(self, report: dict) -> float | None:
        text = self._performance_score(report)
        try:
            return float(text.split("/", 1)[0].strip())
        except (ValueError, IndexError):
            return None

    def _first_ai_takeaway(self, report: dict) -> str:
        lines = [line.strip() for line in (report.get("summary_text") or "").splitlines()]
        for index, line in enumerate(lines):
            if "AI Insights" in line:
                for insight in lines[index + 1:]:
                    if insight and not insight.startswith("━"):
                        return insight
        return "Latest operations report is ready."

    def _confidence_label(self, report: dict) -> str:
        confidence = self._confidence(report)
        if confidence <= 0:
            return "Manual review"
        return f"{round(confidence * 100)}%"

    def _confidence_quality(self, report: dict) -> str:
        confidence = self._confidence(report)
        if confidence >= 0.9:
            return "ready"
        if confidence >= 0.72:
            return "neutral"
        return "warn"

    def _confidence(self, report: dict) -> float:
        values = [
            metric.get("confidence")
            for metric in report.get("metrics", {}).values()
            if metric.get("value") is not None and isinstance(metric.get("confidence"), (int, float))
        ]
        if not values:
            return 0.0
        return sum(float(value) for value in values) / len(values)

    def _metric_sum(self, report: dict, *fields: str) -> float | None:
        values = [self._metric(report, field) for field in fields]
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        if not numeric:
            return None
        return round(sum(numeric), 2)

    def _collection_total(self, report: dict) -> float | None:
        return self._metric_sum(report, "posted_cash", "posted_fees", "green_cleared_cash")

    def _average_collection(self, reports: list[dict]) -> float | None:
        return self._average(self._collection_total(report) for report in reports)

    def _strongest_weekday(self, reports: list[dict]) -> str:
        totals: dict[int, list[float]] = defaultdict(list)
        for report in reports:
            total = self._collection_total(report)
            if not isinstance(total, (int, float)):
                continue
            try:
                weekday = datetime.fromisoformat(report["report_date"]).weekday()
            except ValueError:
                continue
            totals[weekday].append(float(total))
        if not totals:
            return ""
        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        best = max(totals.items(), key=lambda item: sum(item[1]) / len(item[1]))
        return names[best[0]]

    def _metric(self, report: dict, field: str) -> float | int | None:
        value = report.get("metrics", {}).get(field, {}).get("value")
        return value if isinstance(value, (int, float)) else None

    def _json_value(self, value: str, default):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    def _money(self, value: float | int | None) -> str:
        if not isinstance(value, (int, float)):
            return "Manual review"
        return f"${value:,.2f}"

    def _signed_money(self, value: float | int | None) -> str:
        if not isinstance(value, (int, float)):
            return "Manual review"
        if abs(value) < 0.01:
            return "Flat"
        prefix = "+" if value > 0 else "-"
        return f"{prefix}{self._money(abs(value))}"

    def _signed_percent(self, value: float | int | None) -> str:
        if not isinstance(value, (int, float)):
            return "Manual review"
        if abs(value) < 0.01:
            return "Flat"
        prefix = "+" if value > 0 else "-"
        return f"{prefix}{abs(value):.2f}%"

    def _collection_day(self, value: tuple[str, float] | None) -> str:
        if not value:
            return "Manual review"
        return f"{value[0]} ({self._money(value[1])})"

    def _number(self, value: float | int | None) -> str:
        if not isinstance(value, (int, float)):
            return "Manual review"
        if isinstance(value, float) and not value.is_integer():
            return f"{value:,.1f}"
        return f"{int(value):,}"

    def _average(self, values) -> float | None:
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        if not numeric:
            return None
        return round(sum(numeric) / len(numeric), 1)

    def _format_timestamp(self, value: str | None) -> str:
        if not value:
            return "Not available"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        local = parsed.astimezone(ZoneInfo(self.payment_settings.timezone))
        return local.strftime("%Y-%m-%d %I:%M %p")

    def _recent_payments(self, database_path: Path) -> list[dict]:
        with sqlite3.connect(database_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT p.account_number, p.payment_amount_cents, p.payment_type, p.payment_date, p.created_at
                FROM payments p
                LEFT JOIN processed_emails e ON e.message_id = p.message_id
                WHERE p.id IN (
                    SELECT MIN(p2.id)
                    FROM payments p2
                    LEFT JOIN processed_emails e2 ON e2.message_id = p2.message_id
                    GROUP BY COALESCE(
                        e2.internet_message_id,
                        p2.account_number || '|' || p2.payment_amount_cents || '|' ||
                        COALESCE(p2.payment_date, '') || '|' || COALESCE(p2.payment_type, '')
                    )
                )
                ORDER BY p.created_at DESC
                LIMIT 5
                """
            ).fetchall()
        return [
            {
                "account": row["account_number"],
                "amount": cents_to_currency(row["payment_amount_cents"]),
                "type": row["payment_type"] or "",
                "date": row["payment_date"] or "",
            }
            for row in rows
        ]

    def _last_remit_sent(self, database_path: Path, broker_name: str) -> str:
        latest_batch = self._latest_remit_batch(database_path, broker_name)
        return self._format_remit_sent(latest_batch) if latest_batch else "Never"

    def _latest_remit_batch(self, database_path: Path, broker_name: str) -> dict | None:
        try:
            RemitDatabase(database_path).initialize()
            with sqlite3.connect(database_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT sent_date, week_start, remit_file_name, liquidation_file_name
                    FROM remit_batches
                    WHERE lower(broker_name) = lower(?)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (broker_name,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return dict(row) if row else None

    def _format_remit_sent(self, batch: dict | None) -> str:
        if not batch:
            return "Never"
        return f"{batch['sent_date']} for week {batch['week_start']}"

    def _current_remit_week_start(self) -> str:
        timezone = getattr(self.remit_settings, "timezone", self.payment_settings.timezone)
        now = datetime.now(ZoneInfo(timezone))
        monday = now.date() - timedelta(days=now.weekday())
        return monday.isoformat()

    def _local_time_label(self) -> str:
        now = datetime.now(ZoneInfo(self.payment_settings.timezone))
        return now.strftime("%Y-%m-%d %I:%M %p")
