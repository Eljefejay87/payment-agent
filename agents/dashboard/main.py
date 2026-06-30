from __future__ import annotations

import argparse
import logging
import socket
import sys
import webbrowser

from agents.payment_agent.config import load_settings
from agents.weekly_remit_agent.config import load_remit_settings
from shared.logging import configure_logging

from .config import load_dashboard_settings
from .service import DashboardService
from .web import DashboardServer


def main() -> int:
    parser = argparse.ArgumentParser(description="UCM Admin Dashboard")
    parser.add_argument("command", choices=["dashboard"], help="Start the local dashboard.")
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
    args = parser.parse_args()

    settings = load_dashboard_settings(args.env_file)
    payment_settings = load_settings(args.env_file)
    remit_settings = load_remit_settings(args.env_file)
    configure_logging(settings.log_level)

    url = f"http://{settings.host}:{settings.port}"
    logging.info("UCM Admin Dashboard starting at %s", url)
    if settings.host in {"0.0.0.0", "::"}:
        logging.info("Local network URL may be http://%s:%s", _local_ip(), settings.port)
    if not args.no_browser:
        webbrowser.open(url)

    try:
        DashboardServer(
            host=settings.host,
            port=settings.port,
            service=DashboardService(payment_settings, remit_settings),
        ).serve_forever()
    except KeyboardInterrupt:
        logging.info("UCM Admin Dashboard stopped")
    return 0


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    sys.exit(main())
