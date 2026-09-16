"""Async SQLAlchemy engine + session with a durable Postgres job store.

Replaces the in-memory dict that broke under Celery workers (each worker had
its own copy, so builds showed as 'missing') and would not survive restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings
from .models import BuildJob, BuildStatus

logger = logging.getLogger(__name__)

_settings = get_settings()
engine = create_async_engine(_settings.database_url, echo=_settings.debug, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


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


def _to_model(row: JobRow) -> BuildJob:
    return BuildJob(
        id=row.id,
        status=BuildStatus(row.status),
        request=row.request,
        requested_by=row.requested_by,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        celery_task_id=row.celery_task_id,
        log_tail=row.log_tail or "",
        error=row.error,
        vm_ip=row.vm_ip,
        vm_name=row.vm_name,
    )


async def init_db() -> None:
    """Create tables if they don't exist. Call from app lifespan."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def save_job(job: BuildJob) -> None:
    async with async_session() as session:
        existing = await session.get(JobRow, job.id)
        if existing is None:
            session.add(JobRow(
                id=job.id,
                status=job.status.value,
                request=job.request.model_dump(),
                requested_by=job.requested_by,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                celery_task_id=job.celery_task_id,
                log_tail=job.log_tail,
                error=job.error,
                vm_ip=job.vm_ip,
                vm_name=job.vm_name,
            ))
        else:
            existing.status = job.status.value
            existing.request = job.request.model_dump()
            existing.requested_by = job.requested_by
            existing.started_at = job.started_at
            existing.finished_at = job.finished_at
            existing.celery_task_id = job.celery_task_id
            existing.log_tail = job.log_tail
            existing.error = job.error
            existing.vm_ip = job.vm_ip
            existing.vm_name = job.vm_name
        await session.commit()


async def get_job(job_id: str) -> Optional[BuildJob]:
    async with async_session() as session:
        row = await session.get(JobRow, job_id)
        return _to_model(row) if row else None


async def list_jobs(username: Optional[str] = None) -> list[BuildJob]:
    async with async_session() as session:
        stmt = select(JobRow).order_by(JobRow.created_at.desc())
        if username:
            stmt = stmt.where(JobRow.requested_by == username)
        result = await session.execute(stmt)
        return [_to_model(r) for r in result.scalars().all()]
