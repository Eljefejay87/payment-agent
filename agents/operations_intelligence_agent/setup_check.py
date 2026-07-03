from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import OperationsSettings
from .database import OperationsDatabase


@dataclass(frozen=True)
class SetupCheck:
    name: str
    passed: bool
    message: str
    fix: str = ""


REQUIRED_ENV_FIELDS: tuple[tuple[str, str], ...] = (
    ("TEAMS_GRAPH_TENANT_ID or MS_GRAPH_TENANT_ID", "graph_tenant_id"),
    ("TEAMS_GRAPH_CLIENT_ID or MS_GRAPH_CLIENT_ID", "graph_client_id"),
    ("OPS_LEADERSHIP_CHAT_ID or TEAMS_CHAT_ID", "leadership_chat_id"),
)


def run_setup_checks(settings: OperationsSettings) -> list[SetupCheck]:
    checks: list[SetupCheck] = []
    checks.extend(_check_required_env(settings))
    checks.append(_check_database(settings.database_path))
    checks.append(_check_folder(settings.screenshots_dir, "Screenshot folder"))
    checks.append(_check_folder(settings.reports_dir, "Reports folder"))
    checks.append(_check_tesseract(settings.ocr_command))
    checks.append(_check_token_cache(settings.teams_graph_token_cache_path))
    checks.append(_check_chat_id(settings.leadership_chat_id))
    return checks


def format_setup_checks(checks: list[SetupCheck]) -> str:
    lines = ["Operations Intelligence setup check", ""]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"[{status}] {check.name}: {check.message}")
        if not check.passed and check.fix:
            lines.append(f"       Fix: {check.fix}")
    failed = sum(1 for check in checks if not check.passed)
    lines.append("")
    if failed:
        lines.append(f"Result: {failed} item(s) need attention before the first live test.")
    else:
        lines.append("Result: setup looks ready for the first live test.")
    return "\n".join(lines)


def _check_required_env(settings: OperationsSettings) -> list[SetupCheck]:
    checks: list[SetupCheck] = []
    for env_name, field_name in REQUIRED_ENV_FIELDS:
        value = getattr(settings, field_name)
        checks.append(
            SetupCheck(
                name=env_name,
                passed=bool(str(value).strip()),
                message="configured" if str(value).strip() else "missing",
                fix=f"Add {env_name} to .env.",
            )
        )
    return checks


def _check_database(database_path: Path) -> SetupCheck:
    try:
        OperationsDatabase(database_path).initialize()
        return SetupCheck(
            name="Database",
            passed=True,
            message=f"ready at {database_path}",
        )
    except Exception as exc:
        return SetupCheck(
            name="Database",
            passed=False,
            message=f"could not create or open {database_path}: {exc}",
            fix="Check DATABASE_PATH and folder permissions.",
        )


def _check_folder(folder: Path, name: str) -> SetupCheck:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        test_file = folder / ".ops-write-test"
        test_file.write_text("ok")
        test_file.unlink(missing_ok=True)
        return SetupCheck(name=name, passed=True, message=f"ready at {folder}")
    except Exception as exc:
        return SetupCheck(
            name=name,
            passed=False,
            message=f"could not write to {folder}: {exc}",
            fix=f"Check the path for {name} and folder permissions.",
        )


def _check_tesseract(command: str) -> SetupCheck:
    executable = shutil.which(command) if not Path(command).is_absolute() else command
    if not executable or not Path(executable).exists():
        return SetupCheck(
            name="Tesseract OCR",
            passed=False,
            message=f"{command} was not found",
            fix="Install it with: brew install tesseract. If needed, set OPS_OCR_COMMAND=/opt/homebrew/bin/tesseract.",
        )
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else str(executable)
        return SetupCheck(name="Tesseract OCR", passed=True, message=first_line)
    except Exception as exc:
        return SetupCheck(
            name="Tesseract OCR",
            passed=False,
            message=f"{executable} is installed but did not run: {exc}",
            fix="Try running tesseract --version, then update OPS_OCR_COMMAND if the path is different.",
        )


def _check_token_cache(token_cache_path: Path) -> SetupCheck:
    if token_cache_path.exists() and token_cache_path.stat().st_size > 0:
        return SetupCheck(
            name="Microsoft Graph token cache",
            passed=True,
            message=f"found at {token_cache_path}",
        )
    return SetupCheck(
        name="Microsoft Graph token cache",
        passed=False,
        message=f"not found at {token_cache_path}",
        fix="Run: python main.py ops-auth",
    )


def _check_chat_id(chat_id: str) -> SetupCheck:
    if chat_id.strip():
        return SetupCheck(name="Teams leadership chat id", passed=True, message="configured")
    return SetupCheck(
        name="Teams leadership chat id",
        passed=False,
        message="missing",
        fix="Add OPS_LEADERSHIP_CHAT_ID to .env.",
    )
