"""Build API list/detail/create keep RBAC and do not leak vSphere passwords."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user, require_builder
from app.database import get_job_sync, save_job_sync
from app.main import app
from app.models import BuildJob, BuildRequest, BuildStatus, Role, UserOut
from tests.placeholders import test_placeholder


class _FakeTask:
    id = "celery-task-1"


def _user(username: str, role: Role):
    def dep() -> UserOut:
        return UserOut(username=username, role=role)

    return dep


@pytest.fixture
def api(job_db, monkeypatch):
    monkeypatch.setattr("app.api.builds.run_build.delay", lambda *a, **k: _FakeTask())
    app.dependency_overrides[get_current_user] = _user("alice", Role.builder)
    app.dependency_overrides[require_builder] = _user("alice", Role.builder)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_create_list_detail_redact_password(api):
    payload = {
        "hostname": "web01",
        "ip": "10.0.0.8",
        "os_image": "ubuntu-24.04",
    }
    payload["vsphere_password"] = test_placeholder()
    created = api.post("/api/builds", json=payload)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["requested_by"] == "alice"
    assert "vsphere_password" not in body["request"]
    assert body["celery_task_id"] == "celery-task-1"
    job_id = body["id"]

    stored = get_job_sync(job_id)
    assert stored is not None
    assert stored.request.vsphere_password is None

    listed = api.get("/api/builds")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == job_id
    assert "vsphere_password" not in rows[0]["request"]

    detail = api.get(f"/api/builds/{job_id}")
    assert detail.status_code == 200
    assert "vsphere_password" not in detail.json()["request"]


def test_builder_cannot_read_someone_elses_job(api):
    other = BuildJob(
        id=str(uuid.uuid4()),
        status=BuildStatus.queued,
        request=BuildRequest(hostname="other", ip="10.0.0.9", os_image="ubuntu-24.04"),
        requested_by="bob",
        created_at=datetime.now(timezone.utc),
    )
    save_job_sync(other)

    listed = api.get("/api/builds")
    assert listed.status_code == 200
    assert listed.json() == []

    denied = api.get(f"/api/builds/{other.id}")
    assert denied.status_code == 403


def test_cancel_queued_job(api, monkeypatch):
    monkeypatch.setattr("app.api.builds.celery_app.control.revoke", lambda *a, **k: None)
    created = api.post(
        "/api/builds",
        json={"hostname": "web01", "ip": "10.0.0.8", "os_image": "ubuntu-24.04"},
    )
    job_id = created.json()["id"]
    cancelled = api.post(f"/api/builds/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert "vsphere_password" not in body["request"]
    stored = get_job_sync(job_id)
    assert stored is not None
    assert stored.status == BuildStatus.cancelled


def test_admin_lists_all_jobs(job_db):
    save_job_sync(
        BuildJob(
            id=str(uuid.uuid4()),
            status=BuildStatus.queued,
            request=BuildRequest(hostname="bob-vm", ip="10.0.0.9", os_image="ubuntu-24.04"),
            requested_by="bob",
            created_at=datetime.now(timezone.utc),
        )
    )
    app.dependency_overrides[get_current_user] = _user("root", Role.admin)
    app.dependency_overrides[require_builder] = _user("root", Role.admin)
    try:
        with TestClient(app) as client:
            listed = client.get("/api/builds")
        assert listed.status_code == 200
        assert {row["requested_by"] for row in listed.json()} == {"bob"}
    finally:
        app.dependency_overrides.clear()
