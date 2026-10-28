"""Job service with a fake job runner: no browser, no portal."""

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portal_runner.core.errors import ConfigError, InputError
from portal_runner.core.record import RunRecord
from portal_runner.core.redact import Redactor
from portal_runner.service.app import create_app
from portal_runner.service.queue import JobQueue, JobResult
from portal_runner.service.store import FAILED, QUEUED, RUNNING, SUCCEEDED, JobStore

GOOD = {"portal": "fake", "key": "A"}


class FakeRunner:
    """Records concurrency; writes a manifest and a document like a real run."""

    def __init__(self, tmp: Path, delay: float = 0.2) -> None:
        self.tmp, self.delay = tmp, delay
        self.running: dict[str, int] = {}
        self.peak = 0
        self.peak_per_key: dict[str, int] = {}

    async def __call__(self, job, on_record) -> JobResult:
        if job.inputs.get("explode"):
            raise RuntimeError("boom")
        record = RunRecord(self.tmp / "runs", job.portal, job.inputs, Redactor())
        on_record(record)
        self.running[job.job_key] = self.running.get(job.job_key, 0) + 1
        self.peak = max(self.peak, sum(self.running.values()))
        self.peak_per_key[job.job_key] = max(self.peak_per_key.get(job.job_key, 0), self.running[job.job_key])
        async with record.stage("work"):
            await asyncio.sleep(self.delay)
        self.running[job.job_key] -= 1
        document = self.tmp / "output" / f"{job.id}.pdf"
        document.parent.mkdir(exist_ok=True)
        document.write_bytes(b"%PDF-1.4 fake")
        record.finish("success", document=str(document))
        return JobResult(ok=True, document=str(document))


def validate(inputs: dict) -> tuple[str, str]:
    if inputs.get("bad"):
        raise InputError("invoice.number is required")
    if inputs.get("unconfigured"):
        raise ConfigError("missing in .env: ZOHO_EMAIL")
    return "fake", f"fake:{inputs.get('key', 'A')}"


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "jobs.db")
    yield s
    s.close()


# ---------- store ----------
def test_store_round_trip(store):
    job = store.add("fake", "fake:A", {"x": 1})
    store.start(job.id)
    store.attach_run(job.id, "run-1", "runs/run-1")
    store.finish(job.id, ok=True, document="out.pdf", error=None)
    saved = store.get(job.id)
    assert (saved.status, saved.inputs, saved.run_id, saved.document) == (SUCCEEDED, {"x": 1}, "run-1", "out.pdf")
    assert store.recent()[0].id == job.id


def test_interrupted_jobs_are_requeued(store):
    job = store.add("fake", "fake:A", {})
    store.start(job.id)
    assert store.requeue_interrupted() == 1
    assert store.get(job.id).status == QUEUED
    assert store.queued_ids() == [job.id]


# ---------- queue ----------
async def test_jobs_run_in_parallel_but_never_twice_for_one_key(store, tmp_path):
    fake = FakeRunner(tmp_path)
    queue = JobQueue(store, fake, workers=3)
    await queue.start()
    for key in ["A", "A", "B", "C"]:
        queue.submit("fake", f"fake:{key}", {"key": key})
    started = time.monotonic()
    await queue.drain()
    await queue.stop()
    assert fake.peak >= 2  # different invoices overlap
    assert fake.peak_per_key["fake:A"] == 1  # the same invoice never does
    assert time.monotonic() - started < 4 * fake.delay  # not fully serial
    assert {j.status for j in store.recent()} == {SUCCEEDED}


async def test_a_crashing_job_fails_alone(store, tmp_path):
    queue = JobQueue(store, FakeRunner(tmp_path, delay=0), workers=1)
    await queue.start()
    bad = queue.submit("fake", "fake:X", {"explode": True})
    good = queue.submit("fake", "fake:Y", {})
    await queue.drain()
    await queue.stop()
    assert store.get(bad.id).status == FAILED
    assert "boom" in store.get(bad.id).error
    assert store.get(good.id).status == SUCCEEDED


async def test_queued_jobs_survive_a_restart(tmp_path):
    first = JobStore(tmp_path / "jobs.db")
    job = first.add("fake", "fake:A", {})
    first.start(job.id)  # the service died while this job was running
    first.close()
    store = JobStore(tmp_path / "jobs.db")
    queue = JobQueue(store, FakeRunner(tmp_path, delay=0), workers=1)
    await queue.start()
    await queue.drain()
    await queue.stop()
    assert store.get(job.id).status == SUCCEEDED
    store.close()


# ---------- HTTP API ----------
@pytest.fixture
def client(store, tmp_path):
    queue = JobQueue(store, FakeRunner(tmp_path), workers=2, max_queued=3)
    with TestClient(create_app(queue, out_dir=tmp_path / "output", validate=validate)) as c:
        yield c


def wait_for(client, job_id: str, status: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {status}")


def test_submit_run_and_download(client):
    created = client.post("/api/jobs", json=GOOD)
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["status"] in (QUEUED, RUNNING)
    done = wait_for(client, job_id, SUCCEEDED)
    assert [s["key"] for s in done["stages"]] == ["work"]
    assert client.get(done["document_url"]).content.startswith(b"%PDF")
    assert client.get(done["manifest_url"]).json()["outcome"] == "success"


def test_live_stages_while_running(client):
    job_id = client.post("/api/jobs", json=GOOD).json()["id"]
    running = wait_for(client, job_id, RUNNING)
    assert running["stages"][0]["status"] == "running"


@pytest.mark.parametrize(
    ("body", "code"),
    [({"bad": True}, 422), ({"unconfigured": True}, 503), ([1, 2], 422)],
)
def test_rejected_submissions(client, body, code):
    response = client.post("/api/jobs", json=body)
    assert response.status_code == code


def test_non_json_body(client):
    assert client.post("/api/jobs", content=b"not json").status_code == 400


def test_queue_full(client):
    codes = [client.post("/api/jobs", json={"key": str(i)}).status_code for i in range(8)]
    assert 429 in codes


def test_documents_outside_the_output_folder_are_never_served(client, store, tmp_path):
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF private")
    job = store.add("fake", "fake:Z", {})
    store.finish(job.id, ok=True, document=str(secret), error=None)
    assert client.get(f"/api/jobs/{job.id}/document").status_code == 404


def test_unknown_job(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_ui_and_health(client):
    assert "Portal Runner" in client.get("/").text
    assert client.get("/api/health").json()["status"] == "ok"
