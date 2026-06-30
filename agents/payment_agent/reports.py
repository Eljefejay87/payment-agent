from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .parser import cents_to_currency
from shared.integrations.microsoft_teams import TeamsMessage
from shared.utils.text import escape_html


def build_daily_report(rows: list, report_date: str) -> TeamsMessage:
    total_count = len(rows)
    total_cents = sum(row["payment_amount_cents"] for row in rows)
    title = f"UCM Payment Agent Daily Report - {report_date}"

    if not rows:
        text = f"**{title}**\n\nNo online payments were processed today."
        html = f"<h2>{title}</h2><p>No online payments were processed today.</p>"
        return TeamsMessage(title=title, text=text, html=html)

    lines = [
        f"**{title}**",
        "",
        f"Total payments: {total_count}",
        f"Total collected: {cents_to_currency(total_cents)}",
        "",
        "| Account | Amount | Type | Payment date | Note |",
        "|---|---:|---|---|---|",
    ]
    html_rows = []
    for row in rows:
        lines.append(
            f"| {row['account_number']} | {cents_to_currency(row['payment_amount_cents'])} | "
            f"{row['payment_type'] or ''} | {row['payment_date'] or ''} | {row['note'] or ''} |"
        )
        html_rows.append(
            "<tr>"
            f"<td>{escape_html(row['account_number'])}</td>"
            f"<td>{cents_to_currency(row['payment_amount_cents'])}</td>"
            f"<td>{escape_html(row['payment_type'] or '')}</td>"
            f"<td>{escape_html(row['payment_date'] or '')}</td>"
            f"<td>{escape_html(row['note'] or '')}</td>"
            "</tr>"
        )

    html = (
        f"<h2>{escape_html(title)}</h2>"
        f"<p><b>Total payments:</b> {total_count}<br>"
        f"<b>Total collected:</b> {cents_to_currency(total_cents)}</p>"
        "<table><thead><tr><th>Account</th><th>Amount</th><th>Type</th><th>Payment date</th><th>Note</th></tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody></table>"
    )
    return TeamsMessage(title=title, text="\n".join(lines), html=html)


def build_realtime_alert(row) -> TeamsMessage:
    title = "Payment Received"
    fields = [
        ("💵", "Amount", cents_to_currency(row.payment_amount_cents)),
        ("📄", "Account #", row.account_number),
        ("💳", "Payment Type", row.payment_type),
        ("📝", "Note", row.note),
        ("📅", "Received", row.payment_date or row.email_received_at),
    ]
    text_lines = ["💰 **Payment Received**"]
    html_lines = ["<h2>💰 Payment Received</h2>"]

    for icon, label, value in fields:
        if value:
            text_lines.append(f"{icon} **{label}:** {value}")
            html_lines.append(f"<p>{icon} <b>{escape_html(label)}:</b> {escape_html(str(value))}</p>")

    text = "\n\n".join(text_lines)
    html = "".join(html_lines)
    return TeamsMessage(title=title, text=text, html=html)


def today_in_timezone(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
