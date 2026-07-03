from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any


MONEY_TOTAL_FIELDS = ("posted_cash", "posted_fees")
FUTURE_TOTAL_FIELDS = ("future_scheduled_cash", "future_scheduled_fees")


@dataclass(frozen=True)
class HistoricalSummary:
    total_collected: float | None
    average_daily_collections: float | None
    average_calls: float | None
    average_live_contacts: float | None
    average_contact_rate: float | None
    average_close_rate: float | None
    best_collection_day: tuple[str, float] | None
    lowest_collection_day: tuple[str, float] | None
    top_collector: tuple[str, float] | None
    quality_passing_reports: int
    reports_count: int


@dataclass(frozen=True)
class HistoricalContext:
    rolling_7_collections: float | None = None
    rolling_30_collections: float | None = None
    rolling_7_attempts: float | None = None
    rolling_30_attempts: float | None = None
    rolling_7_live_contacts: float | None = None
    rolling_30_live_contacts: float | None = None
    rolling_7_contact_rate: float | None = None
    rolling_30_contact_rate: float | None = None
    same_weekday_collections: float | None = None
    same_weekday_attempts: float | None = None
    same_weekday_live_contacts: float | None = None
    same_weekday_contact_rate: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return self.__dict__.copy()


def build_historical_summary(reports: list[dict[str, Any]]) -> HistoricalSummary:
    readable = [report for report in reports if _passes_quality_gate(report)]
    collected_by_day = [
        (report["report_date"], total)
        for report in readable
        if (total := _metric_sum(report, *MONEY_TOTAL_FIELDS)) is not None
    ]
    collector_totals: dict[str, float] = {}
    for report in readable:
        for row in report.get("collector_totals", []) or []:
            if row.get("source") != "whiteboard":
                continue
            collector = str(row.get("collector") or "")
            total = row.get("total")
            if collector and isinstance(total, (int, float)):
                collector_totals[collector] = collector_totals.get(collector, 0.0) + float(total)

    return HistoricalSummary(
        total_collected=_sum_values([total for _, total in collected_by_day]),
        average_daily_collections=_average([total for _, total in collected_by_day]),
        average_calls=_average(_metric(report, "attempts") for report in readable),
        average_live_contacts=_average(_metric(report, "live_contacts") for report in readable),
        average_contact_rate=_average(_metric(report, "contact_rate") for report in readable),
        average_close_rate=_average(_metric(report, "close_rate") for report in readable),
        best_collection_day=max(collected_by_day, key=lambda item: item[1]) if collected_by_day else None,
        lowest_collection_day=min(collected_by_day, key=lambda item: item[1]) if collected_by_day else None,
        top_collector=max(collector_totals.items(), key=lambda item: item[1]) if len(collector_totals) >= 2 else None,
        quality_passing_reports=len(readable),
        reports_count=len(reports),
    )


def build_historical_context(report_date: str, previous_reports: list[dict[str, Any]]) -> HistoricalContext:
    parsed_date = _parse_date(report_date)
    same_weekday = []
    if parsed_date:
        same_weekday = [
            report
            for report in previous_reports
            if (report_day := _parse_date(report["report_date"])) and report_day.weekday() == parsed_date.weekday()
        ]

    last_7 = _reports_in_window(report_date, previous_reports, days=7)
    last_30 = _reports_in_window(report_date, previous_reports, days=30)
    return HistoricalContext(
        rolling_7_collections=_average(_collection_total(report) for report in last_7),
        rolling_30_collections=_average(_collection_total(report) for report in last_30),
        rolling_7_attempts=_average(_metric(report, "attempts") for report in last_7),
        rolling_30_attempts=_average(_metric(report, "attempts") for report in last_30),
        rolling_7_live_contacts=_average(_metric(report, "live_contacts") for report in last_7),
        rolling_30_live_contacts=_average(_metric(report, "live_contacts") for report in last_30),
        rolling_7_contact_rate=_average(_metric(report, "contact_rate") for report in last_7),
        rolling_30_contact_rate=_average(_metric(report, "contact_rate") for report in last_30),
        same_weekday_collections=_average(_collection_total(report) for report in same_weekday),
        same_weekday_attempts=_average(_metric(report, "attempts") for report in same_weekday),
        same_weekday_live_contacts=_average(_metric(report, "live_contacts") for report in same_weekday),
        same_weekday_contact_rate=_average(_metric(report, "contact_rate") for report in same_weekday),
    )


def format_historical_summary(summary: HistoricalSummary) -> str:
    lines = [
        "Historical Summary",
        f"- Total collected: {_money(summary.total_collected)}",
        f"- Average daily collections: {_money(summary.average_daily_collections)}",
        f"- Average calls: {_number(summary.average_calls)}",
        f"- Average live contacts: {_number(summary.average_live_contacts)}",
        f"- Average contact rate: {_percent(summary.average_contact_rate)}",
        f"- Average close rate: {_percent(summary.average_close_rate)}",
        f"- Best collection day: {_day_money(summary.best_collection_day)}",
        f"- Lowest collection day: {_day_money(summary.lowest_collection_day)}",
        f"- Top collector: {_collector(summary.top_collector)}",
        f"- Reports passing quality gate: {summary.quality_passing_reports}",
    ]
    return "\n".join(lines)


def _reports_in_window(report_date: str, reports: list[dict[str, Any]], *, days: int) -> list[dict[str, Any]]:
    parsed_date = _parse_date(report_date)
    if not parsed_date:
        return [report for report in reports if _passes_quality_gate(report)][-days:]
    start = parsed_date - timedelta(days=days)
    return [
        report
        for report in reports
        if _passes_quality_gate(report)
        and (report_day := _parse_date(report["report_date"]))
        and start <= report_day < parsed_date
    ]


def _passes_quality_gate(report: dict[str, Any]) -> bool:
    metrics = report.get("metrics", {})
    required = ("accounts_worked", "attempts", "live_contacts", "contact_rate")
    has_required = all(_metric(report, field) is not None for field in required)
    has_money = _metric(report, "posted_cash") is not None or _metric(report, "future_scheduled_cash") is not None
    return has_required and has_money


def _collection_total(report: dict[str, Any]) -> float | None:
    return _metric_sum(report, *MONEY_TOTAL_FIELDS)


def _metric_sum(report: dict[str, Any], *fields: str) -> float | None:
    values = [_metric(report, field) for field in fields]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return round(sum(numeric), 2)


def _metric(report: dict[str, Any], field: str) -> float | int | None:
    value = report.get("metrics", {}).get(field, {}).get("value")
    return value if isinstance(value, (int, float)) else None


def _average(values: Any) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 2)


def _sum_values(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values), 2)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _money(value: float | None) -> str:
    return "Manual review" if value is None else f"${value:,.2f}"


def _number(value: float | None) -> str:
    return "Manual review" if value is None else f"{value:,.0f}"


def _percent(value: float | None) -> str:
    return "Manual review" if value is None else f"{value:.2f}%"


def _day_money(value: tuple[str, float] | None) -> str:
    if not value:
        return "Manual review"
    return f"{value[0]} ({_money(value[1])})"


def _collector(value: tuple[str, float] | None) -> str:
    if not value:
        return "Manual review"
    return f"{value[0]} ({_money(value[1])})"
