from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, pool

from app.migrations import (
    acquire_migration_lock,
    adopt_unversioned_schema,
    release_migration_lock,
)

config = context.config
# App startup passes configure_logger=False so uvicorn's loggers survive.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

sync_url = config.get_main_option("sqlalchemy.url")
if not sync_url:
    from app.config import get_settings
    from app.database import database_urls

    _, sync_url = database_urls(get_settings().database_url)
    config.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))


def _target_metadata():
    from app.database import Base

    return Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=sync_url, target_metadata=_target_metadata(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        locked = acquire_migration_lock(connection)
        try:
            metadata = _target_metadata()
            context.configure(connection=connection, target_metadata=metadata)
            adopt_unversioned_schema(
                connection,
                context.get_context(),
                ScriptDirectory.from_config(config),
                model_tables=metadata.tables.keys(),
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if locked:
                release_migration_lock(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
