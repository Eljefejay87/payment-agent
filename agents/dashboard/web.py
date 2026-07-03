from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .service import DashboardService


class DashboardServer:
    def __init__(self, host: str, port: int, service: DashboardService) -> None:
        self.host = host
        self.port = port
        self.service = service

    def serve_forever(self) -> None:
        service = self.service

        class Handler(DashboardRequestHandler):
            dashboard_service = service

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.serve_forever()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    dashboard_service: DashboardService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(render_dashboard(self.dashboard_service.snapshot()))
            return
        if parsed.path == "/operations":
            self._send_html(render_operations_page(self.dashboard_service.snapshot()["operations"]))
            return
        if parsed.path == "/operations/screenshot":
            self._send_known_file(parsed.query, kind="screenshot")
            return
        if parsed.path == "/operations/report-file":
            self._send_known_file(parsed.query, kind="report")
            return
        if parsed.path == "/api/status":
            self._send_json(self.dashboard_service.snapshot())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        actions: dict[str, Callable[[], object]] = {
            "/api/payment-scan": self.dashboard_service.scan_payments,
            "/api/remit-send": self.dashboard_service.send_weekly_remit,
            "/api/open-remit-folder": self.dashboard_service.open_remit_folder,
        }
        action = actions.get(self.path)
        if not action:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        result = action()
        self._send_json({"ok": result.ok, "message": result.message})

    def log_message(self, format: str, *args) -> None:
        return

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_known_file(self, query: str, *, kind: str) -> None:
        params = parse_qs(query)
        report_hash = (params.get("hash") or [""])[0]
        report_date = (params.get("date") or [""])[0]
        operations = self.dashboard_service.operations_snapshot()
        reports = operations.get("detail", {}).get("historical_reports", [])
        report = next(
            (
                item
                for item in reports
                if (report_hash and item.get("screenshot_hash") == report_hash)
                or (report_date and item.get("report_date") == report_date)
            ),
            None,
        )
        if not report:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = Path(report["screenshot_path"] if kind == "screenshot" else report["report_path"])
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _content_type(path))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def render_dashboard(snapshot: dict, banner: str = "") -> str:
    payment = snapshot["payment"]
    remit = snapshot["remit"]
    checklist = snapshot["manager_checklist"]
    operations = snapshot["operations"]
    future_agents = snapshot["future_agents"]
    banner_html = f"<div id='banner' class='banner'>{_e(banner)}</div>" if banner else "<div id='banner'></div>"
    payment_rows = "".join(
        "<tr>"
        f"<td>{_e(row['account'])}</td>"
        f"<td>{_e(row['amount'])}</td>"
        f"<td>{_e(row['type'])}</td>"
        f"<td>{_e(row['date'])}</td>"
        "</tr>"
        for row in payment["recent"]
    ) or "<tr><td colspan='4' class='muted'>No recent payments found.</td></tr>"
    remit_files = "".join(f"<li>{_e(name)}</li>" for name in remit["files"]) or "<li>No files ready yet</li>"
    future_cards = "".join(
        f"""
        <article class="agent-card muted-card">
          <div class="card-head">
            <h3>{_e(agent['name'])}</h3>
            <span class="badge neutral">{_e(agent['status'])}</span>
          </div>
          <p>Priority: {_e(agent['priority'])}</p>
        </article>
        """
        for agent in future_agents
    )
    remit_send_disabled = "disabled" if remit["status"] != "Ready" else ""
    operations_card = _render_operations_card(operations)
    checklist_open_link = (
        f"<a class='button-link' href='{_e(checklist['url'])}' target='_blank' rel='noopener'>Open Checklist</a>"
        if checklist["url"]
        else "<button disabled>Open Checklist</button>"
    )
    checklist_sheet_link = (
        f"<a class='button-link secondary' href='{_e(checklist['sheet_url'])}' target='_blank' rel='noopener'>Open Sheet</a>"
    )
    checklist_status_url = json.dumps(checklist["url"] + "?dashboard=1&callback=renderChecklistStatus")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UCM Admin Dashboard</title>
  <style>{CSS}</style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>UCM Admin Dashboard</h1>
        <p>Local Agent Control Center</p>
      </div>
      <button class="secondary" onclick="location.reload()">Refresh</button>
    </header>
    {banner_html}
    <section class="summary-grid">
      <div class="metric">
        <span>Payments Today</span>
        <strong>{payment['today_count']}</strong>
      </div>
      <div class="metric">
        <span>Collected Today</span>
        <strong>{_e(payment['today_total'])}</strong>
      </div>
      <div class="metric">
        <span>Remit Status</span>
        <strong>{_e(remit['status'])}</strong>
      </div>
    </section>
    <section class="agent-grid">
      <article class="agent-card">
        <div class="card-head">
          <h2>Payment Agent</h2>
          <span class="badge ready">{_e(payment['status'])}</span>
        </div>
        <p class="detail">{_e(payment['detail'])}</p>
        <div class="actions">
          <button onclick="postAction('/api/payment-scan', true)">Scan Now</button>
        </div>
        <table>
          <thead><tr><th>Account</th><th>Amount</th><th>Type</th><th>Date</th></tr></thead>
          <tbody>{payment_rows}</tbody>
        </table>
      </article>
      <article class="agent-card">
        <div class="card-head">
          <h2>Weekly Remit Agent</h2>
          <span class="badge {'ready' if remit['status'] == 'Ready' else 'warn'}">{_e(remit['status'])}</span>
        </div>
        <p class="detail">{_e(remit['detail'])}</p>
        <dl>
          <dt>Broker</dt><dd>{_e(remit['broker'])}</dd>
          <dt>Deadline</dt><dd>{_e(remit['send_deadline'])}</dd>
          <dt>Last Sent</dt><dd>{_e(remit['last_sent'])}</dd>
        </dl>
        <ul class="file-list">{remit_files}</ul>
        <div class="actions">
          <button class="secondary" onclick="postAction('/api/open-remit-folder', false)">Open Drop Folder</button>
          <button {remit_send_disabled} onclick="postAction('/api/remit-send', true)">Send Weekly Remit</button>
        </div>
      </article>
      <article class="agent-card">
        <div class="card-head">
          <h2>Daily Checklist</h2>
          <span id="checklistStatusBadge" class="badge {'ready' if checklist['url'] else 'warn'}">{_e(checklist['status'])}</span>
        </div>
        <p class="detail">{_e(checklist['detail'])}</p>
        <dl>
          <dt>Checklist</dt><dd id="checklistPct">Loading...</dd>
          <dt>Attendance</dt><dd id="attendanceSubmitted">Loading...</dd>
          <dt>Submitted</dt><dd id="attendanceSubmittedAt">-</dd>
          <dt>Alerts</dt><dd>{_e(checklist['schedule'])}</dd>
        </dl>
        <div class="mini-columns">
          <div>
            <h3>At Work</h3>
            <ul id="atWorkList" class="compact-list"><li>Loading...</li></ul>
          </div>
          <div>
            <h3>Not At Work</h3>
            <ul id="notAtWorkList" class="compact-list"><li>Loading...</li></ul>
          </div>
        </div>
        <p class="detail" id="checklistFlags">New Biz flags: Loading...</p>
        <p class="detail" id="scheduledTimeOff">Scheduled time off: Loading...</p>
        <p class="detail" id="checklistWarnings">Warnings: Loading...</p>
        <div class="actions">
          {checklist_open_link}
          {checklist_sheet_link}
        </div>
      </article>
      {operations_card}
    </section>
    <section>
      <h2 class="section-title">Future Agents</h2>
      <div class="future-grid">{future_cards}</div>
    </section>
  </main>
  <script>
    async function postAction(path, confirmFirst) {{
      if (confirmFirst && !confirm('Run this action now?')) return;
      const banner = document.getElementById('banner');
      banner.className = 'banner';
      banner.textContent = 'Working...';
      const response = await fetch(path, {{ method: 'POST' }});
      const data = await response.json();
      banner.className = data.ok ? 'banner ok' : 'banner error';
      banner.textContent = data.message;
      if (data.ok) setTimeout(() => location.reload(), 1200);
    }}
    function renderList(id, items, emptyText) {{
      const list = document.getElementById(id);
      list.innerHTML = '';
      const values = items && items.length ? items : [emptyText];
      values.forEach((item) => {{
        const li = document.createElement('li');
        li.textContent = item;
        list.appendChild(li);
      }});
    }}
    function renderChecklistStatus(data) {{
      const badge = document.getElementById('checklistStatusBadge');
      if (badge) {{
        badge.textContent = data.status || 'Ready';
        badge.className = 'badge ' + (data.status === 'Complete' ? 'ready' : data.status === 'Ready' ? 'neutral' : 'warn');
      }}
      const completed = data.checklistCompleted || 0;
      const total = data.checklistTotal || 0;
      document.getElementById('checklistPct').textContent = total ? completed + ' / ' + total + ' (' + (data.checklistPercent || 0) + '%)' : (data.checklistPercent || 0) + '%';
      document.getElementById('attendanceSubmitted').textContent = data.morningAttendanceSaved ? 'Morning Saved' : data.attendanceSubmitted ? 'Submitted' : 'Not Submitted';
      document.getElementById('attendanceSubmittedAt').textContent = data.submittedAt || '-';
      renderList('atWorkList', data.atWork, 'None submitted');
      renderList('notAtWorkList', data.notAtWork, 'None submitted');
      document.getElementById('checklistFlags').textContent = 'New Biz flags: ' + (data.flags && data.flags.length ? data.flags.join(', ') : 'None');
      document.getElementById('scheduledTimeOff').textContent = 'Scheduled time off: ' + (data.scheduledTimeOff && data.scheduledTimeOff.length ? data.scheduledTimeOff.join(', ') : 'None');
      document.getElementById('checklistWarnings').textContent = 'Warnings: ' + (data.warnings && data.warnings.length ? data.warnings.join(', ') : 'None');
    }}
    function loadChecklistStatus() {{
      const script = document.createElement('script');
      script.src = {checklist_status_url} + '&_=' + Date.now();
      document.body.appendChild(script);
      script.onload = () => script.remove();
      script.onerror = () => {{
        document.getElementById('checklistPct').textContent = 'Unable to load';
        document.getElementById('attendanceSubmitted').textContent = 'Unable to load';
        document.getElementById('checklistWarnings').textContent = 'Warnings: Checklist status feed is not reachable.';
        script.remove();
      }};
    }}
    loadChecklistStatus();
    setInterval(loadChecklistStatus, 30000);
  </script>
</body>
</html>"""


def _e(value: object) -> str:
    return html.escape(str(value))


def render_operations_page(operations: dict) -> str:
    detail = operations["detail"]
    historical_rows = "".join(_render_operations_report_row(report) for report in detail["historical_reports"])
    manual_rows = "".join(_render_operations_report_row(report) for report in detail["manual_review_reports"])
    historical_rows = historical_rows or "<tr><td colspan='6' class='muted'>No historical reports available yet.</td></tr>"
    manual_rows = manual_rows or "<tr><td colspan='6' class='muted'>No reports need manual review.</td></tr>"
    executive_kpis = _render_ops_kpi_cards(detail["executive_kpis"], class_name="ops-exec-kpis")
    trend_cards = _render_ops_kpi_cards(detail["trend_cards"], class_name="ops-trend-kpis")
    charts = _render_operation_charts(detail["charts"])
    insights = _render_executive_insights(detail["executive_insights"])
    historical_trends = _render_historical_trends(detail["historical_trends"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Operations Intelligence</title>
  <style>{CSS}</style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>Operations Intelligence</h1>
        <p>Executive analytics from processed UCM operations reports</p>
      </div>
      <a class="button-link secondary" href="/">Back to Dashboard</a>
    </header>
    {executive_kpis}
    <section class="ops-analytics-grid">
      {charts}
      {insights}
    </section>
    {trend_cards}
    {historical_trends}
    <details class="agent-card ops-brief-card">
      <summary>View Latest Full Brief</summary>
      <pre class="brief-text">{_e(detail['latest_brief'])}</pre>
    </details>
    <section class="agent-card ops-table-card">
      <div class="card-head">
        <div>
          <h2>Historical Reports</h2>
          <p class="detail">{_e(detail['duplicate_audit'])}</p>
        </div>
        <div class="filter-tabs" aria-label="Historical report filters">
          <button class="secondary active" type="button" onclick="filterOpsReports('all', this)">All</button>
          <button class="secondary" type="button" onclick="filterOpsReports('ready', this)">Ready</button>
          <button class="secondary" type="button" onclick="filterOpsReports('manual-review', this)">Manual Review</button>
        </div>
      </div>
      <table>
        <thead><tr><th>Date</th><th>Status</th><th>Collected</th><th>Performance Score</th><th>AI Confidence</th><th>Links</th></tr></thead>
        <tbody>{historical_rows}</tbody>
      </table>
    </section>
    <section class="agent-card ops-table-card">
      <h2>Reports Needing Manual Review</h2>
      <table>
        <thead><tr><th>Date</th><th>Status</th><th>Collected</th><th>Performance Score</th><th>AI Confidence</th><th>Links</th></tr></thead>
        <tbody>{manual_rows}</tbody>
      </table>
    </section>
  </main>
  <script>
    function filterOpsReports(status, button) {{
      document.querySelectorAll('.filter-tabs button').forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      document.querySelectorAll('tr[data-status]').forEach((row) => {{
        row.hidden = status !== 'all' && row.dataset.status !== status;
      }});
    }}
  </script>
</body>
</html>"""


def _render_ops_kpi_cards(cards: list[dict], *, class_name: str) -> str:
    items = "".join(
        f"""
        <div class="ops-kpi-card {_e(card.get('tone', 'neutral'))}">
          <span>{_e(card['label'])}</span>
          <strong>{_e(card['value'])}</strong>
        </div>
        """
        for card in cards
    )
    return f"<section class='{class_name}'>{items}</section>"


def _render_operation_charts(charts: dict) -> str:
    return f"""
    <section class="agent-card ops-chart-panel">
      <div class="card-head">
        <h2>30-Day Movement</h2>
        <span class="badge neutral">Processed reports</span>
      </div>
      <div class="chart-grid">
        {_line_chart('Collections', charts['collections'], money=True)}
        {_line_chart('Performance Score', charts['performance_score'])}
        {_line_chart('Contact Rate', charts['contact_rate'], suffix='%')}
        {_scatter_chart('Calls vs Collections', charts['calls_vs_collections'])}
      </div>
    </section>
    """


def _render_executive_insights(insights: list[str]) -> str:
    items = "".join(f"<li>{_e(insight)}</li>" for insight in insights)
    return f"""
    <aside class="agent-card ops-insights-panel">
      <div class="card-head">
        <h2>Executive Insights</h2>
        <span class="badge neutral">Dashboard only</span>
      </div>
      <ul class="insight-list">{items}</ul>
    </aside>
    """


def _line_chart(title: str, points: list[dict], *, money: bool = False, suffix: str = "") -> str:
    if not points:
        return _empty_chart(title)
    values = [float(point["value"]) for point in points]
    low, high = min(values), max(values)
    span = high - low or 1.0
    width, height = 320, 130
    step = width / max(1, len(values) - 1)
    coords = [
        (round(index * step, 2), round(height - ((value - low) / span * (height - 22)) - 10, 2))
        for index, value in enumerate(values)
    ]
    polyline = " ".join(f"{x},{y}" for x, y in coords)
    circles = "".join(f"<circle cx='{x}' cy='{y}' r='3'></circle>" for x, y in coords)
    latest = _format_chart_value(values[-1], money=money, suffix=suffix)
    return f"""
    <div class="chart-card">
      <div class="chart-head"><span>{_e(title)}</span><strong>{_e(latest)}</strong></div>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(title)} chart">
        <polyline points="{polyline}"></polyline>
        {circles}
      </svg>
    </div>
    """


def _scatter_chart(title: str, points: list[dict]) -> str:
    values = [
        (float(point["x"]), float(point["y"]))
        for point in points
        if isinstance(point.get("x"), (int, float)) and isinstance(point.get("y"), (int, float))
    ]
    if not values:
        return _empty_chart(title)
    width, height = 320, 130
    min_x, max_x = min(x for x, _ in values), max(x for x, _ in values)
    min_y, max_y = min(y for _, y in values), max(y for _, y in values)
    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0
    dots = ""
    for x_value, y_value in values:
        x = round(((x_value - min_x) / span_x * (width - 24)) + 12, 2)
        y = round(height - ((y_value - min_y) / span_y * (height - 24)) - 12, 2)
        dots += f"<circle cx='{x}' cy='{y}' r='4'></circle>"
    return f"""
    <div class="chart-card">
      <div class="chart-head"><span>{_e(title)}</span><strong>{len(values)} days</strong></div>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(title)} chart">{dots}</svg>
    </div>
    """


def _empty_chart(title: str) -> str:
    return f"""
    <div class="chart-card empty-chart">
      <div class="chart-head"><span>{_e(title)}</span><strong>Not enough data</strong></div>
    </div>
    """


def _format_chart_value(value: float, *, money: bool = False, suffix: str = "") -> str:
    if money:
        return f"${value:,.0f}"
    if suffix:
        return f"{value:.2f}{suffix}"
    return f"{value:,.0f}"


def _render_historical_trends(trends: dict) -> str:
    items = [
        ("7-day avg collections", trends["average_7_day_collections"]),
        ("30-day avg collections", trends["average_30_day_collections"]),
        ("Best collection day", trends["best_collection_day"]),
        ("Lowest collection day", trends["lowest_collection_day"]),
        ("Same-weekday avg", trends["same_weekday_average"]),
        ("Collections vs 7-day avg", trends["collection_trend_vs_7_day"]),
        ("Contact rate vs 30-day avg", trends["contact_rate_trend_vs_30_day"]),
        ("Forecast", trends["forecast"]),
    ]
    cards = "".join(
        f"""
        <div class="trend-stat">
          <span>{_e(label)}</span>
          <strong>{_e(value)}</strong>
        </div>
        """
        for label, value in items
    )
    return f"""
    <section class="agent-card ops-trends-card">
      <div class="card-head">
        <h2>Historical Trends</h2>
        <span class="badge neutral">Dashboard only</span>
      </div>
      <div class="trend-grid">{cards}</div>
    </section>
    """


def _render_operations_card(operations: dict) -> str:
    card = operations["card"]
    if not operations["has_report"]:
        body = f"<p class='detail'>{_e(operations['message'])}</p>"
    elif operations["status"] == "Manual Review":
        body = "<p class='manual-review'>Manual review needed</p><p class='detail'>Latest OCR did not pass the quality gate.</p>"
    else:
        body = f"""
        <div class="ops-score-row">
          <div><span>Performance Score</span><strong>{_e(card['performance_score'])}</strong></div>
          <span class="badge {_e(card['quality'])}">{_e(card['confidence'])}</span>
        </div>
        <div class="ops-metric-grid">
          <div><span>Collected</span><strong>{_e(card['collected_today'])}</strong></div>
          <div><span>Future</span><strong>{_e(card['future_payments'])}</strong></div>
          <div><span>Pending</span><strong>{_e(card['pending_payments'])}</strong></div>
          <div><span>Calls</span><strong>{_e(card['calls'])}</strong></div>
          <div><span>Live Contacts</span><strong>{_e(card['live_contacts'])}</strong></div>
          <div><span>Accounts</span><strong>{_e(card['accounts_worked'])}</strong></div>
        </div>
        <p class="detail ops-takeaway">{_e(card['takeaway'])}</p>
        """
    return f"""
      <article class="agent-card operations-card">
        <div class="card-head">
          <h2>Operations Intelligence</h2>
          <span class="badge {'ready' if operations['status'] == 'Ready' else 'warn'}">{_e(operations['status'])}</span>
        </div>
        {body}
        <dl class="ops-update">
          <dt>Last updated</dt><dd>{_e(card['last_updated'])}</dd>
        </dl>
        <div class="actions">
          <a class="button-link" href="/operations">View Full Operations Report</a>
        </div>
      </article>
    """


def _render_operations_report_row(report: dict) -> str:
    status = "Ready" if _report_quality_passed(report) else "Manual review"
    status_key = "ready" if status == "Ready" else "manual-review"
    confidence = _report_confidence(report)
    collected = _report_collected(report)
    performance_score = _report_performance_score(report)
    report_href = f"/operations/report-file?date={quote(report['report_date'])}"
    screenshot_href = f"/operations/screenshot?hash={quote(report['screenshot_hash'])}"
    return (
        f"<tr data-status='{status_key}'>"
        f"<td>{_e(report['report_date'])}</td>"
        f"<td>{_e(status)}</td>"
        f"<td>{_e(collected)}</td>"
        f"<td>{_e(performance_score)}</td>"
        f"<td>{_e(confidence)}</td>"
        f"<td><a href='{report_href}' target='_blank'>Report</a> · <a href='{screenshot_href}' target='_blank'>Screenshot</a></td>"
        "</tr>"
    )


def _report_quality_passed(report: dict) -> bool:
    required = ("accounts_worked", "attempts", "live_contacts", "contact_rate")
    has_required = all(_metric_value(report, field) is not None for field in required)
    has_money = _metric_value(report, "posted_cash") is not None or _metric_value(report, "future_scheduled_cash") is not None
    return has_required and has_money


def _report_collected(report: dict) -> str:
    values = [_metric_value(report, field) for field in ("posted_cash", "posted_fees", "green_cleared_cash")]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return "Manual review" if not numeric else f"${sum(numeric):,.2f}"


def _report_confidence(report: dict) -> str:
    values = [
        metric.get("confidence")
        for metric in report.get("metrics", {}).values()
        if metric.get("value") is not None and isinstance(metric.get("confidence"), (int, float))
    ]
    return "Manual review" if not values else f"{round(sum(values) / len(values) * 100)}%"


def _report_performance_score(report: dict) -> str:
    for line in (report.get("summary_text") or "").splitlines():
        if " / 100" in line:
            return line.strip()
    return "Manual review"


def _metric_value(report: dict, field: str):
    return report.get("metrics", {}).get(field, {}).get("value")


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png"}:
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "text/plain; charset=utf-8"


CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7f8;
  --panel: #ffffff;
  --ink: #172026;
  --muted: #5e6b73;
  --line: #dbe2e7;
  --brand: #155e75;
  --brand-strong: #0f4658;
  --ok: #116149;
  --warn: #9a5b00;
  --soft-ok: #e8f5ef;
  --soft-warn: #fff4df;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 24px 0 40px;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
h1, h2, h3, p { margin: 0; }
h1 { font-size: 30px; letter-spacing: 0; }
.topbar p, .detail, .muted { color: var(--muted); }
button {
  appearance: none;
  border: 0;
  background: var(--brand);
  color: white;
  min-height: 40px;
  padding: 0 14px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 650;
  cursor: pointer;
}
a.button-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  appearance: none;
  border: 0;
  background: var(--brand);
  color: white;
  min-height: 40px;
  padding: 0 14px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 650;
  cursor: pointer;
  text-decoration: none;
}
button:hover { background: var(--brand-strong); }
button.secondary, a.button-link.secondary {
  background: #e8eef2;
  color: var(--ink);
}
a.button-link:hover { background: var(--brand-strong); }
button.secondary:hover, a.button-link.secondary:hover { background: #d7e1e7; }
button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.banner {
  min-height: 0;
  margin-bottom: 14px;
  padding: 0;
}
.banner:not(:empty) {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.banner.ok { border-color: #a7d7c3; background: var(--soft-ok); }
.banner.error { border-color: #f2c783; background: var(--soft-warn); }
.summary-grid, .agent-grid, .future-grid {
  display: grid;
  gap: 14px;
}
.summary-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 14px;
}
.metric, .agent-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.metric {
  padding: 16px;
}
.metric span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 8px;
}
.metric strong {
  display: block;
  font-size: 28px;
  letter-spacing: 0;
}
.agent-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
}
.agent-card {
  padding: 18px;
}
.operations-card {
  border-top: 3px solid var(--brand);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}
.badge.ready { color: var(--ok); background: var(--soft-ok); }
.badge.warn { color: var(--warn); background: var(--soft-warn); }
.badge.neutral { color: var(--muted); background: #edf1f4; }
.manual-review {
  color: var(--warn);
  font-size: 22px;
  font-weight: 750;
}
.ops-score-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.ops-score-row span, .ops-metric-grid span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 5px;
}
.ops-score-row strong {
  display: block;
  font-size: 22px;
  line-height: 1.15;
}
.ops-metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 14px 0;
}
.ops-metric-grid strong {
  display: block;
  font-size: 18px;
  overflow-wrap: anywhere;
}
.ops-takeaway {
  padding: 10px 12px;
  border-radius: 8px;
  background: #f0f6f8;
  color: var(--ink);
}
.ops-update {
  grid-template-columns: 92px 1fr;
  margin-bottom: 0;
}
.ops-detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.8fr);
  gap: 14px;
  align-items: start;
}
.ops-exec-kpis, .ops-trend-kpis {
  display: grid;
  gap: 12px;
  margin-bottom: 14px;
}
.ops-exec-kpis {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.ops-trend-kpis {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin-top: 14px;
}
.ops-kpi-card {
  min-height: 96px;
  padding: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.ops-kpi-card.ready { border-top: 3px solid var(--ok); }
.ops-kpi-card.warn { border-top: 3px solid var(--warn); }
.ops-kpi-card span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 10px;
}
.ops-kpi-card strong {
  display: block;
  font-size: 22px;
  line-height: 1.15;
  overflow-wrap: anywhere;
}
.ops-analytics-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 0.85fr);
  gap: 14px;
  align-items: start;
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.chart-card {
  min-height: 190px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.chart-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.chart-head span {
  color: var(--muted);
  font-size: 12px;
}
.chart-head strong {
  font-size: 15px;
}
.chart-card svg {
  display: block;
  width: 100%;
  height: 130px;
  overflow: visible;
}
.chart-card polyline {
  fill: none;
  stroke: var(--brand);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.chart-card circle {
  fill: var(--brand);
}
.empty-chart {
  display: flex;
  align-items: center;
}
.insight-list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.45;
}
.insight-list li + li {
  margin-top: 10px;
}
.ops-table-card {
  margin-top: 14px;
}
.ops-trends-card {
  margin-top: 14px;
}
.trend-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.trend-stat {
  min-height: 84px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.trend-stat span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}
.trend-stat strong {
  display: block;
  font-size: 18px;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.ops-brief-card {
  min-width: 0;
}
.brief-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  padding: 14px;
  border-radius: 8px;
  background: #f8fafb;
  border: 1px solid var(--line);
  color: var(--ink);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.45;
}
details.ops-brief-card {
  margin-top: 14px;
}
details.ops-brief-card summary {
  cursor: pointer;
  font-weight: 750;
}
details.ops-brief-card .brief-text {
  margin-top: 14px;
}
.filter-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.filter-tabs button {
  min-height: 34px;
  padding: 0 12px;
}
.filter-tabs button.active {
  background: var(--brand);
  color: white;
}
.subhead {
  margin-top: 18px;
}
.ops-copy {
  margin-top: 10px;
  line-height: 1.45;
}
.actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 16px 0;
}
table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 14px;
}
th, td {
  text-align: left;
  border-bottom: 1px solid var(--line);
  padding: 10px 8px;
  overflow-wrap: anywhere;
}
th {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
}
dl {
  display: grid;
  grid-template-columns: 92px 1fr;
  gap: 8px 10px;
  margin: 16px 0;
}
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; }
.file-list {
  padding-left: 20px;
  color: var(--ink);
}
.compact-list {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--ink);
}
.mini-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 12px 0;
}
.mini-columns h3 {
  font-size: 13px;
  color: var(--muted);
}
.section-title {
  margin: 22px 0 12px;
}
.future-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.muted-card {
  min-height: 110px;
}
@media (max-width: 860px) {
  .summary-grid, .agent-grid, .future-grid, .ops-detail-grid, .ops-analytics-grid {
    grid-template-columns: 1fr;
  }
  .ops-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ops-exec-kpis, .ops-trend-kpis, .chart-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .trend-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (max-width: 520px) {
  main {
    width: min(100vw - 20px, 1180px);
    padding-top: 16px;
  }
  .ops-score-row {
    flex-direction: column;
  }
  .ops-metric-grid {
    grid-template-columns: 1fr;
  }
  .ops-exec-kpis, .ops-trend-kpis, .chart-grid {
    grid-template-columns: 1fr;
  }
  .trend-grid {
    grid-template-columns: 1fr;
  }
  table {
    font-size: 13px;
  }
}
"""
