"""Shared fixtures for the OV Builder web backend.

Uses a file-backed SQLite store so tests do not need Postgres, Redis,
LDAP, or a live ovbuilder CLI. SECRET_KEY is pinned before app.config is
imported so JWT issue/verify share one key.
"""

from __future__ import annotations

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-32ch")
os.environ.setdefault("LDAP_ENABLED", "true")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("ROLE_CACHE_TTL_SECONDS", "60")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import database as db
from app.auth import create_token
from app.config import get_settings
from app.main import app
from app.models import Role


@pytest.fixture
async def user_db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'ovbuilder.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_async_engine(url)
    db.engine = engine
    db.async_session = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    await engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
async def client(user_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def auth_header(username: str, role: Role) -> dict[str, str]:
    token = create_token(username, role, get_settings())
    return {"Authorization": f"Bearer {token}"}
