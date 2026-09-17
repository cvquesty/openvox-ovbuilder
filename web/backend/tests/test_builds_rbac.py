"""RBAC gates on /api/builds: admin / builder / viewer + unauthenticated."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.database import save_job
from app.models import BuildJob, BuildRequest, BuildStatus, Role

from tests.conftest import auth_header

BUILD_PAYLOAD = {
    "hostname": "web01",
    "ip": "10.0.0.8",
    "os_image": "ubuntu-24.04",
    "vsphere_password": "s3cret",
}


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
            vsphere_password="other-secret",
        ),
        requested_by=owner,
        created_at=datetime.now(timezone.utc),
    )


async def test_unauthenticated_builds_are_unauthorized(client):
    listed = await client.get("/api/builds")
    assert listed.status_code == 401
    created = await client.post("/api/builds", json=BUILD_PAYLOAD)
    assert created.status_code == 401


async def test_viewer_cannot_submit_or_cancel(client, monkeypatch):
    monkeypatch.setattr("app.api.builds.run_build.delay", lambda *a, **k: _FakeTask())
    headers = auth_header("viewer1", Role.viewer)
    created = await client.post("/api/builds", json=BUILD_PAYLOAD, headers=headers)
    assert created.status_code == 403

    job = _seed_job(owner="viewer1")
    await save_job(job)
    cancelled = await client.post(f"/api/builds/{job.id}/cancel", headers=headers)
    assert cancelled.status_code == 403


async def test_viewer_can_list_and_get_own_jobs(client):
    own = _seed_job(owner="viewer1", hostname="mine")
    other = _seed_job(owner="bob", hostname="bobs")
    await save_job(own)
    await save_job(other)

    headers = auth_header("viewer1", Role.viewer)
    listed = await client.get("/api/builds", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert [r["id"] for r in rows] == [own.id]
    assert all(r["request"]["vsphere_password"] is None for r in rows)

    mine = await client.get(f"/api/builds/{own.id}", headers=headers)
    assert mine.status_code == 200
    assert mine.json()["id"] == own.id

    denied = await client.get(f"/api/builds/{other.id}", headers=headers)
    assert denied.status_code == 403


async def test_builder_can_submit_and_sees_only_own_jobs(client, monkeypatch):
    monkeypatch.setattr("app.api.builds.run_build.delay", lambda *a, **k: _FakeTask())
    await save_job(_seed_job(owner="bob", hostname="bobs"))

    headers = auth_header("alice", Role.builder)
    created = await client.post("/api/builds", json=BUILD_PAYLOAD, headers=headers)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["requested_by"] == "alice"
    assert body["celery_task_id"] == _FakeTask.id
    assert body["request"]["vsphere_password"] is None
    assert "s3cret" not in created.text

    listed = await client.get("/api/builds", headers=headers)
    assert listed.status_code == 200
    ids = {row["id"] for row in listed.json()}
    assert body["id"] in ids
    assert len(ids) == 1


async def test_builder_cannot_read_or_cancel_someone_elses_job(client, monkeypatch):
    monkeypatch.setattr("app.api.builds.celery_app.control.revoke", lambda *a, **k: None)
    other = _seed_job(owner="bob")
    await save_job(other)
    headers = auth_header("alice", Role.builder)

    denied = await client.get(f"/api/builds/{other.id}", headers=headers)
    assert denied.status_code == 403
    cancelled = await client.post(f"/api/builds/{other.id}/cancel", headers=headers)
    assert cancelled.status_code == 403


async def test_builder_can_cancel_own_queued_job(client, monkeypatch):
    monkeypatch.setattr("app.api.builds.run_build.delay", lambda *a, **k: _FakeTask())
    monkeypatch.setattr("app.api.builds.celery_app.control.revoke", lambda *a, **k: None)
    headers = auth_header("alice", Role.builder)
    created = await client.post("/api/builds", json=BUILD_PAYLOAD, headers=headers)
    job_id = created.json()["id"]

    cancelled = await client.post(f"/api/builds/{job_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["request"]["vsphere_password"] is None


async def test_admin_lists_all_jobs_and_can_read_any(client):
    alice = _seed_job(owner="alice", hostname="alice-vm")
    bob = _seed_job(owner="bob", hostname="bob-vm")
    await save_job(alice)
    await save_job(bob)

    headers = auth_header("root", Role.admin)
    listed = await client.get("/api/builds", headers=headers)
    assert listed.status_code == 200
    owners = {row["requested_by"] for row in listed.json()}
    assert owners == {"alice", "bob"}
    assert all(row["request"]["vsphere_password"] is None for row in listed.json())

    detail = await client.get(f"/api/builds/{bob.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["requested_by"] == "bob"


async def test_missing_job_is_not_found(client):
    headers = auth_header("root", Role.admin)
    res = await client.get("/api/builds/does-not-exist", headers=headers)
    assert res.status_code == 404
