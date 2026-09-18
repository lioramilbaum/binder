"""Alembic environment configured from the application's database settings."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context

from binder.database import Base, database_url
from binder.models import Document, DocumentFile, Folder  # noqa: F401 - registers metadata with Base.


config = context.config
if config.config_file_name is not None and Path(config.config_file_name).is_file():
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without connecting to the database."""

    context.configure(
        url=database_url(), target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations through SQLAlchemy's configured engine."""

    from sqlalchemy import create_engine

    connectable = create_engine(database_url(), pool_pre_ping=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
