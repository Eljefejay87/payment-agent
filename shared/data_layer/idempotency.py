from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def generate_idempotency_key(namespace: str, *components: Any) -> str:
    if not namespace.strip():
        raise ValueError("namespace is required.")
    canonical = [_canonical_value(component) for component in components]
    payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{namespace.strip().lower()}:{digest}"


def cash_flow_idempotency_key(
    vendor: str,
    amount: Decimal | None,
    due_date: date | None,
    source_identifier: str,
) -> str:
    return generate_idempotency_key("cash_flow_hq", vendor, amount, due_date, source_identifier)


def icr_remit_idempotency_key(
    remit_identity: str,
    remit_date: date,
    total_collected: Decimal,
    source_file_identity: str,
) -> str:
    return generate_idempotency_key(
        "icr_remit",
        remit_identity,
        remit_date,
        total_collected,
        source_file_identity,
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal idempotency components must be finite.")
        normalized = format(value.normalize(), "f")
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime idempotency components must be timezone-aware.")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return value.name.casefold()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return " ".join(value.strip().casefold().split())
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, set):
        return sorted((_canonical_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value
