"""Shared fixtures for web-backend tests.

SECRET_KEY is pinned before app.config is imported (fail-fast unless DEBUG).
The job store uses sqlite via configure_database() so tests do not need
Postgres, Redis, LDAP, or a live ovbuilder CLI. Inventory tests mock the
vSphere session — no live vCenter.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Must be set before app.config.get_settings() is first called.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-32ch")
os.environ.setdefault("DEBUG", "false")
# Auth smoke tests mock ldap_authenticate; keep LDAP enabled so login is not 503.
os.environ.setdefault("LDAP_ENABLED", "true")

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from fastapi.testclient import TestClient

from app import database as db
from app.auth import create_token
from app.config import get_settings
from app.main import app
from app.models import Role

get_settings.cache_clear()


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
def client(job_db):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client():
    """HTTP client that does not open the job store (login /me /roles /health)."""
    return TestClient(app)


def auth_header(username: str, role: Role) -> dict[str, str]:
    token = create_token(username, role, get_settings())
    return {"Authorization": f"Bearer {token}"}
