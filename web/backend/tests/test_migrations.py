"""Alembic revisions: round-trip, drift vs models, and pre-Alembic adoption.

Runs on sqlite always. Set ``OVBUILDER_TEST_POSTGRES_URL`` (a throwaway
database — its ``public`` schema is dropped) to also run against Postgres:

    OVBUILDER_TEST_POSTGRES_URL=postgresql+psycopg://u:p@localhost/ovb_test
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.database import Base, JobRow, UserRow
from app.migrations import (
    UNVERSIONED_BASELINE,
    UnrecognizedSchemaError,
    alembic_config,
    detect_unversioned_revision,
    downgrade,
    upgrade,
)

SCRIPT = ScriptDirectory.from_config(alembic_config("sqlite://"))
HEAD = SCRIPT.get_current_head() if len(SCRIPT.get_heads()) == 1 else None
POSTGRES_URL = os.environ.get("OVBUILDER_TEST_POSTGRES_URL", "").strip()


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'migrate.db'}"
        return
    if not POSTGRES_URL:
        pytest.skip("OVBUILDER_TEST_POSTGRES_URL not set")
    engine = create_engine(POSTGRES_URL, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    yield POSTGRES_URL


@pytest.fixture
def engine(db_url):
    eng = create_engine(db_url, poolclass=NullPool)
    yield eng
    eng.dispose()


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _version(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_single_head():
    """Two PRs each adding a revision off the same parent must be rebased."""
    heads = SCRIPT.get_heads()
    assert len(heads) == 1, (
        f"Multiple Alembic heads {heads}: set the newer revision's "
        "down_revision to the other head (or `alembic merge heads`)."
    )


def test_upgrade_downgrade_round_trip(db_url, engine):
    upgrade(db_url)
    assert {"build_jobs", "users", "alembic_version"} <= _tables(engine)
    assert _version(engine) == HEAD

    downgrade(db_url, "0001")
    assert "users" not in _tables(engine)
    assert _version(engine) == "0001"

    downgrade(db_url, "base")
    assert _tables(engine) - {"alembic_version"} == set()
    assert _version(engine) is None

    upgrade(db_url)
    assert _version(engine) == HEAD


def test_upgrade_is_idempotent(db_url, engine):
    upgrade(db_url)
    upgrade(db_url)
    assert _version(engine) == HEAD


def test_migrations_match_models(db_url, engine):
    """Fails when a model changes without a matching revision."""
    upgrade(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def _create_pre_alembic_schema(engine) -> None:
    """What ``create_all`` produced before Alembic owned the schema."""
    tables = [Base.metadata.tables[table] for _, table, _ in UNVERSIONED_BASELINE]
    Base.metadata.create_all(engine, tables=tables)


def test_adopts_create_all_database(db_url, engine):
    _create_pre_alembic_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            JobRow.__table__.insert().values(
                id="legacy-1",
                status="succeeded",
                request={"hostname": "web01"},
                requested_by="alice",
                created_at=_now(),
                log_tail="",
            )
        )
    assert "alembic_version" not in _tables(engine)

    upgrade(db_url)

    assert _version(engine) == HEAD
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM build_jobs")).scalar_one() == "legacy-1"


def test_adopts_partial_pre_alembic_database(db_url, engine):
    """build_jobs from create_all (0.97-beta19) but no users table yet."""
    JobRow.__table__.create(engine)

    upgrade(db_url)

    assert "users" in _tables(engine)
    assert _version(engine) == HEAD


def test_refuses_table_without_its_predecessors(db_url, engine):
    UserRow.__table__.create(engine)
    with pytest.raises(UnrecognizedSchemaError, match="users"):
        upgrade(db_url)
    assert _version(engine) is None


def test_refuses_model_table_newer_than_baseline(db_url, engine):
    """A stray create_all of newer models must not be stamped at the baseline."""
    _create_pre_alembic_schema(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE newer_feature (id INTEGER PRIMARY KEY)"))
        with pytest.raises(UnrecognizedSchemaError, match="newer_feature"):
            detect_unversioned_revision(conn, model_tables=["newer_feature"])
        assert detect_unversioned_revision(conn) == UNVERSIONED_BASELINE[-1][0]


def test_refuses_table_missing_baseline_columns(db_url, engine):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE build_jobs (id VARCHAR(36) PRIMARY KEY)"))
    with pytest.raises(UnrecognizedSchemaError, match="missing columns"):
        upgrade(db_url)
    assert _version(engine) is None
