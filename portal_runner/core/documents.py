"""Read captured PDFs. Bytes differ per download (generation timestamps), so "same document"
is decided by a fingerprint of the text content."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pypdf import PdfReader


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(flatten(text).encode()).hexdigest()
