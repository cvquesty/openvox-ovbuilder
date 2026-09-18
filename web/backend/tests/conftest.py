"""Shared fixtures for the OV Builder web backend.

Uses a file-backed SQLite store so tests do not need Postgres, Redis,
LDAP, or a live ovbuilder CLI. SECRET_KEY is pinned before app.config is
imported so JWT issue/verify share one key (and so Settings fail-fast
from PR #28 does not refuse to boot).
"""

from __future__ import annotations

import os

# Must be set before app.config.get_settings() is first called.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-32ch")
os.environ.setdefault("LDAP_ENABLED", "true")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("ROLE_CACHE_TTL_SECONDS", "60")

import pytest
from httpx import ASGITransport, AsyncClient

from app import database as db
from app.auth import create_token
from app.config import get_settings
from app.main import app
from app.models import Role


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-long-enough-32ch")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'jobs.db'}")
    get_settings.cache_clear()
    db.configure_database()
    db.Base.metadata.create_all(db.get_sync_engine())
    yield
    db.dispose_database()
    get_settings.cache_clear()


@pytest.fixture
def user_db(job_db):
    """Same sqlite schema as job_db; name kept for role-override tests."""
    yield


@pytest.fixture
async def client(user_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def auth_header(username: str, role: Role) -> dict[str, str]:
    token = create_token(username, role, get_settings())
    return {"Authorization": f"Bearer {token}"}
