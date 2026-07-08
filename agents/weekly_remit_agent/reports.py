from __future__ import annotations

from shared.integrations.microsoft_teams import TeamsMessage
from shared.utils.text import escape_html

from .models import RemitBatch


def build_broker_email_subject(batch: RemitBatch) -> str:
    return f"United Capital Management Weekly Remit - {batch.broker_name} - Week of {batch.week_start}"


def build_broker_email_html(batch: RemitBatch) -> str:
    broker = escape_html(batch.broker_name)
    week_start = escape_html(batch.week_start)
    remit_name = escape_html(batch.files.remit.name)
    liquidation_name = escape_html(batch.files.liquidation.name)
    return (
        "<p>Hi Jim,</p>"
        f"<p>Attached are United Capital Management's weekly {broker} remit report "
        f"and liquidation report for the week of {week_start}.</p>"
        "<p><strong>Attached files:</strong></p>"
        "<ul>"
        f"<li>{remit_name}</li>"
        f"<li>{liquidation_name}</li>"
        "</ul>"
        "<p>Please let us know if you need anything else.</p>"
        "<p>Thank you,<br>United Capital Management</p>"
    )


def build_owner_teams_message(batch: RemitBatch) -> TeamsMessage:
    title = "Weekly Remit Sent"
    text = "\n".join(
        [
            "**Weekly Remit Sent**",
            "",
            f"**Broker:** {batch.broker_name}",
            f"**Sent To:** {batch.recipient_email}",
            f"**Sent Date:** {batch.sent_date}",
            "**Files:**",
            f"- {batch.files.remit.name}",
            f"- {batch.files.liquidation.name}",
        ]
    )
    html = (
        "<h2>Weekly Remit Sent</h2>"
        f"<p><strong>Broker:</strong> {escape_html(batch.broker_name)}</p>"
        f"<p><strong>Sent To:</strong> {escape_html(batch.recipient_email)}</p>"
        f"<p><strong>Sent Date:</strong> {escape_html(batch.sent_date)}</p>"
        "<p><strong>Files:</strong></p>"
        "<ul>"
        f"<li>{escape_html(batch.files.remit.name)}</li>"
        f"<li>{escape_html(batch.files.liquidation.name)}</li>"
        "</ul>"
    )
    return TeamsMessage(title=title, text=text, html=html)
