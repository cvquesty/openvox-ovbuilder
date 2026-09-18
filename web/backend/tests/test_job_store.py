"""Build jobs persist in SQLAlchemy and are visible to both async API and sync Celery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect

from app.config import get_settings
from app.database import (
    Base,
    configure_database,
    database_urls,
    dispose_database,
    get_job,
    get_job_sync,
    get_sync_engine,
    list_jobs,
    list_jobs_sync,
    run_alembic_upgrade,
    save_job,
    save_job_sync,
    update_job_log_sync,
)
from app.models import BuildJob, BuildRequest, BuildStatus, redact_build_request


def _job(**kwargs) -> BuildJob:
    data = dict(
        id=str(uuid.uuid4()),
        status=BuildStatus.queued,
        request=redact_build_request(
            BuildRequest(
                hostname="web01",
                ip="10.0.0.8",
                os_image="ubuntu-24.04",
                vsphere_password="s3cret",
            )
        ),
        requested_by="alice",
        created_at=datetime.now(timezone.utc),
    )
    data.update(kwargs)
    return BuildJob(**data)


def test_database_urls_normalize_postgres_drivers():
    async_url, sync_url = database_urls("postgresql://ovbuilder:pw@127.0.0.1:5432/ovbuilder")
    assert async_url.startswith("postgresql+asyncpg://")
    assert sync_url.startswith("postgresql+psycopg://")
    assert "ovbuilder:pw@" in async_url
    async_url, sync_url = database_urls(
        "postgresql+asyncpg://ovbuilder:pw@127.0.0.1:5432/ovbuilder"
    )
    assert async_url.startswith("postgresql+asyncpg://")
    assert sync_url.startswith("postgresql+psycopg://")


def test_sync_save_get_list(job_db):
    job = _job()
    save_job_sync(job)
    loaded = get_job_sync(job.id)
    assert loaded is not None
    assert loaded.status == BuildStatus.queued
    assert loaded.request.hostname == "web01"
    # API redacts before persist; the worker gets the password on the Celery payload only.
    assert loaded.request.vsphere_password is None
    listed = list_jobs_sync(username="alice")
    assert [j.id for j in listed] == [job.id]
    assert list_jobs_sync(username="bob") == []


def test_async_and_sync_share_the_same_table(job_db):
    job = _job(requested_by="alice")
    save_job_sync(job)
    loaded = asyncio.run(get_job(job.id))
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.requested_by == "alice"

    job.status = BuildStatus.running
    asyncio.run(save_job(job))
    again = get_job_sync(job.id)
    assert again is not None
    assert again.status == BuildStatus.running

    listed = asyncio.run(list_jobs(username="alice"))
    assert len(listed) == 1


def test_log_update_does_not_clobber_cancel_status(job_db):
    job = _job(status=BuildStatus.running)
    save_job_sync(job)

    job.status = BuildStatus.cancelling
    job.error = "Cancelled by user"
    save_job_sync(job)

    latest = update_job_log_sync(job.id, "line 1\nline 2")
    assert latest is not None
    assert latest.status == BuildStatus.cancelling
    assert latest.error == "Cancelled by user"
    assert latest.log_tail == "line 1\nline 2"


def test_alembic_upgrade_creates_build_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migrate.db'}")
    get_settings.cache_clear()
    configure_database()
    try:
        run_alembic_upgrade()
        tables = inspect(get_sync_engine()).get_table_names()
        assert "build_jobs" in tables
        assert "alembic_version" in tables
        columns = {c["name"] for c in inspect(get_sync_engine()).get_columns("build_jobs")}
        assert {"id", "status", "request", "requested_by", "log_tail"} <= columns
        # create_all is not required after a real migration
        assert "build_jobs" in Base.metadata.tables
    finally:
        dispose_database()
        get_settings.cache_clear()
