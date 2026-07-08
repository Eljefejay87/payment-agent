from __future__ import annotations

import io
import sys
from pathlib import Path


BUNDLED_SITE_PACKAGES = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages"
)


def extract_text_from_pdf_bytes(content: bytes) -> str:
    PdfReader = load_pypdf_reader()
    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()


def load_pypdf_reader():
    try:
        from pypdf import PdfReader

        return PdfReader
    except ImportError:
        if BUNDLED_SITE_PACKAGES.exists():
            sys.path.append(str(BUNDLED_SITE_PACKAGES))
        from pypdf import PdfReader

        return PdfReader
