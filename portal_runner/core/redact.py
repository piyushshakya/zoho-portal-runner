"""Scrub secrets and account identifiers from everything written to disk."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
APP_PATH_ID = re.compile(r"(/app/)\d+")
# 10+ digit ids, also glued to letters or URL-encoded; not inside lowercase-hex hashes.
LONG_ID = re.compile(r"(?<![\da-f])\d{10,}(?![\da-f])")
ENCODED_ID = re.compile(r"(?<=%[0-9A-Fa-f]{2})\d{10,}")
MIN_SECRET_LEN = 4


class Redactor:
    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        self._secrets: set[str] = set()
        self._pattern: re.Pattern[str] | None = None
        for s in secrets:
            self.add(s)

    @property
    def secrets(self) -> list[str]:
        return sorted(self._secrets, key=len, reverse=True)

    def add(self, secret: str | None) -> None:
        """Register a value that must never appear in output (e.g. a TOTP code just used)."""
        secret = (secret or "").strip()
        if len(secret) < MIN_SECRET_LEN:
            return
        self._secrets.add(secret)
        local, at, _ = secret.partition("@")
        if at and len(local) >= MIN_SECRET_LEN:
            self._secrets.add(local)
        self._pattern = re.compile("|".join(map(re.escape, self.secrets)), re.IGNORECASE)

    def text(self, s: str) -> str:
        if self._pattern:
            s = self._pattern.sub("[REDACTED]", s)
        s = EMAIL.sub("[EMAIL]", s)
        s = APP_PATH_ID.sub(r"\1[ORG]", s)
        s = ENCODED_ID.sub("[ID]", s)
        return LONG_ID.sub("[ID]", s)

    def url(self, url: str) -> str:
        """Redact a URL and drop query strings (they often carry tokens)."""
        base, _, fragment = url.partition("#")
        return self.text(base.split("?")[0] + ("#" + fragment.split("?")[0] if fragment else ""))

    def obj(self, value: Any) -> Any:
        """Redact every string inside a JSON-like structure."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.obj(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.obj(v) for v in value]
        return value


class RedactingFilter(logging.Filter):
    """Defense in depth: scrub every log line, even though we never log secrets on purpose."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = self.redactor.text(record.getMessage()), None
        return True
