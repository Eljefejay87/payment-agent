from __future__ import annotations

import argparse
import logging
import re
import socket
import subprocess
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

    local_url, lan_url, tailscale_url = _dashboard_urls(settings.port)
    logging.info("UCM Admin Dashboard starting on %s:%s", settings.host, settings.port)
    logging.info("Local URL: %s", local_url)
    logging.info("LAN URL: %s", lan_url)
    logging.info("Tailscale URL: %s", tailscale_url)
    logging.info("Shared data database: %s", settings.shared_database_path)
    if not args.no_browser:
        webbrowser.open(local_url)

    try:
        DashboardServer(
            host=settings.host,
            port=settings.port,
            service=DashboardService(payment_settings, remit_settings, settings),
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
        pass
    for interface in ("en0", "en1"):
        ip = _interface_ip(interface)
        if ip:
            return ip
    return "127.0.0.1"


def _dashboard_urls(port: int) -> tuple[str, str, str]:
    local_url = f"http://127.0.0.1:{port}"
    local_ip = _local_ip()
    lan_url = f"http://{local_ip}:{port}" if local_ip != "127.0.0.1" else "Unavailable"
    tailscale_ip = _tailscale_ip()
    tailscale_url = f"http://{tailscale_ip}:{port}" if tailscale_ip else "Unavailable"
    return local_url, lan_url, tailscale_url


def _tailscale_ip() -> str:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if _is_ipv4(candidate):
            return candidate
    return ""


def _interface_ip(interface: str) -> str:
    try:
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    candidate = result.stdout.strip()
    if _is_ipv4(candidate):
        return candidate
    return _ifconfig_ip(interface)


def _ifconfig_ip(interface: str) -> str:
    try:
        result = subprocess.run(
            ["ifconfig", interface],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)\b", result.stdout)
    return match.group(1) if match else ""


def _is_ipv4(value: str) -> bool:
    return bool(re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value))


if __name__ == "__main__":
    sys.exit(main())
