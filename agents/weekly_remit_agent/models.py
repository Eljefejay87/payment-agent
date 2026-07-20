from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemitFiles:
    remit: Path
    liquidation: Path

    @property
    def attachments(self) -> list[Path]:
        return [self.remit, self.liquidation]


@dataclass(frozen=True)
class RemitBatch:
    broker_name: str
    recipient_email: str
    week_start: str
    sent_date: str
    files: RemitFiles
    remit_hash: str
    liquidation_hash: str


@dataclass(frozen=True)
class RemitRunStatus:
    broker_name: str
    recipient_email: str
    remit_found: bool
    liquidation_found: bool
    attachments_sent: int
    send_time: str
    processing_seconds: float
    archive_result: str
    final_status: str
    dry_run: bool = False
