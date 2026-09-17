"""Shared fixtures: sqlite job store so tests do not need Postgres."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app import database as db


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'jobs.db'}")
    get_settings.cache_clear()
    db.configure_database()
    db.Base.metadata.create_all(db.get_sync_engine())
    yield
    db.dispose_database()
    get_settings.cache_clear()
