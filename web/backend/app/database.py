"""Async SQLAlchemy engine + session, plus a tiny in-memory job store.

The in-memory store is the default so the scaffold runs with zero external
dependencies. Swap to Postgres (see models + alembic) when you want durable
build history across restarts.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings
from .models import BuildJob


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(_settings.database_url, echo=_settings.debug)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# In-memory job registry (thread-safe enough for the scaffold)
# ---------------------------------------------------------------------------
_jobs: Dict[str, BuildJob] = {}
_lock = asyncio.Lock()


async def save_job(job: BuildJob) -> None:
    async with _lock:
        _jobs[job.id] = job


async def get_job(job_id: str) -> Optional[BuildJob]:
    async with _lock:
        return _jobs.get(job_id)


async def list_jobs(username: Optional[str] = None) -> list[BuildJob]:
    async with _lock:
        jobs = list(_jobs.values())
    if username:
        jobs = [j for j in jobs if j.requested_by == username]
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)
