"""Alembic env.py for the financial module.

This module's migrations are isolated in the financial schema.
Migration history travels with the module if/when it becomes its own service.
"""
import sys
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root to path
sys.path.insert(0, str(Path(__file__).parents[5]))

from app.modules.financial.models import FinancialBase
from app.platform.config import get_settings

config = context.config
settings = get_settings()

# Override sqlalchemy.url with our settings
config.set_main_option("sqlalchemy.url", settings.database_sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = FinancialBase.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="financial",
        include_schemas=True,
        include_object=lambda obj, name, type_, reflected, compare_to:
            getattr(obj, "schema", None) == "financial",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema="financial",
            include_schemas=True,
            include_object=lambda obj, name, type_, reflected, compare_to:
                getattr(obj, "schema", None) == "financial",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
