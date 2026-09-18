"""Celery run_build loads the request from Postgres and throttles DB polls."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.database import get_job_sync, save_job_sync
from app.models import BuildJob, BuildRequest, BuildStatus
from app.tasks import run_build
from tests.placeholders import placeholder_value


def _queued_job() -> BuildJob:
    guest = placeholder_value()
    req = BuildRequest(
        hostname="web01",
        ip="10.0.0.8",
        os_image="ubuntu-24.04",
    )
    req.vsphere_password = guest
    return BuildJob(
        id=str(uuid.uuid4()),
        status=BuildStatus.queued,
        request=req,
        requested_by="alice",
        created_at=datetime.now(timezone.utc),
    )


def test_run_build_loads_password_from_job_row(job_db, monkeypatch):
    job = _queued_job()
    save_job_sync(job)
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        proc = MagicMock()
        proc.stdout = iter(["building...\n", "assigned 10.0.0.8\n"])
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr("app.tasks.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.tasks.notify_job", lambda *_a, **_k: None)

    run_build.apply(args=(job.id,))
    stored = get_job_sync(job.id)
    assert stored is not None
    assert stored.status == BuildStatus.succeeded
    assert captured["env"]["VSPHERE_PASSWORD"] == placeholder_value()
    assert "--vsphere-password" not in str(captured)


def test_run_build_throttles_status_polls(job_db, monkeypatch):
    job = _queued_job()
    save_job_sync(job)
    polls = {"n": 0}
    real_get = get_job_sync

    def counting_get(job_id: str):
        polls["n"] += 1
        return real_get(job_id)

    frozen = time.monotonic()

    def fake_popen(cmd, **kwargs):
        proc = MagicMock()
        proc.stdout = iter([f"line {i}\n" for i in range(30)])
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr("app.tasks.get_job_sync", counting_get)
    monkeypatch.setattr("app.tasks.time.monotonic", lambda: frozen)
    monkeypatch.setattr("app.tasks.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.tasks.notify_job", lambda *_a, **_k: None)

    run_build.apply(args=(job.id,))
    # Initial load + first-line poll (last_poll=0) + 25-line poll + final check.
    # Per-line polling would be 30+ extra gets.
    assert polls["n"] < 10
    assert polls["n"] >= 3
