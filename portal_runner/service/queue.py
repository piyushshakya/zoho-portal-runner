"""Job queue and worker pool.

* Jobs are persisted first (SQLite), then queued in memory; on start-up, queued and
  interrupted jobs are loaded again, so a restart loses nothing.
* ``workers`` jobs run at the same time. Two jobs with the same ``job_key`` (e.g. the
  same invoice number) never run concurrently, so they cannot both create the invoice.
* The function that runs a job is injected: the real one drives a browser, tests pass a fake.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from portal_runner.core.record import RunRecord
from portal_runner.service.store import Job, JobStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    ok: bool
    document: str | None = None
    error: str | None = None


# run_job(job, on_record): on_record(record) is called as soon as the run has an id,
# so the API can show live stages while the job runs.
RunJob = Callable[[Job, Callable[[RunRecord], None]], Awaitable[JobResult]]


class QueueFullError(Exception):
    pass


class JobQueue:
    def __init__(self, store: JobStore, run_job: RunJob, *, workers: int = 2, max_queued: int = 100) -> None:
        self.store = store
        self.run_job = run_job
        self.workers = workers
        self.max_queued = max_queued
        self.live: dict[str, RunRecord] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._key_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        requeued = self.store.requeue_interrupted()
        if requeued:
            log.info("queue: %d interrupted job(s) put back in the queue", requeued)
        for job_id in self.store.queued_ids():
            self._queue.put_nowait(job_id)
        self._tasks = [asyncio.create_task(self._work(n), name=f"worker-{n}") for n in range(1, self.workers + 1)]
        log.info("queue: %d worker(s) started, %d job(s) waiting", self.workers, self._queue.qsize())

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def submit(self, portal: str, job_key: str, inputs: dict[str, Any]) -> Job:
        if self._queue.qsize() >= self.max_queued:
            raise QueueFullError(f"queue is full ({self.max_queued} jobs waiting); try again later")
        job = self.store.add(portal, job_key, inputs)
        self._queue.put_nowait(job.id)
        log.info("queue: job %s queued (%s)", job.id, job_key)
        return job

    def waiting(self) -> int:
        return self._queue.qsize()

    async def drain(self) -> None:
        """Wait until every queued job has finished (used by tests)."""
        await self._queue.join()

    async def _work(self, n: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_one(job_id, n)
            except Exception:  # a worker must survive any single job
                log.exception("queue: worker-%d crashed on job %s", n, job_id)
            finally:
                self._queue.task_done()

    async def _run_one(self, job_id: str, n: int) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        async with self._key_locks[job.job_key]:
            self.store.start(job.id)
            log.info("queue: worker-%d running job %s", n, job.id)

            def on_record(record: RunRecord) -> None:
                self.live[job.id] = record
                self.store.attach_run(job.id, record.id, str(record.dir))

            try:
                result = await self.run_job(job, on_record)
            except Exception as e:
                result = JobResult(ok=False, error=f"{type(e).__name__}: {e}")
            finally:
                self.live.pop(job.id, None)
            self.store.finish(job.id, ok=result.ok, document=result.document, error=result.error)
            log.info("queue: job %s %s", job.id, "succeeded" if result.ok else "failed")
