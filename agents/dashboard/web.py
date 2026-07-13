from __future__ import annotations

import html
import json
import re
from datetime import date
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from uuid import uuid4

from .service import DashboardService
from .shared_data import ReviewQueueFilters
from .review_actions import ReviewActionError, ReviewConflictError
from shared.data_layer.models import Priority, RecordType, ReviewStatus, SourceSystem


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
        if parsed.path == "/needs-review":
            self._send_html(
                render_needs_review_page(
                    self._review_items(parsed.query),
                    parsed.query,
                    csrf_token=self.dashboard_service.review_csrf_token,
                )
            )
            return
        review_match = re.fullmatch(r"/operations/review/(\d+)", parsed.path)
        if review_match:
            report = self.dashboard_service.operations_review_report(int(review_match.group(1)))
            if not report:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_html(render_operations_review_page(report))
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
        if parsed.path == "/api/shared-dashboard":
            self._send_json(self.dashboard_service.shared_data.summary())
            return
        if parsed.path == "/api/needs-review":
            self._send_json({"items": self._review_items(parsed.query)})
            return
        audit_match = re.fullmatch(r"/api/needs-review/([^/]+)/audit", parsed.path)
        if audit_match:
            self._send_json({"events": self.dashboard_service.review_actions.audit_history(audit_match.group(1))})
            return
        review_item_match = re.fullmatch(r"/api/needs-review/([^/]+)", parsed.path)
        if review_item_match:
            item = self.dashboard_service.shared_data.review_item(review_item_match.group(1))
            if item is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_json(item)
            return
        if parsed.path == "/api/agent-health":
            self._send_json(self.dashboard_service.shared_data.agent_health())
            return
        if parsed.path == "/dashboard-logo":
            self._send_dashboard_logo()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _review_items(self, query: str) -> list[dict]:
        params = {key: values[-1] for key, values in parse_qs(query).items() if values}
        filters = ReviewQueueFilters(
            record_type=_enum_or_none(RecordType, params.get("record_type")),
            source_system=_enum_or_none(SourceSystem, params.get("source_system")),
            priority=_enum_or_none(Priority, params.get("priority")),
            review_status=_enum_or_none(ReviewStatus, params.get("review_status")),
            action_required=_bool_or_none(params.get("action_required")),
            date_from=_date_or_none(params.get("date_from")),
            date_to=_date_or_none(params.get("date_to")),
        )
        return self.dashboard_service.shared_data.needs_review(
            filters,
            page=max(1, int(params.get("page", "1"))),
            page_size=min(100, max(1, int(params.get("page_size", "25")))),
        )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        review_action_match = re.fullmatch(
            r"/api/needs-review/([^/]+)/(approve|reject|resolve)", parsed.path
        )
        if review_action_match:
            self._handle_review_action(review_action_match.group(1), review_action_match.group(2))
            return
        actions: dict[str, Callable[[], object]] = {
            "/api/payment-scan": self.dashboard_service.scan_payments,
            "/api/remit-send": self.dashboard_service.send_weekly_remit,
            "/api/open-remit-folder": self.dashboard_service.open_remit_folder,
            "/api/shared-sync": self.dashboard_service.sync_shared_data,
        }
        action = actions.get(self.path)
        if action:
            result = action()
            self._send_json({"ok": result.ok, "message": result.message})
            return

        match = re.fullmatch(r"/operations/review/(\d+)/(save|approve|reprocess)", parsed.path)
        if not match:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        report_id = int(match.group(1))
        command = match.group(2)
        form = self._read_form()
        if command == "save":
            result = self.dashboard_service.save_operations_corrections(report_id, form)
        elif command == "approve":
            result = self.dashboard_service.approve_operations_report(report_id)
        else:
            result = self.dashboard_service.reprocess_operations_report(report_id)
        if result.ok:
            self._redirect(f"/operations/review/{report_id}?message={quote(result.message)}")
        else:
            report = self.dashboard_service.operations_review_report(report_id)
            if not report:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_html(render_operations_review_page(report, error=result.message))

    def _handle_review_action(self, record_id: str, action: str) -> None:
        form = self._read_form()
        if form.get("csrf_token") != self.dashboard_service.review_csrf_token:
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid review action token")
            return
        if form.get("confirmation") != "CONFIRM":
            self._redirect("/needs-review?error=" + quote("Explicit confirmation is required."))
            return
        try:
            result = self.dashboard_service.review_actions.apply(
                record_id,
                action=action,
                reviewer=form.get("reviewer", ""),
                reason=form.get("reason"),
                expected_updated_at=form.get("expected_updated_at", ""),
                request_id=form.get("request_id", ""),
            )
        except ReviewConflictError as exc:
            self._redirect("/needs-review?error=" + quote(str(exc)))
            return
        except ReviewActionError as exc:
            self._redirect("/needs-review?error=" + quote(str(exc)))
            return
        suffix = " (duplicate request; no second change)" if result.duplicate_request else ""
        action_label = {"approve": "approved", "reject": "rejected", "resolve": "resolved"}[action]
        self._redirect("/needs-review?message=" + quote(f"Review item {action_label}{suffix}."))

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

    def _redirect(self, path: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.end_headers()

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = parse_qs(body, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

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

    def _send_dashboard_logo(self) -> None:
        logo_path = Path(getattr(self.dashboard_service.dashboard_settings, "logo_path", ""))
        if not logo_path.is_file() or logo_path.suffix.lower() != ".png":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = logo_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)


def render_dashboard(snapshot: dict, banner: str = "") -> str:
    payment = snapshot["payment"]
    remit = snapshot["remit"]
    cash_flow = snapshot.get("cash_flow") or {
        "status": "Unavailable",
        "message": "Not available",
        "summary": {
            "due_this_week_total": "$0.00",
            "needs_review_count": 0,
            "upcoming_count": 0,
            "past_due_count": 0,
            "paid_count": 0,
        },
        "needs_attention": [],
        "upcoming_bills": [],
    }
    checklist = snapshot["manager_checklist"]
    operations = snapshot["operations"]
    future_agents = snapshot["future_agents"]
    shared_dashboard = snapshot.get("shared_dashboard") or {"needs_review": {}}
    sync_health = snapshot.get("sync_health") or {}
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
    remit_badge_class = "ready" if remit["status"] in {"Ready", "Sent"} else "warn"
    remit_send_disabled = "disabled" if remit["status"] != "Ready" else ""
    cash_flow_dashboard = _render_cash_flow_dashboard(cash_flow)
    operations_card = _render_operations_card(operations)
    needs_review_card = _render_needs_review(shared_dashboard)
    sync_health_card = _render_sync_health(sync_health)
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
      <div class="brand-lockup">
        <div class="brand-logo-crop"><img src="/dashboard-logo" alt="United Capital Management"></div>
        <div>
          <h1>UCM Admin Dashboard</h1>
          <p>Local Agent Control Center</p>
        </div>
      </div>
      <button class="secondary" onclick="location.reload()">Refresh</button>
    </header>
    {banner_html}
    <div id="scheduledOffTodayAlert" class="scheduled-off-banner" hidden></div>
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
          <span class="badge {remit_badge_class}">{_e(remit['status'])}</span>
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
    </section>
    <section class="intelligence-grid operations-only-grid">
      {operations_card}
    </section>
    <section class="cash-review-grid">
      {cash_flow_dashboard}
      {needs_review_card}
    </section>
    {sync_health_card}
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
    function filterCashFlowForecast() {{
      const category = document.getElementById('forecastCategory')?.value || '';
      const vendor = document.getElementById('forecastVendor')?.value || '';
      const status = document.getElementById('forecastStatus')?.value || '';
      const payment = document.getElementById('forecastPayment')?.value || '';
      let visible = 0;
      document.querySelectorAll('.forecast-row').forEach((row) => {{
        const matches = (!category || row.dataset.category === category)
          && (!vendor || row.dataset.vendor === vendor)
          && (!status || row.dataset.status === status)
          && (!payment || row.dataset.payment === payment);
        row.hidden = !matches;
        if (matches) visible += 1;
      }});
      const empty = document.getElementById('forecastEmptyRow');
      if (empty) empty.hidden = visible !== 0;
    }}
    function renderScheduledOffToday(items) {{
      const alert = document.getElementById('scheduledOffTodayAlert');
      const names = (items || [])
        .map((item) => String(item).split(':')[0].trim())
        .filter(Boolean);
      alert.innerHTML = '';
      if (!names.length) {{
        alert.hidden = true;
        return;
      }}
      const title = document.createElement('strong');
      title.textContent = 'Scheduled Off Today:';
      const list = document.createElement('ul');
      names.forEach((name) => {{
        const li = document.createElement('li');
        li.textContent = name;
        list.appendChild(li);
      }});
      alert.appendChild(title);
      alert.appendChild(list);
      alert.hidden = false;
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
      renderScheduledOffToday(data.scheduledTimeOff);
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


def _render_cash_flow_dashboard(cash_flow: dict) -> str:
    summary = cash_flow["summary"]
    forecast = cash_flow.get("forecast") or {}
    periods = forecast.get("periods") or {}
    payment_types = forecast.get("payment_types") or {}
    filters = forecast.get("filters") or {}
    needs_attention = "".join(
        f"""
        <div class="cash-flow-attention-item">
          <strong>{_e(item.get('vendor') or item.get('expense_name') or 'Unknown vendor')}</strong>
          <span>{_e(_cash_amount(item.get('amount')))}</span>
          <span>{_e(item.get('due_status') or 'No due date')}</span>
          <p>{_e(item.get('notes') or '')}</p>
        </div>
        """
        for item in cash_flow.get("needs_attention", [])
    ) or "<p class='muted'>No bills need attention.</p>"
    period_specs = (
        ("past_due", "Past Due", "red"),
        ("due_today", "Due Today", "yellow"),
        ("next_7_days", "Next 7 Days", "yellow"),
        ("next_30_days", "Next 30 Days", "green"),
        ("this_month", "This Month", "green"),
    )
    horizon_cards = "".join(
        f"""
        <div class="forecast-horizon-card {tone}">
          <span>{label}</span>
          <strong>{_e(periods.get(key, {}).get('total', '$0.00'))}</strong>
          <small>{periods.get(key, {}).get('count', 0)} bill{'s' if periods.get(key, {}).get('count', 0) != 1 else ''}</small>
          <div class="forecast-progress" aria-hidden="true"><i style="width:{periods.get(key, {}).get('progress', 0)}%"></i></div>
        </div>
        """
        for key, label, tone in period_specs
    )
    top_rows = "".join(
        "<tr class='forecast-row' "
        f"data-category='{_e(_cash_filter_value(item.get('category')))}' "
        f"data-vendor='{_e(_cash_filter_value(item.get('vendor') or item.get('expense_name')))}' "
        f"data-status='{_e(_cash_filter_value(item.get('forecast_status')))}' "
        f"data-payment='{_e(_cash_payment_filter(item.get('payment_type')))}'>"
        f"<td>{_e(item.get('vendor') or item.get('expense_name') or 'Unknown vendor')}</td>"
        f"<td>{_e(_cash_date(item.get('due_date')))}</td>"
        f"<td>{_e(_cash_amount(item.get('amount')))}</td>"
        f"<td>{_e(item.get('category') or '')}</td>"
        f"<td><span class='badge forecast-{_cash_badge_class(item.get('forecast_status'))}'>{_e(item.get('forecast_status') or 'Upcoming')}</span></td>"
        f"<td>{_e(item.get('action_required') or '—')}</td>"
        "</tr>"
        for item in forecast.get("top_upcoming", [])
    )
    top_rows += "<tr id='forecastEmptyRow' hidden><td colspan='6' class='muted'>No upcoming bills match these filters.</td></tr>"
    category_options = _cash_filter_options(filters.get("categories", []), "All categories")
    vendor_options = _cash_filter_options(filters.get("vendors", []), "All vendors")
    status_options = _cash_filter_options(filters.get("statuses", []), "All statuses", forecast_status=True)
    message = f"<p class='detail'>{_e(cash_flow['message'])}</p>" if cash_flow.get("message") else ""
    return f"""
    <section class="agent-card cash-flow-dashboard">
      <div class="card-head">
        <div>
          <h2>Cash Flow Forecast</h2>
          <p class="detail">Read-only forecast from Cash Flow HQ</p>
        </div>
        <span class="badge {'ready' if cash_flow.get('status') == 'Ready' else 'warn'}">{_e(cash_flow.get('status', 'Ready'))}</span>
      </div>
      {message}
      <div class="forecast-horizon">{horizon_cards}</div>
      <div class="forecast-payment-split">
        <div><span>AutoPay Total</span><strong>{_e(payment_types.get('autopay', {}).get('total', '$0.00'))}</strong></div>
        <div><span>Manual Payment Total</span><strong>{_e(payment_types.get('manual', {}).get('total', '$0.00'))}</strong></div>
      </div>
      <div class="cash-flow-summary">
        <div class="cash-flow-card"><span>Bills Due This Week</span><strong>{_e(summary['due_this_week_total'])}</strong></div>
        <div class="cash-flow-card"><span>Needs Review</span><strong>{summary['needs_review_count']}</strong></div>
        <div class="cash-flow-card"><span>Upcoming Bills</span><strong>{summary['upcoming_count']}</strong></div>
        <div class="cash-flow-card"><span>Past Due</span><strong>{summary['past_due_count']}</strong></div>
        <div class="cash-flow-card"><span>Paid</span><strong>{summary['paid_count']}</strong></div>
      </div>
      <div class="cash-flow-sections">
        <section>
          <h3>Needs Attention</h3>
          <div class="cash-flow-attention-list">{needs_attention}</div>
        </section>
        <section>
          <h3>Top Upcoming Payments</h3>
          <div class="forecast-filters" aria-label="Cash Flow Forecast filters">
            <label><span>Category</span><select id="forecastCategory" onchange="filterCashFlowForecast()">{category_options}</select></label>
            <label><span>Vendor</span><select id="forecastVendor" onchange="filterCashFlowForecast()">{vendor_options}</select></label>
            <label><span>Status</span><select id="forecastStatus" onchange="filterCashFlowForecast()">{status_options}</select></label>
            <label><span>Payment</span><select id="forecastPayment" onchange="filterCashFlowForecast()"><option value="">All payments</option><option value="autopay">AutoPay only</option><option value="manual">Manual only</option></select></label>
          </div>
          <table>
            <thead><tr><th>Vendor</th><th>Due Date</th><th>Amount</th><th>Category</th><th>Status</th><th>Action Required</th></tr></thead>
            <tbody>{top_rows}</tbody>
          </table>
        </section>
      </div>
    </section>
    """


def _render_needs_review(shared_dashboard: dict) -> str:
    review = shared_dashboard.get("needs_review", {})
    preview_items = review.get("top_items", [])[:5]
    items = "".join(
        f"""
        <li>
          <div><strong>{_e(item.get('title') or '')}</strong><span>{_e(item.get('effective_date') or 'No effective date')}</span></div>
          <p>{_e(item.get('review_reason') or '')}</p>
        </li>
        """
        for item in preview_items
    ) or "<li class='muted'>No normalized records currently need review.</li>"
    return f"""
    <section class="agent-card needs-review-dashboard">
      <div class="card-head">
        <div><h2>Needs Review</h2><p class="detail">Read-only normalized action queue</p></div>
        <div><span class="badge {'warn' if review.get('unresolved_count', 0) else 'ready'}">{review.get('unresolved_count', 0)} unresolved</span> <a class="button-link small secondary" href="/needs-review">View all</a></div>
      </div>
      <div class="needs-review-summary">
        <div><span>Critical / High</span><strong>{review.get('critical_high_count', 0)}</strong></div>
        <div><span>Past Due</span><strong>{review.get('past_due_count', 0)}</strong></div>
        <div><span>Failed Runs</span><strong>{review.get('failed_agent_run_count', 0)}</strong></div>
        <div><span>Oldest Age</span><strong>{review.get('oldest_unresolved_age_days', 0)}d</strong></div>
      </div>
      <ul class="review-preview-list">{items}</ul>
    </section>
    """


def _render_sync_health(health: dict) -> str:
    latest = health.get("latest") or {}
    status = latest.get("status") or "not_run"
    last_run = latest.get("completed_at") or "Never"
    counts = (
        f"{latest.get('records_created', 0)} created, "
        f"{latest.get('records_updated', 0)} updated, "
        f"{latest.get('records_skipped', 0)} unchanged"
        if latest
        else "No scheduled sync has run yet."
    )
    return f"""
    <section class="agent-card sync-health-card">
      <div class="card-head">
        <div><h2>Shared Data Sync</h2><p class="detail">Cash Flow HQ and ICR synchronization health</p></div>
        <span class="badge {'ready' if status == 'completed' else 'warn'}">{_e(status.replace('_', ' ').title())}</span>
      </div>
      <div class="cash-flow-summary">
        <div class="cash-flow-card"><span>Schedule</span><strong>{health.get('interval_minutes', 60)}m</strong></div>
        <div class="cash-flow-card"><span>Source</span><strong>{_e(health.get('source', 'all'))}</strong></div>
        <div class="cash-flow-card"><span>Recent Failures</span><strong>{health.get('failed_count', 0)}</strong></div>
      </div>
      <p class="detail">Last completed: {_e(last_run)}</p>
      <p>{_e(counts)}</p>
      <div class="actions"><button onclick="postAction('/api/shared-sync', true)">Sync Now</button></div>
    </section>
    """


def render_needs_review_page(items: list[dict], query: str = "", *, csrf_token: str = "") -> str:
    params = {key: values[-1] for key, values in parse_qs(query).items() if values}
    message = params.get("message")
    error = params.get("error")
    banner = ""
    if message:
        banner = f'<div class="banner ok">{_e(message)}</div>'
    elif error:
        banner = f'<div class="banner error">{_e(error)}</div>'
    rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('title') or '')}<br><small>{_e(item.get('summary') or '')}</small></td>"
        f"<td>{_e(item.get('record_type') or '')}</td>"
        f"<td>{_e(item.get('source_system') or '')}</td>"
        f"<td>{_e(item.get('priority') or '')}</td>"
        f"<td>{_e(item.get('review_status') or '')}</td>"
        f"<td>{_e(item.get('review_reason') or '')}</td>"
        f"<td>{_e(item.get('effective_date') or '')}</td>"
        f"<td>{_review_action_form(item, csrf_token)}</td>"
        "</tr>"
        for item in items
    ) or "<tr><td colspan='7' class='muted'>No review items match these filters.</td></tr>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Needs Review - UCM Admin Dashboard</title><style>{CSS}</style></head>
<body><main>
  <header class="topbar"><div><h1>Needs Review</h1><p class="detail">Read-only normalized action queue</p></div><a class="button-link secondary" href="/">Dashboard</a></header>
  {banner}
  <section class="agent-card">
    <form class="forecast-filters" method="get" action="/needs-review">
      {_filter_select('record_type', params.get('record_type'), ['bill', 'remit', 'agent_run'], 'All record types')}
      {_filter_select('source_system', params.get('source_system'), ['outlook', 'local_file', 'sqlite'], 'All sources')}
      {_filter_select('priority', params.get('priority'), ['critical', 'high', 'normal', 'low'], 'All priorities')}
      {_filter_select('review_status', params.get('review_status'), ['pending'], 'All review statuses')}
      {_filter_select('action_required', params.get('action_required'), ['true', 'false'], 'Any action state')}
      <label><span>From</span><input type="date" name="date_from" value="{_e(params.get('date_from', ''))}"></label>
      <label><span>To</span><input type="date" name="date_to" value="{_e(params.get('date_to', ''))}"></label>
      <button type="submit">Apply filters</button>
    </form>
    <table><thead><tr><th>Item</th><th>Type</th><th>Source</th><th>Priority</th><th>Review</th><th>Reason</th><th>Date</th><th>Controlled action</th></tr></thead><tbody>{rows}</tbody></table>
  </section>
</main></body></html>"""


def _filter_select(name: str, selected: str | None, values: list[str], all_label: str) -> str:
    options = [f'<option value="">{_e(all_label)}</option>']
    options.extend(
        f'<option value="{_e(value)}"{" selected" if value == selected else ""}>{_e(value.replace("_", " ").title())}</option>'
        for value in values
    )
    return f'<label><span>{_e(name.replace("_", " ").title())}</span><select name="{_e(name)}">{"".join(options)}</select></label>'


def _review_action_form(item: dict, csrf_token: str) -> str:
    if item.get("record_type") == "agent_run":
        return "<span class='muted'>Read-only alert</span>"
    record_id = _e(item.get("id") or "")
    return f"""
    <form method="post" class="review-action-form">
      <input type="hidden" name="csrf_token" value="{_e(csrf_token)}">
      <input type="hidden" name="expected_updated_at" value="{_e(item.get('updated_at') or '')}">
      <input type="hidden" name="request_id" value="{uuid4()}">
      <label><span>Reviewer</span><input name="reviewer" maxlength="100" required></label>
      <label><span>Reason (required for reject)</span><input name="reason" maxlength="500"></label>
      <label><input type="checkbox" name="confirmation" value="CONFIRM" required> Confirm this local review decision</label>
      <div class="actions">
        <button formaction="/api/needs-review/{record_id}/approve" type="submit">Approve</button>
        <button formaction="/api/needs-review/{record_id}/resolve" class="secondary" type="submit">Resolve</button>
        <button formaction="/api/needs-review/{record_id}/reject" class="danger" type="submit">Reject</button>
      </div>
    </form>"""


def _cash_amount(value: object) -> str:
    return f"${float(value):,.2f}" if isinstance(value, (int, float)) else ""


def _cash_date(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _cash_filter_value(value: object) -> str:
    return str(value or "").strip().lower()


def _cash_payment_filter(value: object) -> str:
    return "autopay" if _cash_filter_value(value) in {"auto pay", "autopay"} else "manual"


def _cash_badge_class(value: object) -> str:
    return {"past due": "past-due", "due soon": "due-soon", "paid": "paid"}.get(_cash_filter_value(value), "upcoming")


def _cash_filter_options(values: list[str], all_label: str, *, forecast_status: bool = False) -> str:
    options = [f'<option value="">{_e(all_label)}</option>']
    source = ["Past Due", "Due Soon", "Upcoming", "Paid"] if forecast_status else values
    options.extend(f'<option value="{_e(_cash_filter_value(value))}">{_e(value)}</option>' for value in source)
    return "".join(options)


def render_operations_page(operations: dict) -> str:
    detail = operations["detail"]
    historical_rows = "".join(_render_operations_report_row(report) for report in detail["historical_reports"])
    manual_rows = "".join(_render_operations_report_row(report) for report in detail["manual_review_reports"])
    queue_rows = "".join(_render_manual_review_queue_row(report) for report in detail.get("manual_review_queue", []))
    historical_rows = historical_rows or "<tr><td colspan='6' class='muted'>No historical reports available yet.</td></tr>"
    manual_rows = manual_rows or "<tr><td colspan='6' class='muted'>No reports need manual review.</td></tr>"
    queue_rows = queue_rows or "<tr><td colspan='6' class='muted'>No owner review needed.</td></tr>"
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
    <section class="agent-card ops-table-card">
      <div class="card-head">
        <div>
          <h2>Manual Review Queue</h2>
          <p class="detail">One owner-facing item per business date. Debug and duplicate runs stay hidden here.</p>
        </div>
      </div>
      <table>
        <thead><tr><th>Date</th><th>Reason for Review</th><th>Missing Fields</th><th>AI Confidence</th><th>Screenshot</th><th>Review</th></tr></thead>
        <tbody>{queue_rows}</tbody>
      </table>
    </section>
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


def render_operations_review_page(report: dict, *, error: str = "") -> str:
    screenshot = f"/operations/screenshot?hash={quote(report['screenshot_hash'])}"
    status = "Ready" if _report_quality_passed(report) else "Manual Review"
    alert = f"<div class='banner error'>{_e(error)}</div>" if error else ""
    metrics = _render_review_metrics(report)
    form = _render_review_form(report)
    missing = ", ".join(_e(field) for field in report.get("missing_fields", [])) or "None"
    notes = "".join(f"<li>{_e(note)}</li>" for note in report.get("manual_review_notes", [])) or "<li>None</li>"
    ocr_text = report.get("ocr_text") or "No OCR text saved."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Operations Manual Review</title>
  <style>{CSS}</style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>Operations Manual Review</h1>
        <p>{_e(report['report_date'])} · {_e(status)} · {_e(_report_confidence(report))}</p>
      </div>
      <a class="button-link secondary" href="/operations">Back to Operations</a>
    </header>
    {alert}
    <section class="review-grid">
      <article class="agent-card">
        <div class="card-head">
          <h2>Original Screenshot</h2>
          <a href="{screenshot}" target="_blank">Open image</a>
        </div>
        <img class="review-shot" src="{screenshot}" alt="Operations screenshot for {_e(report['report_date'])}">
      </article>
      <article class="agent-card">
        <div class="card-head">
          <h2>Review Values</h2>
          <span class="badge {'ready' if status == 'Ready' else 'warn'}">{_e(status)}</span>
        </div>
        <dl>
          <dt>Missing</dt><dd>{missing}</dd>
          <dt>Confidence</dt><dd>{_e(_report_confidence(report))}</dd>
          <dt>Approved</dt><dd>{_e(report.get('approved_at') or 'Not approved')}</dd>
          <dt>Edited</dt><dd>{_e(', '.join(report.get('manually_edited_fields') or []) or 'None')}</dd>
        </dl>
        <h3>Extracted Values</h3>
        {metrics}
        <h3 class="subhead">Review Notes</h3>
        <ul class="insight-list">{notes}</ul>
      </article>
    </section>
    <section class="agent-card review-form-card">
      <div class="card-head">
        <h2>Corrections</h2>
        <span class="badge neutral">Dashboard only</span>
      </div>
      {form}
    </section>
    <details class="agent-card ops-brief-card">
      <summary>Raw OCR Output</summary>
      <pre class="brief-text">{_e(ocr_text)}</pre>
    </details>
  </main>
</body>
</html>"""


def _render_review_metrics(report: dict) -> str:
    fields = (
        ("posted_cash", "Collected Today"),
        ("future_scheduled_cash", "Future Payments"),
        ("pending_cash", "Pending Payments"),
        ("attempts", "Calls"),
        ("live_contacts", "Live Contacts"),
        ("accounts_worked", "Accounts Worked"),
        ("contact_rate", "Contact Rate"),
        ("close_rate", "Close Rate"),
    )
    cards = ""
    for field, label in fields:
        metric = report.get("metrics", {}).get(field, {})
        value = metric.get("value")
        confidence = metric.get("confidence")
        needs_review = value is None or (isinstance(confidence, (int, float)) and confidence < 0.72)
        cards += f"""
        <div class="review-value {'needs-review' if needs_review else ''}">
          <span>{_e(label)}</span>
          <strong>{_e(_format_metric_value(field, value))}</strong>
          <small>{_e(_confidence_text(confidence))}</small>
        </div>
        """
    top = _top_collector(report)
    cards += f"""
      <div class="review-value">
        <span>Top Performer</span>
        <strong>{_e(top)}</strong>
        <small>Whiteboard only</small>
      </div>
    """
    return f"<div class='review-values'>{cards}</div>"


def _render_review_form(report: dict) -> str:
    fields = (
        ("posted_cash", "Collected Today", "money"),
        ("future_scheduled_cash", "Future Payments", "money"),
        ("pending_cash", "Pending Payments", "money"),
        ("attempts", "Calls", "count"),
        ("live_contacts", "Live Contacts", "count"),
        ("accounts_worked", "Accounts Worked", "count"),
        ("contact_rate", "Contact Rate", "percent"),
        ("close_rate", "Close Rate", "percent"),
    )
    inputs = ""
    for field, label, kind in fields:
        value = report.get("metrics", {}).get(field, {}).get("value")
        inputs += f"""
          <label>
            <span>{_e(label)}</span>
            <input name="{field}" value="{_e(_input_value(kind, value))}" placeholder="{_e(_placeholder(kind))}">
          </label>
        """
    return f"""
      <form method="post" action="/operations/review/{report['id']}/save" id="save-corrections-form">
        <div class="review-form-grid">{inputs}</div>
        <div class="review-form-grid top-performer-fields">
          <label><span>Top Performer Code</span><input name="top_performer_code" placeholder="KMAD"></label>
          <label><span>Top Performer Amount</span><input name="top_performer_total" placeholder="$2,845.15"></label>
        </div>
        <label class="wide-field">
          <span>Collector Totals</span>
          <textarea name="collector_totals_text" rows="4" placeholder="KMAD - $2,845.15&#10;CSOLO - $1,250.00"></textarea>
        </label>
      </form>
      <div class="actions">
        <button type="submit" form="save-corrections-form">Save Corrections</button>
        <form method="post" action="/operations/review/{report['id']}/approve">
          <button type="submit" class="secondary">Approve Report</button>
        </form>
        <form method="post" action="/operations/review/{report['id']}/reprocess">
          <button type="submit" class="secondary">Reprocess OCR</button>
        </form>
        <a class="button-link secondary" href="/operations">Cancel</a>
      </div>
    """


def _render_manual_review_queue_row(report: dict) -> str:
    screenshot_href = f"/operations/screenshot?hash={quote(report['screenshot_hash'])}"
    review_href = f"/operations/review/{report['id']}"
    return (
        "<tr>"
        f"<td>{_e(report['report_date'])}</td>"
        f"<td>{_e(report['reason'])}</td>"
        f"<td>{_e(report['missing_fields'])}</td>"
        f"<td>{_e(report['confidence'])}</td>"
        f"<td><a href='{screenshot_href}' target='_blank'>Screenshot</a></td>"
        f"<td><a class='button-link small' href='{review_href}'>Review</a></td>"
        "</tr>"
    )


def _format_metric_value(field: str, value: object) -> str:
    if value is None:
        return "Manual review"
    if field in {"posted_cash", "future_scheduled_cash", "pending_cash"} and isinstance(value, (int, float)):
        return f"${value:,.2f}"
    if field in {"contact_rate", "close_rate"} and isinstance(value, (int, float)):
        return f"{value:.2f}%"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def _input_value(kind: str, value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    if kind == "money":
        return f"{value:.2f}"
    if kind == "percent":
        return f"{value:.2f}%"
    return str(int(value))


def _placeholder(kind: str) -> str:
    if kind == "money":
        return "$0.00"
    if kind == "percent":
        return "0.00%"
    return "0"


def _confidence_text(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "No confidence"
    return f"{round(value * 100)}% confidence"


def _top_collector(report: dict) -> str:
    rows = [
        row
        for row in report.get("collector_totals", [])
        if isinstance(row.get("total"), (int, float)) and row.get("collector")
    ]
    if not rows:
        return "Manual review"
    top = max(rows, key=lambda row: float(row["total"]))
    return f"{top['collector']} (${float(top['total']):,.2f})"


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
    if report.get("manual_review") is False and report.get("approved_at"):
        return True
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


def _enum_or_none(enum_type: type[Enum], value: str | None):
    if not value:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _bool_or_none(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes"}


def _date_or_none(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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
.brand-lockup {
  display: flex;
  align-items: center;
  gap: 14px;
}
.brand-logo-crop {
  position: relative;
  flex: 0 0 164px;
  width: 164px;
  height: 82px;
  overflow: hidden;
}
.brand-logo-crop img {
  position: absolute;
  width: 240px;
  max-width: none;
  height: auto;
  transform: translate(-38px, -73px);
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
button.danger { background: #9f2f2f; }
button.danger:hover { background: #7f1d1d; }
.review-action-form { min-width: 260px; display: grid; gap: 8px; }
.review-action-form label { display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
.review-action-form input[type="text"], .review-action-form input:not([type]) { width: 100%; }
a.button-link:hover { background: var(--brand-strong); }
button.secondary:hover, a.button-link.secondary:hover { background: #d7e1e7; }
a.button-link.small {
  min-height: 32px;
  padding: 0 10px;
  font-size: 13px;
}
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
.scheduled-off-banner {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #f2c783;
  border-radius: 8px;
  background: var(--soft-warn);
}
.scheduled-off-banner ul {
  margin: 8px 0 0;
  padding-left: 20px;
}
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
.cash-flow-dashboard {
  margin-bottom: 14px;
  border-top: 3px solid var(--brand);
}
.cash-review-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(320px, 1fr);
  gap: 14px;
  align-items: start;
  margin-top: 14px;
}
.cash-review-grid .cash-flow-dashboard {
  margin-bottom: 0;
  padding: 16px;
}
.cash-review-grid .forecast-horizon-card {
  padding: 10px;
}
.cash-review-grid .forecast-horizon-card strong {
  font-size: 19px;
}
.cash-review-grid .cash-flow-card {
  min-height: 72px;
  padding: 10px;
}
.cash-review-grid .cash-flow-card strong {
  font-size: 19px;
}
.needs-review-dashboard {
  border-top: 3px solid var(--warn);
  padding: 16px;
}
.needs-review-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 12px 0;
}
.needs-review-summary > div {
  min-height: 62px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.needs-review-summary span {
  display: block;
  color: var(--muted);
  font-size: 11px;
  margin-bottom: 5px;
}
.needs-review-summary strong {
  font-size: 18px;
}
.review-preview-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.review-preview-list li {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
.review-preview-list li > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.review-preview-list strong {
  font-size: 13px;
  line-height: 1.25;
}
.review-preview-list span,
.review-preview-list p {
  color: var(--muted);
  font-size: 11px;
}
.review-preview-list span {
  white-space: nowrap;
}
.review-preview-list p {
  margin-top: 4px;
  line-height: 1.3;
}
.forecast-horizon {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0 10px;
}
.forecast-horizon-card {
  padding: 13px;
  border: 1px solid var(--line);
  border-top-width: 3px;
  border-radius: 8px;
  background: #f8fafb;
}
.forecast-horizon-card.red { border-top-color: #b42318; }
.forecast-horizon-card.yellow { border-top-color: #c47a00; }
.forecast-horizon-card.green { border-top-color: var(--ok); }
.forecast-horizon-card span,
.forecast-payment-split span,
.forecast-filters label > span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.forecast-horizon-card strong {
  display: block;
  margin: 7px 0 3px;
  font-size: 21px;
}
.forecast-horizon-card small { color: var(--muted); }
.forecast-progress {
  height: 5px;
  margin-top: 11px;
  overflow: hidden;
  border-radius: 999px;
  background: #e5ebee;
}
.forecast-progress i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--brand);
}
.forecast-horizon-card.red .forecast-progress i { background: #b42318; }
.forecast-horizon-card.yellow .forecast-progress i { background: #c47a00; }
.forecast-horizon-card.green .forecast-progress i { background: var(--ok); }
.forecast-payment-split {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}
.forecast-payment-split > div {
  padding: 12px 14px;
  border-radius: 8px;
  background: #f0f6f8;
}
.forecast-payment-split strong {
  display: block;
  margin-top: 5px;
  font-size: 20px;
}
.forecast-filters {
  display: grid;
  grid-template-columns: repeat(4, minmax(130px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}
.forecast-filters select {
  width: 100%;
  min-height: 38px;
  margin-top: 4px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: white;
  color: var(--ink);
}
.badge.forecast-upcoming { color: var(--ok); background: var(--soft-ok); }
.badge.forecast-due-soon { color: var(--warn); background: var(--soft-warn); }
.badge.forecast-past-due { color: #9f1c13; background: #fde9e7; }
.badge.forecast-paid { color: var(--muted); background: #edf1f4; }
.forecast-row[hidden] { display: none; }
.forecast-row td:last-child { min-width: 130px; }
.cash-flow-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0;
}
.cash-flow-card {
  min-height: 82px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.cash-flow-card span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}
.cash-flow-card strong {
  display: block;
  font-size: 22px;
  overflow-wrap: anywhere;
}
.cash-flow-sections {
  display: grid;
  grid-template-columns: minmax(280px, 0.85fr) minmax(0, 1.4fr);
  gap: 14px;
  align-items: start;
}
.cash-flow-sections h3 {
  font-size: 16px;
  margin-bottom: 10px;
}
.cash-flow-attention-list {
  display: grid;
  gap: 10px;
}
.cash-flow-attention-item {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.cash-flow-attention-item strong,
.cash-flow-attention-item span {
  display: block;
}
.cash-flow-attention-item span {
  color: var(--muted);
  font-size: 13px;
  margin-top: 4px;
}
.cash-flow-attention-item p {
  margin-top: 8px;
  color: var(--ink);
  font-size: 13px;
  line-height: 1.35;
}
.agent-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
}
.intelligence-grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.75fr) minmax(0, 2.25fr);
  gap: 14px;
  align-items: start;
  margin-top: 14px;
}
.intelligence-grid .cash-flow-dashboard {
  margin-bottom: 0;
}
.operations-only-grid {
  grid-template-columns: 1fr;
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
.review-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(340px, 0.85fr);
  gap: 14px;
  align-items: start;
}
.review-shot {
  display: block;
  width: 100%;
  max-height: 680px;
  object-fit: contain;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.review-values {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.review-value {
  min-height: 88px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
}
.review-value.needs-review {
  border-color: #f2c783;
  background: var(--soft-warn);
}
.review-value span, .review-form-grid label span, .wide-field span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}
.review-value strong {
  display: block;
  font-size: 18px;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.review-value small {
  display: block;
  color: var(--muted);
  margin-top: 6px;
}
.review-form-card {
  margin-top: 14px;
}
.review-form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.top-performer-fields {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 12px;
}
input, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  font: inherit;
  color: var(--ink);
  background: white;
}
textarea {
  resize: vertical;
}
.wide-field {
  display: block;
  margin-top: 12px;
}
.actions form {
  margin: 0;
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
@media (max-width: 1020px) {
  .cash-review-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 860px) {
  .summary-grid, .agent-grid, .future-grid, .intelligence-grid, .ops-detail-grid, .ops-analytics-grid, .review-grid, .cash-flow-sections {
    grid-template-columns: 1fr;
  }
  .cash-flow-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .forecast-horizon {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .forecast-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ops-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ops-exec-kpis, .ops-trend-kpis, .chart-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .trend-grid, .review-form-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }
  .brand-logo-crop {
    flex-basis: 132px;
    width: 132px;
    height: 66px;
  }
  .brand-logo-crop img {
    width: 194px;
    transform: translate(-31px, -59px);
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
  .trend-grid, .review-form-grid, .review-values {
    grid-template-columns: 1fr;
  }
  .cash-flow-summary {
    grid-template-columns: 1fr;
  }
  .forecast-horizon, .forecast-payment-split, .forecast-filters {
    grid-template-columns: 1fr;
  }
  .needs-review-summary {
    grid-template-columns: 1fr;
  }
  table {
    font-size: 13px;
  }
}
"""
