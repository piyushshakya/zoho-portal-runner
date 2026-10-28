"""One run's record: stage timings, pages visited, documents -> runs/<id>/manifest.json."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from portal_runner import __version__
from portal_runner.core.redact import Redactor
from portal_runner.core.retry import with_retries

log = logging.getLogger(__name__)
T = TypeVar("T")

MANIFEST_SCHEMA = 1
MAX_ERROR_CHARS = 2000


class RunRecord:
    def __init__(self, root: Path, portal: str, inputs: dict[str, Any], redactor: Redactor) -> None:
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = Path(root) / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor
        self.console: list[str] = []
        self._started = time.monotonic()
        self._stage: str | None = None
        self.manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "tool_version": __version__,
            "run_id": self.id,
            "portal": portal,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "inputs": inputs,
            "session": {},
            "stages": [],
            "pages_visited": [],
            "dependent_fields": [],
            "documents": [],
            "outcome": "running",
        }

    @property
    def elapsed_ms(self) -> int:
        return round((time.monotonic() - self._started) * 1000)

    def error_text(self, e: BaseException) -> str:
        return self.redactor.text(f"{type(e).__name__}: {e}")[:MAX_ERROR_CHARS]

    @asynccontextmanager
    async def stage(self, key: str) -> AsyncIterator[dict[str, Any]]:
        entry: dict[str, Any] = {"key": key, "status": "running"}
        self.manifest["stages"].append(entry)
        outer, self._stage = self._stage, key
        started = time.monotonic()
        log.info("stage %-22s start", key)
        try:
            yield entry
            entry["status"] = "ok"
        except BaseException as e:
            entry["status"], entry["error"] = "failed", self.error_text(e)
            raise
        finally:
            entry["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info("stage %-22s %s in %.1fs", key, entry["status"], entry["duration_ms"] / 1000)
            self._stage = outer

    async def step(
        self,
        key: str,
        fn: Callable[[], Awaitable[T]],
        *,
        attempts: int = 1,
        reset: Callable[[], Awaitable[None]] | None = None,
    ) -> T:
        """Run ``fn`` as a stage, retrying retryable failures; each attempt is its own entry."""

        async def attempt(n: int) -> T:
            async with self.stage(key) as entry:
                entry["attempt"] = n
                return await fn()

        return await with_retries(attempt, what=key, attempts=attempts, reset=reset)

    def last_failed_stage(self) -> dict[str, Any] | None:
        return next((s for s in reversed(self.manifest["stages"]) if s["status"] == "failed"), None)

    def visit(self, page: str, url: str) -> None:
        self.manifest["pages_visited"].append(
            {"page": page, "url": self.redactor.url(url), "stage": self._stage, "t_ms": self.elapsed_ms}
        )

    def add_document(self, path: Path, captured_via: str, **extra: Any) -> dict[str, Any]:
        data = Path(path).read_bytes()
        doc = {
            "path": str(path),
            "captured_via": captured_via,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "is_pdf": data.startswith(b"%PDF-"),
            **extra,
        }
        self.manifest["documents"].append(doc)
        return doc

    def finish(self, outcome: str, **extra: Any) -> Path:
        self.manifest.update(outcome=outcome, total_ms=self.elapsed_ms, **extra)
        path = self.dir / "manifest.json"
        path.write_text(json.dumps(self.redactor.obj(self.manifest), indent=2, ensure_ascii=False), encoding="utf-8")
        return path
