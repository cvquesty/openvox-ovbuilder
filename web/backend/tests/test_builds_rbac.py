"""JWT RBAC gaps on /api/builds that test_builds_api.py does not cover.

Viewer gates, unauthenticated 401, cancel-of-foreign-job, and 404.
Password is omitted from public payloads (BuildRequestPublic), not nulled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.database import save_job_sync
from app.models import BuildJob, BuildRequest, BuildStatus, Role

from tests.conftest import auth_header
from tests.placeholders import test_placeholder


def _build_payload() -> dict:
    payload = {
        "hostname": "web01",
        "ip": "10.0.0.8",
        "os_image": "ubuntu-24.04",
    }
    payload["vsphere_password"] = test_placeholder()
    return payload


class _FakeTask:
    id = "celery-task-test-1"


def _seed_job(*, owner: str, hostname: str = "other") -> BuildJob:
    return BuildJob(
        id=str(uuid4()),
        status=BuildStatus.queued,
        request=BuildRequest(
            hostname=hostname,
            ip="10.0.0.9",
            os_image="ubuntu-24.04",
            vsphere_password=test_placeholder(),
        ),
        requested_by=owner,
        created_at=datetime.now(timezone.utc),
    )


def test_unauthenticated_builds_are_unauthorized(client):
    listed = client.get("/api/builds")
    assert listed.status_code == 401
    created = client.post("/api/builds", json=_build_payload())
    assert created.status_code == 401


def test_viewer_cannot_submit_or_cancel(client, monkeypatch):
    monkeypatch.setattr("app.api.builds.run_build.delay", lambda *a, **k: _FakeTask())
    headers = auth_header("viewer1", Role.viewer)
    created = client.post("/api/builds", json=_build_payload(), headers=headers)
    assert created.status_code == 403

    job = _seed_job(owner="viewer1")
    save_job_sync(job)
    cancelled = client.post(f"/api/builds/{job.id}/cancel", headers=headers)
    assert cancelled.status_code == 403


def test_viewer_can_list_and_get_own_jobs(client):
    own = _seed_job(owner="viewer1", hostname="mine")
    other = _seed_job(owner="bob", hostname="bobs")
    save_job_sync(own)
    save_job_sync(other)

    headers = auth_header("viewer1", Role.viewer)
    listed = client.get("/api/builds", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert [r["id"] for r in rows] == [own.id]
    assert all("vsphere_password" not in r["request"] for r in rows)

    mine = client.get(f"/api/builds/{own.id}", headers=headers)
    assert mine.status_code == 200
    assert mine.json()["id"] == own.id
    assert "vsphere_password" not in mine.json()["request"]

    denied = client.get(f"/api/builds/{other.id}", headers=headers)
    assert denied.status_code == 403


def test_builder_cannot_cancel_someone_elses_job(client, monkeypatch):
    monkeypatch.setattr("app.api.builds.celery_app.control.revoke", lambda *a, **k: None)
    other = _seed_job(owner="bob")
    save_job_sync(other)
    headers = auth_header("alice", Role.builder)
    cancelled = client.post(f"/api/builds/{other.id}/cancel", headers=headers)
    assert cancelled.status_code == 403


def test_missing_job_is_not_found(client):
    headers = auth_header("root", Role.admin)
    res = client.get("/api/builds/does-not-exist", headers=headers)
    assert res.status_code == 404
