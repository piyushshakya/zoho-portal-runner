"""HTTP API and UI.

POST /api/jobs                 submit an inputs document (same JSON as inputs.json) -> 202
GET  /api/jobs                 recent jobs
GET  /api/jobs/{id}            one job, with its stages (live while it runs)
GET  /api/jobs/{id}/manifest   the run's manifest.json
GET  /api/jobs/{id}/document   the captured PDF
GET  /                         the UI;  /docs: interactive API documentation
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from portal_runner.core.errors import ConfigError, ExitCode, InputError
from portal_runner.core.record import RunRecord
from portal_runner.portals import get_portal
from portal_runner.runner import Runner, RunOptions
from portal_runner.service.queue import JobQueue, JobResult, QueueFullError, RunJob
from portal_runner.service.store import Job

Validate = Callable[[dict[str, Any]], tuple[str, str]]  # inputs -> (portal name, job key)


class Stage(BaseModel):
    key: str
    status: str
    attempt: int | None = None
    duration_ms: int | None = None
    error: str | None = None


class JobOut(BaseModel):
    id: str
    status: str
    portal: str
    job_key: str
    inputs: dict[str, Any]
    created_at: str
    started_at: str | None
    finished_at: str | None
    run_id: str | None
    error: str | None
    document_url: str | None
    manifest_url: str | None
    stages: list[Stage]


def validate_inputs(inputs: dict[str, Any]) -> tuple[str, str]:
    """Reject bad inputs at submission time, before anything is queued."""
    if not isinstance(inputs.get("portal"), str):
        raise InputError('inputs must have a "portal" name')
    portal = get_portal(inputs["portal"]).from_env(inputs)
    return portal.name, portal.job_key


def portal_job_runner(options: RunOptions) -> RunJob:
    """The real job: one Runner per job; jobs of one portal account share one session lock."""
    session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def run(job: Job, on_record: Callable[[RunRecord], None]) -> JobResult:
        portal = get_portal(job.portal).from_env(job.inputs)
        runner = Runner(portal, job.inputs, options, session_lock=session_locks[job.portal])
        on_record(runner.record)
        code = await runner.run()
        manifest = runner.record.manifest
        return JobResult(ok=code == ExitCode.OK, document=manifest.get("document"), error=manifest.get("error"))

    return run


def _manifest(job: Job) -> dict[str, Any] | None:
    path = Path(job.run_dir or "") / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if job.run_dir and path.is_file() else None


def create_app(queue: JobQueue, *, out_dir: Path, validate: Validate = validate_inputs) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await queue.start()
        yield
        await queue.stop()

    app = FastAPI(title="Portal Runner", version="1.0", lifespan=lifespan)

    def job_out(job: Job, with_stages: bool) -> JobOut:
        stages: list[dict[str, Any]] = []
        if with_stages:
            record = queue.live.get(job.id)
            source = record.manifest if record else _manifest(job) or {}
            stages = source.get("stages", [])
        done = job.status in ("succeeded", "failed")
        return JobOut(
            **{k: v for k, v in vars(job).items() if k not in ("run_dir", "document")},
            document_url=f"/api/jobs/{job.id}/document" if job.document else None,
            manifest_url=f"/api/jobs/{job.id}/manifest" if done and job.run_dir else None,
            stages=[Stage.model_validate({k: s.get(k) for k in Stage.model_fields}) for s in stages],
        )

    def find(job_id: str) -> Job:
        job = queue.store.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        return job

    @app.post("/api/jobs", status_code=202, response_model=JobOut)
    async def submit(request: Request) -> JobOut:
        try:
            inputs = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "body must be JSON") from None
        if not isinstance(inputs, dict):
            raise HTTPException(422, "body must be a JSON object")
        try:
            portal, job_key = validate(inputs)
        except InputError as e:
            raise HTTPException(422, str(e)) from None
        except ConfigError as e:
            raise HTTPException(503, f"service is not configured: {e}") from None
        try:
            return job_out(queue.submit(portal, job_key, inputs), with_stages=False)
        except QueueFullError as e:
            raise HTTPException(429, str(e)) from None

    @app.get("/api/jobs", response_model=list[JobOut])
    async def recent(limit: int = 50) -> list[JobOut]:
        jobs = queue.store.recent(min(limit, 200))
        return [job_out(job, with_stages=job.status in ("running", "failed")) for job in jobs]

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    async def one(job_id: str) -> JobOut:
        return job_out(find(job_id), with_stages=True)

    @app.get("/api/jobs/{job_id}/manifest")
    async def manifest(job_id: str) -> JSONResponse:
        data = _manifest(find(job_id))
        if data is None:
            raise HTTPException(404, "no manifest yet")
        return JSONResponse(data)

    @app.get("/api/jobs/{job_id}/document")
    async def document(job_id: str) -> FileResponse:
        job = find(job_id)
        path = Path(job.document or "").resolve()
        if not job.document or not path.is_file() or not path.is_relative_to(out_dir.resolve()):
            raise HTTPException(404, "no document for this job")
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "workers": queue.workers, "waiting": queue.waiting(), "running": len(queue.live)}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def ui() -> str:
        return (files("portal_runner.service") / "static" / "index.html").read_text(encoding="utf-8")

    return app
