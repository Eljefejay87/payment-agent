from __future__ import annotations

import html
import json
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
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(render_dashboard(self.dashboard_service.snapshot()))
            return
        if self.path == "/api/status":
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


def render_dashboard(snapshot: dict, banner: str = "") -> str:
    payment = snapshot["payment"]
    remit = snapshot["remit"]
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
  </script>
</body>
</html>"""


def _e(value: object) -> str:
    return html.escape(str(value))


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
button:hover { background: var(--brand-strong); }
button.secondary {
  background: #e8eef2;
  color: var(--ink);
}
button.secondary:hover { background: #d7e1e7; }
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
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
  align-items: start;
}
.agent-card {
  padding: 18px;
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
  .summary-grid, .agent-grid, .future-grid {
    grid-template-columns: 1fr;
  }
  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }
}
"""
