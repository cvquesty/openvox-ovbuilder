"""Async SQLAlchemy engine + session with a durable Postgres job store.

Replaces the in-memory dict that broke under Celery workers (each worker had
its own copy, so builds showed as 'missing') and would not survive restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings
from .models import (
    BuildJob,
    BuildStatus,
    Role,
    UserAdminOut,
    coerce_role,
    effective_role,
)

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


class UserRow(Base):
    """Persisted identity + last LDAP role + optional local override."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ldap_role: Mapped[str] = mapped_column(String(16), default="viewer")
    role_override: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    ldap_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_user(row: UserRow) -> UserAdminOut:
    ldap_role = coerce_role(row.ldap_role)
    override = coerce_role(row.role_override) if row.role_override else None
    return UserAdminOut(
        username=row.username,
        email=row.email,
        display_name=row.display_name,
        ldap_role=ldap_role,
        role_override=override,
        role=effective_role(ldap_role, override),
        ldap_checked_at=row.ldap_checked_at,
    )


async def get_user(username: str) -> Optional[UserAdminOut]:
    async with async_session() as session:
        row = await session.get(UserRow, username)
        return _to_user(row) if row else None


async def upsert_user(
    *,
    username: str,
    ldap_role: Role,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    ldap_checked_at: Optional[datetime] = None,
) -> UserAdminOut:
    """Create or update the LDAP-mapped fields; never clears a local override."""
    async with async_session() as session:
        row = await session.get(UserRow, username)
        now = _utcnow()
        if row is None:
            row = UserRow(
                username=username,
                email=email,
                display_name=display_name,
                ldap_role=ldap_role.value,
                role_override=None,
                ldap_checked_at=ldap_checked_at,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.ldap_role = ldap_role.value
            if email is not None:
                row.email = email
            if display_name is not None:
                row.display_name = display_name
            if ldap_checked_at is not None:
                row.ldap_checked_at = ldap_checked_at
            row.updated_at = now
        await session.commit()
        await session.refresh(row)
        return _to_user(row)


async def set_role_override(username: str, override: Optional[Role]) -> UserAdminOut:
    """Set or clear a local role override, creating the user row if needed."""
    async with async_session() as session:
        row = await session.get(UserRow, username)
        now = _utcnow()
        if row is None:
            row = UserRow(
                username=username,
                email=None,
                display_name=None,
                ldap_role=Role.viewer.value,
                role_override=override.value if override is not None else None,
                ldap_checked_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.role_override = override.value if override is not None else None
            row.updated_at = now
        await session.commit()
        await session.refresh(row)
        return _to_user(row)


async def list_users() -> list[UserAdminOut]:
    async with async_session() as session:
        stmt = select(UserRow).order_by(UserRow.username.asc())
        result = await session.execute(stmt)
        return [_to_user(r) for r in result.scalars().all()]


async def list_jobs(username: Optional[str] = None) -> list[BuildJob]:
    async with async_session() as session:
        stmt = select(JobRow).order_by(JobRow.created_at.desc())
        if username:
            stmt = stmt.where(JobRow.requested_by == username)
        result = await session.execute(stmt)
        return [_to_model(r) for r in result.scalars().all()]
