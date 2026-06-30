from __future__ import annotations

from pathlib import Path

from .models import RemitFiles


class RemitFileValidationError(ValueError):
    pass


def find_required_remit_files(
    folder: Path,
    remit_name_contains: str,
    liquidation_name_contains: str,
    allowed_extensions: tuple[str, ...],
) -> RemitFiles:
    folder.mkdir(parents=True, exist_ok=True)
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_extensions
    ]
    remit_files = _matching_files(files, remit_name_contains)
    liquidation_files = _matching_files(files, liquidation_name_contains)

    if not remit_files:
        raise RemitFileValidationError(f"Missing remit report containing '{remit_name_contains}'.")
    if not liquidation_files:
        raise RemitFileValidationError(f"Missing liquidation report containing '{liquidation_name_contains}'.")
    if len(remit_files) > 1:
        raise RemitFileValidationError(f"Multiple remit reports found containing '{remit_name_contains}'.")
    if len(liquidation_files) > 1:
        raise RemitFileValidationError(
            f"Multiple liquidation reports found containing '{liquidation_name_contains}'."
        )
    if remit_files[0] == liquidation_files[0]:
        raise RemitFileValidationError("Remit and liquidation report cannot be the same file.")

    return RemitFiles(remit=remit_files[0], liquidation=liquidation_files[0])


def _matching_files(files: list[Path], name_contains: str) -> list[Path]:
    needle = name_contains.lower()
    return sorted(path for path in files if needle in path.name.lower())

