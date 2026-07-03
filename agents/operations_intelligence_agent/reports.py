from __future__ import annotations

import html
import re
from datetime import date, datetime
from typing import Any

from shared.integrations.microsoft_teams import TeamsMessage

from .models import ExtractedReport


DISPLAY_NAMES = {
    "accounts_worked": "Accounts Worked",
    "attempts": "Attempts",
    "live_contacts": "Live Contacts",
    "contact_rate": "Contact Rate",
    "close_rate": "Close Rate",
    "posted_cash": "Posted Cash",
    "posted_fees": "Posted Fees",
    "pending_cash": "Pending Cash",
    "pending_fees": "Pending Fees",
    "future_scheduled_cash": "Future Scheduled Cash",
    "future_scheduled_fees": "Future Scheduled Fees",
}


def build_operations_message(
    report: ExtractedReport,
    previous: dict[str, Any] | None,
    *,
    history: dict[str, Any] | None = None,
) -> TeamsMessage:
    text = build_executive_brief(report, previous, history=history)
    html_text = html.escape(text).replace("\n", "<br>")
    title = "UCM Daily Operations Brief"
    return TeamsMessage(title=title, text=text, html=f"<p>{html_text}</p>")


def build_manual_review_message(report: ExtractedReport) -> TeamsMessage:
    text = build_executive_brief(report, None)
    html_text = html.escape(text).replace("\n", "<br>")
    title = "UCM Daily Operations Brief - Manual Review Required"
    return TeamsMessage(title=title, text=text, html=f"<p>{html_text}</p>")


def build_executive_brief(
    report: ExtractedReport,
    previous: dict[str, Any] | None,
    *,
    history: dict[str, Any] | None = None,
) -> str:
    collected = _sum_values(report, "posted_cash", "posted_fees", "green_cleared_cash")
    pending = _sum_values(report, "pending_cash", "pending_fees")
    future = _sum_values(report, "future_scheduled_cash", "future_scheduled_fees")
    score, score_label = _performance_score(report, previous, collected, future, history=history)
    confidence_label, confidence_note = _confidence_summary(report)
    attention = _attention_line(report)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📊 UCM Daily Operations Brief",
        _long_date(report.report_date),
        "🟢  Performance Score" if report.passes_quality_gate else "🔴  Performance Score",
        f"{score} / 100 — {score_label}",
        _performance_sentence(report, previous, collected, future),
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💰 Money",
        f"💵 Collected Today: {_money(collected)}",
        f"🟢 Future Payments: {_money(future)}",
        f"🟡 Pending Payments: {_money(pending)}",
        "📞 Activity",
        f"☎️ Calls: {_metric(report.metrics, 'attempts')} | 👥 Live Contacts: {_metric(report.metrics, 'live_contacts')}",
        f"📂 Accounts Worked: {_metric(report.metrics, 'accounts_worked')}",
        "📈 Performance",
        f"✅ Contact Rate: {_percent_metric(report.metrics, 'contact_rate')} | 🔥 Close Rate: {_percent_metric(report.metrics, 'close_rate')}",
        f"🏆 Top Performer: {_top_collector(report)}",
        "🧠 AI Insights",
    ]
    lines.extend(_executive_insights(report, previous))
    lines.extend(
        [
            "🚩 Needs Attention",
            attention,
            "🤖 AI Confidence",
            confidence_label,
            confidence_note,
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
    )
    return "\n".join(lines)


def build_summary_text(report: ExtractedReport, previous: dict[str, Any] | None) -> str:
    metrics = report.metrics
    total_collected = _sum_values(report, "posted_cash", "posted_fees", "green_cleared_cash")
    pending = _sum_values(report, "pending_cash", "pending_fees")
    future = _sum_values(report, "future_scheduled_cash", "future_scheduled_fees")
    top_collector = _top_collector(report)
    missing = ", ".join(_display(field) for field in report.missing_fields) or "None"
    review_needed = "Yes" if report.needs_manual_review else "No"

    lines = [
        f"UCM Daily Operations Detail - {report.report_date}",
        "",
        "Today's Snapshot:",
        f"- Total Collected: {_money(total_collected)}",
        f"- Pending Payments: {_money(pending)}",
        f"- Future Scheduled: {_money(future)}",
        f"- Accounts Worked: {_metric(metrics, 'accounts_worked')}",
        f"- Attempts: {_metric(metrics, 'attempts')}",
        f"- Live Contacts: {_metric(metrics, 'live_contacts')}",
        f"- Contact Rate: {_percent_metric(metrics, 'contact_rate')}",
        f"- Close Rate: {_percent_metric(metrics, 'close_rate')}",
        f"- Top Collector: {top_collector}",
        "",
        "What Matters:",
    ]
    lines.extend(f"- {item}" for item in _insights(report, previous))
    lines.extend(
        [
            "",
            "Trend vs Previous Report:",
            f"- Collections: {_trend(total_collected, _previous_sum(previous, 'posted_cash', 'posted_fees', 'green_cleared_cash'))}",
            f"- Attempts: {_trend(report.metric_value('attempts'), _previous_value(previous, 'attempts'), integer=True)}",
            f"- Live Contacts: {_trend(report.metric_value('live_contacts'), _previous_value(previous, 'live_contacts'), integer=True)}",
            f"- Future Payments: {_trend(future, _previous_sum(previous, 'future_scheduled_cash', 'future_scheduled_fees'))}",
            "",
            "Data Quality:",
            f"- Completeness score: {_score(report.completeness_score)}",
            f"- Confidence score: {_score(report.confidence_score)}",
            f"- Quality gate passed: {'Yes' if report.passes_quality_gate else 'No'}",
            f"- Missing fields: {missing}",
            f"- Manual review needed: {review_needed}",
            f"- Screenshot path: {report.screenshot_path}",
        ]
    )
    if report.manual_review_notes:
        lines.extend(f"- {note}" for note in report.manual_review_notes[:5])
    return "\n".join(lines)


def build_detailed_report_text(report: ExtractedReport, previous: dict[str, Any] | None) -> str:
    return build_summary_text(report, previous)


def _insights(report: ExtractedReport, previous: dict[str, Any] | None) -> list[str]:
    insights: list[str] = []
    collected = _sum_values(report, "posted_cash", "posted_fees", "green_cleared_cash")
    future = _sum_values(report, "future_scheduled_cash", "future_scheduled_fees")
    attempts = report.metric_value("attempts")
    live_contacts = report.metric_value("live_contacts")
    contact_rate = report.metric_value("contact_rate")

    previous_collected = _previous_sum(previous, "posted_cash", "posted_fees", "green_cleared_cash")
    if collected is not None and previous_collected is not None:
        direction = "up" if collected >= previous_collected else "down"
        insights.append(f"Collections are {direction} versus the previous readable report.")
    if attempts and live_contacts:
        insights.append(f"The team produced {_number(live_contacts)} live contacts from {_number(attempts)} attempts.")
    if contact_rate is not None:
        insights.append(f"Contact rate finished at {_percent(contact_rate)}, which is the key conversion signal to watch tomorrow.")
    if future:
        insights.append(f"Future scheduled payments add {_money(future)} to the near-term pipeline.")
    if not insights:
        insights.append("The screenshot needs manual review before reliable performance insights can be stated.")
    return insights[:3]


def _executive_insights(report: ExtractedReport, previous: dict[str, Any] | None) -> list[str]:
    if not report.passes_quality_gate:
        return ["🔴 OCR quality is too low for executive interpretation."]

    insights: list[str] = []
    collected = _sum_values(report, "posted_cash", "posted_fees", "green_cleared_cash")
    future = _sum_values(report, "future_scheduled_cash", "future_scheduled_fees")
    contact_rate = report.metric_value("contact_rate")
    previous_collected = _previous_sum(previous, "posted_cash", "posted_fees", "green_cleared_cash")
    previous_future = _previous_sum(previous, "future_scheduled_cash", "future_scheduled_fees")
    previous_contact = _previous_value(previous, "contact_rate")

    if isinstance(collected, (int, float)) and isinstance(previous_collected, (int, float)):
        direction = "above" if collected >= previous_collected else "below"
        insights.append(f"{'✅' if collected >= previous_collected else '⚠️'} Collections finished {direction} the previous report.")
    elif isinstance(collected, (int, float)):
        insights.append("✅ Collections were readable and ready for review.")

    if isinstance(contact_rate, (int, float)) and isinstance(previous_contact, (int, float)):
        direction = "improved" if contact_rate >= previous_contact else "declined"
        insights.append(f"{'✅' if contact_rate >= previous_contact else '⚠️'} Contact rate {direction} versus the previous report.")
    elif isinstance(contact_rate, (int, float)):
        insights.append("💡 Contact rate is available for daily conversion tracking.")

    if isinstance(future, (int, float)) and isinstance(previous_future, (int, float)):
        direction = "strong" if future >= previous_future else "lighter"
        insights.append(f"{'💡' if future >= previous_future else '⚠️'} Tomorrow's payment pipeline looks {direction}.")
    elif isinstance(future, (int, float)):
        insights.append("💡 Tomorrow's payment pipeline remains visible.")

    return insights[:3] or ["✅ No unusual movement detected."]


def _top_collector(report: ExtractedReport) -> str:
    if len(report.collector_totals) < 2:
        return "Manual review"
    valid_totals = [
        row
        for row in report.collector_totals
        if _valid_collector_name(str(row.get("collector") or ""))
        and str(row.get("source") or "") == "whiteboard"
    ]
    if len(valid_totals) < 2:
        return "Manual review"
    top = max(valid_totals, key=lambda row: float(row.get("total") or 0))
    return f"{top.get('collector')} ({_money(float(top.get('total') or 0))})"


def _sum_values(report: ExtractedReport, *fields: str) -> float | None:
    values = [report.metric_value(field) for field in fields]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return round(sum(numeric), 2)


def _previous_sum(previous: dict[str, Any] | None, *fields: str) -> float | None:
    if not previous:
        return None
    values = [_previous_value(previous, field) for field in fields]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return None
    return round(sum(numeric), 2)


def _previous_value(previous: dict[str, Any] | None, field: str) -> float | int | None:
    if not previous:
        return None
    value = previous.get("metrics", {}).get(field, {}).get("value")
    return value if isinstance(value, (int, float)) else None


def _metric(metrics: dict[str, Any], field: str) -> str:
    metric = metrics.get(field)
    if not metric or metric.value is None:
        return "Manual review"
    return _number(metric.value)


def _percent_metric(metrics: dict[str, Any], field: str) -> str:
    metric = metrics.get(field)
    if not metric or metric.value is None:
        return "Manual review"
    return _percent(metric.value)


def _trend(current: Any, previous: Any, *, integer: bool = False) -> str:
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return "Manual review"
    delta = current - previous
    if abs(delta) < 0.005:
        return "Flat"
    direction = "Up" if delta > 0 else "Down"
    amount = _number(abs(delta)) if integer else _money(abs(delta))
    return f"{direction} {amount}"


def _money(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "Manual review"
    return f"${value:,.2f}"


def _number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "Manual review"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def _percent(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "Manual review"
    return f"{value:.2f}%"


def _display(field: str) -> str:
    if field == "posted_cash_or_future_scheduled_cash":
        return "Posted Cash or Future Scheduled Cash"
    return DISPLAY_NAMES.get(field, field.replace("_", " ").title())


def _score(value: float) -> str:
    return f"{value:.0%}"


def _long_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%A, %B %-d, %Y")


def _performance_score(
    report: ExtractedReport,
    previous: dict[str, Any] | None,
    collected: float | None,
    future: float | None,
    *,
    history: dict[str, Any] | None = None,
) -> tuple[int, str]:
    if not report.passes_quality_gate:
        return 0, "MANUAL REVIEW REQUIRED"
    score = 75
    previous_collected = _previous_sum(previous, "posted_cash", "posted_fees", "green_cleared_cash")
    previous_future = _previous_sum(previous, "future_scheduled_cash", "future_scheduled_fees")
    if isinstance(collected, (int, float)) and isinstance(previous_collected, (int, float)) and collected >= previous_collected:
        score += 7
    if isinstance(future, (int, float)) and isinstance(previous_future, (int, float)) and future >= previous_future:
        score += 5
    rolling_7_collections = _history_value(history, "rolling_7_collections")
    rolling_30_collections = _history_value(history, "rolling_30_collections")
    same_weekday_collections = _history_value(history, "same_weekday_collections")
    if isinstance(collected, (int, float)) and isinstance(rolling_7_collections, (int, float)):
        score += 4 if collected >= rolling_7_collections else -4
    if isinstance(collected, (int, float)) and isinstance(rolling_30_collections, (int, float)):
        score += 3 if collected >= rolling_30_collections else -3
    if isinstance(collected, (int, float)) and isinstance(same_weekday_collections, (int, float)):
        score += 3 if collected >= same_weekday_collections else -3
    if report.metric_value("contact_rate") is not None:
        score += 3
    if report.metric_value("close_rate") is not None:
        score += 3
    score += min(7, round(report.completeness_score * 7))
    score = max(0, min(100, score))
    if score >= 90:
        label = "STRONG DAY"
    elif score >= 80:
        label = "GOOD DAY"
    elif score >= 70:
        label = "STEADY DAY"
    else:
        label = "NEEDS ATTENTION"
    return score, label


def _history_value(history: dict[str, Any] | None, field: str) -> float | None:
    if not history:
        return None
    value = history.get(field)
    return value if isinstance(value, (int, float)) else None


def _performance_sentence(
    report: ExtractedReport,
    previous: dict[str, Any] | None,
    collected: float | None,
    future: float | None,
) -> str:
    if not report.passes_quality_gate:
        return "OCR did not meet the quality gate, so this report needs manual review."
    previous_collected = _previous_sum(previous, "posted_cash", "posted_fees", "green_cleared_cash")
    previous_future = _previous_sum(previous, "future_scheduled_cash", "future_scheduled_fees")
    collected_text = "Collections were readable"
    future_text = "the payment pipeline is visible"
    if isinstance(collected, (int, float)) and isinstance(previous_collected, (int, float)):
        collected_text = "Collections exceeded the previous report" if collected >= previous_collected else "Collections trailed the previous report"
    if isinstance(future, (int, float)) and isinstance(previous_future, (int, float)):
        future_text = "tomorrow's payment pipeline remains healthy" if future >= previous_future else "tomorrow's payment pipeline needs attention"
    return f"{collected_text} and {future_text}."


def _attention_line(report: ExtractedReport) -> str:
    if not report.passes_quality_gate:
        missing = ", ".join(_display(field) for field in report.missing_quality_fields)
        return f"🔴 Manual review required: missing {missing}."
    if report.needs_manual_review:
        return "⚠️ Manual review recommended for non-critical fields."
    return "✅ No immediate action required."


def _confidence_summary(report: ExtractedReport) -> tuple[str, str]:
    if not report.passes_quality_gate:
        return "🔴 Manual Review Required", "Required fields were missing or unreadable."
    score = report.confidence_score
    if score >= 0.9:
        icon = "🟢"
    elif score >= 0.72:
        icon = "🟡"
    else:
        return "🔴 Manual Review Required", "OCR confidence is below the safe threshold."
    return f"{icon} {_score(score)}", "OCR passed the quality gate for the executive brief."


def _valid_collector_name(name: str) -> bool:
    if not name or "," in name:
        return False
    if re.search(r"\b[A-Z]{2}\s+\d{5}\b", name.upper()):
        return False
    if re.search(r"\d{3}[-.) ]\d{3}[-. ]\d{4}", name):
        return False
    if any(blocked in name.upper() for blocked in ("ATLANTA", "DASHBOARD", "POSTED", "PENDING")):
        return False
    return bool(re.match(r"^[A-Z0-9 _.-]{2,30}$", name.upper()))
