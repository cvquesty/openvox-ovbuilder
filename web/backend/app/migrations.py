"""Alembic glue shared by ``alembic/env.py``, API startup, and tests.

Databases created before Alembic owned the schema (``create_all`` on
Postgres, 0.97-beta19) have the tables but no ``alembic_version`` row, so a
plain ``upgrade head`` would fail with "relation already exists". Such a
database is *adopted*: the newest baseline revision whose tables are already
present is stamped, then normal upgrades apply the rest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# Serializes concurrent upgrades (installer + API startup) on Postgres.
POSTGRES_ADVISORY_LOCK_ID = 7_401_305_510

# Frozen: the schema each pre-Alembic revision created. Only revisions that
# could exist unversioned belong here; never add revisions written after
# Alembic became the only schema path.
UNVERSIONED_BASELINE: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "0001",
        "build_jobs",
        frozenset(
            {
                "id",
                "status",
                "request",
                "requested_by",
                "created_at",
                "started_at",
                "finished_at",
                "celery_task_id",
                "log_tail",
                "error",
                "vm_ip",
                "vm_name",
            }
        ),
    ),
    (
        "0002",
        "users",
        frozenset(
            {
                "username",
                "email",
                "display_name",
                "ldap_role",
                "role_override",
                "ldap_checked_at",
                "created_at",
                "updated_at",
            }
        ),
    ),
)


class UnrecognizedSchemaError(RuntimeError):
    """Tables exist without ``alembic_version`` and do not match a baseline."""


def detect_unversioned_revision(
    connection: Connection, model_tables: Iterable[str] = ()
) -> Optional[str]:
    """Return the baseline revision an unversioned schema matches.

    ``None`` means nothing to adopt: the database is empty or already tracked
    by Alembic. *model_tables* (``Base.metadata`` names) flags tables that a
    later revision should have created. Raises
    :class:`UnrecognizedSchemaError` rather than guessing.
    """
    insp = inspect(connection)
    tables = set(insp.get_table_names())
    if "alembic_version" in tables:
        return None

    matched: Optional[str] = None
    adopted: set[str] = set()
    for revision, table, columns in UNVERSIONED_BASELINE:
        if table not in tables:
            break
        present = {c["name"] for c in insp.get_columns(table)}
        missing = sorted(columns - present)
        if missing:
            raise UnrecognizedSchemaError(
                f"Table {table!r} exists without alembic_version but is missing "
                f"columns {missing}. Fix the table by hand, then run "
                f"`alembic stamp {revision}` and `alembic upgrade head`."
            )
        matched = revision
        adopted.add(table)

    known = {table for _, table, _ in UNVERSIONED_BASELINE} | set(model_tables)
    stray = sorted((tables & known) - adopted)
    if stray:
        raise UnrecognizedSchemaError(
            f"Tables {stray} exist without alembic_version and do not match a "
            "pre-Alembic baseline. Inspect the database, then run "
            "`alembic stamp <revision>` for the revision it matches."
        )
    return matched


def adopt_unversioned_schema(
    connection: Connection, migration_context, script, model_tables: Iterable[str] = ()
) -> Optional[str]:
    """Stamp a pre-Alembic database at its matching baseline revision."""
    with connection.begin():
        revision = detect_unversioned_revision(connection, model_tables)
        if revision is not None:
            logger.warning(
                "Database has ovbuilder tables but no alembic_version; stamping %s",
                revision,
            )
            migration_context.stamp(script, revision)
    return revision


def acquire_migration_lock(connection: Connection) -> bool:
    """Take a session-level advisory lock on Postgres. No-op elsewhere."""
    if connection.dialect.name != "postgresql":
        return False
    with connection.begin():
        connection.execute(
            text("SELECT pg_advisory_lock(:id)"), {"id": POSTGRES_ADVISORY_LOCK_ID}
        )
    return True


def release_migration_lock(connection: Connection) -> None:
    with connection.begin():
        connection.execute(
            text("SELECT pg_advisory_unlock(:id)"), {"id": POSTGRES_ADVISORY_LOCK_ID}
        )


def alembic_config(sync_url: str, *, configure_logger: bool = False):
    """Alembic ``Config`` for *sync_url*, usable from any working directory."""
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # ConfigParser interpolation: a literal % in a password must be doubled.
    cfg.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = configure_logger
    return cfg


def upgrade(sync_url: str, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(alembic_config(sync_url), revision)


def downgrade(sync_url: str, revision: str) -> None:
    from alembic import command

    command.downgrade(alembic_config(sync_url), revision)
