"""Shared fixtures for web-backend tests (no live vCenter required)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Must be set before app.config.get_settings() is first called.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-32ch")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("LDAP_ENABLED", "false")

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

from app.config import get_settings
from app import database as db

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
