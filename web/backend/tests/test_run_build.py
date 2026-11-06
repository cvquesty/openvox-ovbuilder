"""Celery run_build loads the request from Postgres and throttles DB polls."""

from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

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


_FAKE_OVBUILDER = """
import signal, subprocess, sys, time
if sys.argv[2] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
# Stand-in for terraform: a grandchild of the worker.
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
print("terraform apply started", flush=True)
with open(sys.argv[1], "w") as fh:
    fh.write(str(child.pid))
# Silent from here on: cancel must not depend on new output.
time.sleep(300)
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
@pytest.mark.parametrize("sigterm", ["honor", "ignore"])
def test_cancel_kills_build_process_group_and_marks_cancelled(
    job_db, monkeypatch, tmp_path, sigterm
):
    job = _queued_job()
    save_job_sync(job)
    script = tmp_path / "fake_ovbuilder.py"
    script.write_text(_FAKE_OVBUILDER)
    pid_file = tmp_path / "terraform.pid"

    monkeypatch.setattr(
        "app.tasks._build_command",
        lambda req, job_id: (
            [sys.executable, str(script), str(pid_file), sigterm],
            dict(os.environ),
        ),
    )
    monkeypatch.setattr("app.tasks.settings.ovbuilder_home", str(tmp_path))
    monkeypatch.setattr("app.tasks.settings.build_cancel_grace_seconds", 1)
    monkeypatch.setattr("app.tasks._STATUS_POLL_SECONDS", 0.1)
    monkeypatch.setattr("app.tasks.notify_job", lambda *_a, **_k: None)

    real_get = get_job_sync

    def get_with_cancel(job_id: str):
        latest = real_get(job_id)
        # Simulate POST /cancel once terraform is running.
        if latest is not None and pid_file.exists() and pid_file.read_text():
            latest.status = BuildStatus.cancelling
            save_job_sync(latest)
        return latest

    monkeypatch.setattr("app.tasks.get_job_sync", get_with_cancel)

    started = time.monotonic()
    run_build.apply(args=(job.id,))
    assert time.monotonic() - started < 30

    stored = real_get(job.id)
    assert stored is not None
    assert stored.status == BuildStatus.cancelled
    assert stored.finished_at is not None
    assert "terraform apply started" in (stored.log_tail or "")

    terraform_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(terraform_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(terraform_pid)


def test_run_build_starts_build_in_own_session(job_db, monkeypatch):
    job = _queued_job()
    save_job_sync(job)
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        proc = MagicMock()
        proc.stdout = iter(["ok\n"])
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr("app.tasks.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.tasks.notify_job", lambda *_a, **_k: None)

    run_build.apply(args=(job.id,))
    assert captured["start_new_session"] is (os.name == "posix")
