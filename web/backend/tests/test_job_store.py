"""Build jobs persist in SQLAlchemy and are visible to both async API and sync Celery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect

from app.config import get_settings
from app.database import (
    Base,
    active_job_counts,
    configure_database,
    count_jobs,
    count_jobs_sync,
    database_urls,
    dispose_database,
    get_job,
    get_job_sync,
    get_sync_engine,
    list_jobs,
    list_jobs_sync,
    postgres_pool_kwargs,
    run_alembic_upgrade,
    save_job,
    save_job_sync,
    update_job_log_sync,
)
from app.models import ACTIVE_BUILD_STATUSES, BuildJob, BuildRequest, BuildStatus
from tests.placeholders import placeholder_value


def _job(**kwargs) -> BuildJob:
    data = dict(
        id=str(uuid.uuid4()),
        status=BuildStatus.queued,
        request=BuildRequest(
            hostname="web01",
            ip="10.0.0.8",
            os_image="ubuntu-24.04",
            vsphere_password=placeholder_value(),
        ),
        requested_by="alice",
        created_at=datetime.now(timezone.utc),
    )
    data.update(kwargs)
    return BuildJob(**data)


def test_database_urls_normalize_postgres_drivers():
    # Assemble at runtime so committed sources have no user:pass@ URI.
    user = "ovbuilder"
    role = placeholder_value()
    raw = "postgresql://{0}:{1}@127.0.0.1:5432/ovbuilder".format(user, role)
    async_url, sync_url = database_urls(raw)
    assert async_url.startswith("postgresql+asyncpg://")
    assert sync_url.startswith("postgresql+psycopg://")
    assert f"{user}:{role}@" in async_url
    async_url, sync_url = database_urls(
        "postgresql+asyncpg://{0}:{1}@127.0.0.1:5432/ovbuilder".format(user, role)
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
    # Worker loads credentials from the job row; API responses still redact.
    assert loaded.request.vsphere_password == placeholder_value()
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


def test_postgres_pool_kwargs_match_api_and_worker():
    api = postgres_pool_kwargs(pool_size=5, max_overflow=10)
    assert api == {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True}
    worker = postgres_pool_kwargs(pool_size=2, max_overflow=2)
    assert worker["pool_size"] == 2
    assert worker["max_overflow"] == 2
    assert worker["pool_pre_ping"] is True


def test_count_jobs_filters_active_status(job_db):
    save_job_sync(_job(status=BuildStatus.queued))
    save_job_sync(_job(status=BuildStatus.running, requested_by="bob"))
    save_job_sync(_job(status=BuildStatus.succeeded))
    assert count_jobs_sync(statuses=ACTIVE_BUILD_STATUSES) == 2
    assert count_jobs_sync(username="alice", statuses=ACTIVE_BUILD_STATUSES) == 1
    assert asyncio.run(count_jobs(statuses=ACTIVE_BUILD_STATUSES)) == 2
    counts = asyncio.run(active_job_counts())
    assert counts[BuildStatus.queued.value] == 1
    assert counts[BuildStatus.running.value] == 1
