"""SQLAlchemy persistence for build jobs.

The FastAPI process and Celery workers are separate interpreters. Jobs live in
Postgres so list/detail/create and worker status/log updates share one table.

Two engines, one table:
- async (asyncpg) for the API
- sync (psycopg) for Celery and Alembic — no asyncio.run() in worker threads
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import JSON, DateTime, String, Text, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings
from .models import BuildJob, BuildRequest, BuildStatus

_async_engine: Optional[AsyncEngine] = None
_sync_engine: Optional[Engine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_sync_session_factory: Optional[sessionmaker[Session]] = None


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    """Durable representation of a BuildJob."""

    __tablename__ = "build_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    request: Mapped[dict] = mapped_column(JSON)
    requested_by: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    log_tail: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vm_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vm_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


def database_urls(url: Optional[str] = None) -> tuple[str, str]:
    """Return ``(async_url, sync_url)`` derived from ``DATABASE_URL``.

    Accepts ``postgresql+asyncpg://``, ``postgresql+psycopg://``,
    ``postgresql://``, and sqlite variants. Drivers are normalized so the API
    always uses asyncpg (or aiosqlite) and Celery/Alembic always use a sync
    driver.
    """
    raw = (url or get_settings().database_url).strip()
    if "://" not in raw:
        raise ValueError(f"DATABASE_URL must include a scheme: {raw!r}")
    scheme, rest = raw.split("://", 1)
    if "sqlite" in scheme:
        return f"sqlite+aiosqlite://{rest}", f"sqlite://{rest}"
    return f"postgresql+asyncpg://{rest}", f"postgresql+psycopg://{rest}"


def _is_sqlite(url: str) -> bool:
    return url.split("://", 1)[0].startswith("sqlite")


def configure_database(database_url: Optional[str] = None) -> None:
    """Create (or recreate) the async and sync engines.

    Safe to call from tests after pointing ``DATABASE_URL`` at sqlite.
    """
    global _async_engine, _sync_engine, _async_session_factory, _sync_session_factory
    dispose_database()

    async_url, sync_url = database_urls(database_url)
    echo = get_settings().debug

    async_kwargs: dict = {"echo": echo, "pool_pre_ping": not _is_sqlite(async_url)}
    _async_engine = create_async_engine(async_url, **async_kwargs)

    sync_kwargs: dict = {"echo": echo}
    if _is_sqlite(sync_url):
        sync_kwargs["connect_args"] = {"check_same_thread": False}
        # One in-process connection so async + sync sessions see the same rows
        # when tests use a shared sqlite file (and :memory:).
        sync_kwargs["poolclass"] = StaticPool
    else:
        sync_kwargs["pool_pre_ping"] = True
    _sync_engine = create_engine(sync_url, **sync_kwargs)

    _async_session_factory = async_sessionmaker(
        _async_engine, expire_on_commit=False, class_=AsyncSession
    )
    _sync_session_factory = sessionmaker(
        _sync_engine, expire_on_commit=False, class_=Session
    )


def dispose_database() -> None:
    global _async_engine, _sync_engine, _async_session_factory, _sync_session_factory
    if _async_engine is not None:
        _async_engine.sync_engine.dispose()
        _async_engine = None
    if _sync_engine is not None:
        _sync_engine.dispose()
        _sync_engine = None
    _async_session_factory = None
    _sync_session_factory = None


def _ensure_configured() -> None:
    if _async_engine is None or _sync_engine is None:
        configure_database()


def get_async_engine() -> AsyncEngine:
    _ensure_configured()
    assert _async_engine is not None
    return _async_engine


def get_sync_engine() -> Engine:
    _ensure_configured()
    assert _sync_engine is not None
    return _sync_engine


def _async_session() -> async_sessionmaker[AsyncSession]:
    _ensure_configured()
    assert _async_session_factory is not None
    return _async_session_factory


def _sync_session() -> sessionmaker[Session]:
    _ensure_configured()
    assert _sync_session_factory is not None
    return _sync_session_factory


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _to_model(row: JobRow) -> BuildJob:
    return BuildJob(
        id=row.id,
        status=BuildStatus(row.status),
        request=BuildRequest.model_validate(row.request),
        requested_by=row.requested_by,
        created_at=_aware(row.created_at) or datetime.now(timezone.utc),
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        celery_task_id=row.celery_task_id,
        log_tail=row.log_tail or "",
        error=row.error,
        vm_ip=row.vm_ip,
        vm_name=row.vm_name,
    )


def _apply_job(row: JobRow, job: BuildJob) -> None:
    row.status = job.status.value
    row.request = job.request.model_dump()
    row.requested_by = job.requested_by
    row.started_at = job.started_at
    row.finished_at = job.finished_at
    row.celery_task_id = job.celery_task_id
    row.log_tail = job.log_tail or ""
    row.error = job.error
    row.vm_ip = job.vm_ip
    row.vm_name = job.vm_name


def _row_from_job(job: BuildJob) -> JobRow:
    return JobRow(
        id=job.id,
        status=job.status.value,
        request=job.request.model_dump(),
        requested_by=job.requested_by,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        celery_task_id=job.celery_task_id,
        log_tail=job.log_tail or "",
        error=job.error,
        vm_ip=job.vm_ip,
        vm_name=job.vm_name,
    )


def run_alembic_upgrade() -> None:
    """Apply Alembic migrations to ``head`` using the sync engine URL."""
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    _, sync_url = database_urls()
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")


async def init_db() -> None:
    """Bring schema up to date.

    Postgres: Alembic ``upgrade head`` (same path as ``install-web.sh``).
    sqlite: ``create_all`` so unit tests do not need a migration runner.
    """
    _ensure_configured()
    _, sync_url = database_urls()
    if _is_sqlite(sync_url):
        async with get_async_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    await asyncio.to_thread(run_alembic_upgrade)


# ---------------------------------------------------------------------------
# Sync API — Celery workers
# ---------------------------------------------------------------------------


def save_job_sync(job: BuildJob) -> None:
    factory = _sync_session()
    with factory() as session:
        existing = session.get(JobRow, job.id)
        if existing is None:
            session.add(_row_from_job(job))
        else:
            _apply_job(existing, job)
        session.commit()


def get_job_sync(job_id: str) -> Optional[BuildJob]:
    factory = _sync_session()
    with factory() as session:
        row = session.get(JobRow, job_id)
        return _to_model(row) if row else None


def list_jobs_sync(username: Optional[str] = None) -> list[BuildJob]:
    factory = _sync_session()
    with factory() as session:
        stmt = select(JobRow).order_by(JobRow.created_at.desc())
        if username:
            stmt = stmt.where(JobRow.requested_by == username)
        return [_to_model(r) for r in session.scalars(stmt).all()]


def update_job_log_sync(job_id: str, log_tail: str) -> Optional[BuildJob]:
    """Write log_tail only so a worker cannot clobber a cancel status."""
    factory = _sync_session()
    with factory() as session:
        row = session.get(JobRow, job_id)
        if row is None:
            return None
        row.log_tail = log_tail
        session.commit()
        session.refresh(row)
        return _to_model(row)


# ---------------------------------------------------------------------------
# Async API — FastAPI
# ---------------------------------------------------------------------------


async def save_job(job: BuildJob) -> None:
    factory = _async_session()
    async with factory() as session:
        existing = await session.get(JobRow, job.id)
        if existing is None:
            session.add(_row_from_job(job))
        else:
            _apply_job(existing, job)
        await session.commit()


async def get_job(job_id: str) -> Optional[BuildJob]:
    factory = _async_session()
    async with factory() as session:
        row = await session.get(JobRow, job_id)
        return _to_model(row) if row else None


async def list_jobs(username: Optional[str] = None) -> list[BuildJob]:
    factory = _async_session()
    async with factory() as session:
        stmt = select(JobRow).order_by(JobRow.created_at.desc())
        if username:
            stmt = stmt.where(JobRow.requested_by == username)
        result = await session.execute(stmt)
        return [_to_model(r) for r in result.scalars().all()]
