from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path: str | None = None) -> bool:
        return False


def load_environment(env_file: str | None = None) -> None:
    load_dotenv(env_file)


def get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def get_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))
