"""SQLite job store: jobs survive a restart of the service."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    portal TEXT NOT NULL,
    job_key TEXT NOT NULL,
    inputs TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    run_id TEXT,
    run_dir TEXT,
    document TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs (status, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    id: str
    status: str
    portal: str
    job_key: str
    inputs: dict[str, Any]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    run_id: str | None = None
    run_dir: str | None = None
    document: str | None = None
    error: str | None = None


class JobStore:
    def __init__(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)

    def add(self, portal: str, job_key: str, inputs: dict[str, Any]) -> Job:
        job = Job(uuid.uuid4().hex[:12], QUEUED, portal, job_key, inputs, _now())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO jobs (id, status, portal, job_key, inputs, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job.id, job.status, job.portal, job.job_key, json.dumps(inputs), job.created_at),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def recent(self, limit: int = 50) -> list[Job]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
            return [_job(r) for r in rows.fetchall()]

    def queued_ids(self) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT id FROM jobs WHERE status = ? ORDER BY created_at, rowid", (QUEUED,))
            return [r["id"] for r in rows.fetchall()]

    def start(self, job_id: str) -> None:
        self._update(job_id, status=RUNNING, started_at=_now())

    def attach_run(self, job_id: str, run_id: str, run_dir: str) -> None:
        self._update(job_id, run_id=run_id, run_dir=run_dir)

    def finish(self, job_id: str, *, ok: bool, document: str | None, error: str | None) -> None:
        self._update(job_id, status=SUCCEEDED if ok else FAILED, finished_at=_now(), document=document, error=error)

    def requeue_interrupted(self) -> int:
        """Jobs that were running when the service stopped go back to the queue (flows are idempotent)."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE jobs SET status = ?, started_at = NULL WHERE status = ?", (QUEUED, RUNNING)
            )
            return cursor.rowcount

    def close(self) -> None:
        self._db.close()

    def _update(self, job_id: str, **fields: str | None) -> None:
        columns = ", ".join(f"{name} = ?" for name in fields)  # names come from this module only
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))  # noqa: S608


def _job(row: sqlite3.Row) -> Job:
    data = dict(row)
    data["inputs"] = json.loads(data["inputs"])
    return Job(**data)
