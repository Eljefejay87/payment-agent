from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class ICRRemitResult:
    broker: str
    contact: str
    remit_week: date
    week_ending: date
    file_path: Path
    due_to_agency: Decimal
    due_to_client: Decimal
    total_collected: Decimal
    status: str = "Pending"
    notes: str = ""

