"""Job store create / list / get against the SQLite test database."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.database import get_job, list_jobs, save_job
from app.models import BuildJob, BuildRequest, BuildStatus


def _job(**kwargs) -> BuildJob:
    data = dict(
        id=str(uuid4()),
        status=BuildStatus.queued,
        request=BuildRequest(
            hostname="web01",
            ip="10.0.0.8",
            os_image="ubuntu-24.04",
            vsphere_password="s3cret",
        ),
        requested_by="alice",
        created_at=datetime.now(timezone.utc),
    )
    data.update(kwargs)
    return BuildJob(**data)


async def test_save_get_roundtrip(job_db):
    job = _job()
    await save_job(job)
    loaded = await get_job(job.id)
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.status == BuildStatus.queued
    assert loaded.requested_by == "alice"
    assert loaded.request.hostname == "web01"
    # Durable store keeps the worker secret; API schemas must still redact it.
    assert loaded.request.vsphere_password == "s3cret"


async def test_get_missing_job_returns_none(job_db):
    assert await get_job("missing-id") is None


async def test_list_jobs_filters_by_username(job_db):
    alice = _job(requested_by="alice", id=str(uuid4()))
    bob = _job(
        requested_by="bob",
        id=str(uuid4()),
        request=BuildRequest(hostname="bob-vm", ip="10.0.0.9", os_image="ubuntu-24.04"),
    )
    await save_job(alice)
    await save_job(bob)

    alice_rows = await list_jobs(username="alice")
    assert [j.id for j in alice_rows] == [alice.id]

    bob_rows = await list_jobs(username="bob")
    assert [j.id for j in bob_rows] == [bob.id]

    everyone = await list_jobs()
    assert {j.requested_by for j in everyone} == {"alice", "bob"}


async def test_save_job_updates_existing_row(job_db):
    job = _job()
    await save_job(job)
    job.status = BuildStatus.running
    job.log_tail = "cloning..."
    job.vm_name = "web01"
    await save_job(job)

    loaded = await get_job(job.id)
    assert loaded is not None
    assert loaded.status == BuildStatus.running
    assert loaded.log_tail == "cloning..."
    assert loaded.vm_name == "web01"
    assert loaded.request.vsphere_password == "s3cret"
