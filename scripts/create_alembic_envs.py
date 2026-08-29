"""Create Alembic env.py for each module.

Per-module Alembic directories (Architecture §7.3):
Each module owns its migration history inside its own schema.
When a module is extracted to a service, its Alembic history travels with it.

Run once: python scripts/create_alembic_envs.py
"""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent

MODULE_CONFIG = {
    "identity": ("app.modules.identity.models", "IdentityBase"),
    "inbound": ("app.modules.inbound.models", "InboundBase"),
    "extraction": ("app.modules.extraction.models", "ExtractionBase"),
    "operations": ("app.modules.operations.models", "OperationsBase"),
    "connectors": ("app.modules.connectors.models", "ConnectorsBase"),
    "skills": ("app.modules.skills.models", "SkillsBase"),
    "notification": ("app.modules.notification.models", "NotificationBase"),
    "briefing": ("app.modules.briefing.models", "BriefingBase"),
    "support": ("app.modules.support.models", "SupportBase"),
    "financial": ("app.modules.financial.models", "FinancialBase"),
}

ENV_PY_TEMPLATE = '''"""Alembic env.py for the {module} module.

This module's migrations are isolated in the {module} schema.
Migration history travels with the module if/when it becomes its own service.
"""
import sys
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root to path
sys.path.insert(0, str(Path(__file__).parents[5]))

from {model_module} import {base_class}
from app.platform.config import get_settings

config = context.config
settings = get_settings()

# Override sqlalchemy.url with our settings
config.set_main_option("sqlalchemy.url", settings.database_sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = {base_class}.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={{"paramstyle": "named"}},
        version_table="alembic_version",
        version_table_schema="{module}",
        include_schemas=True,
        include_object=lambda obj, name, type_, reflected, compare_to:
            getattr(obj, "schema", None) == "{module}",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {{}}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema="{module}",
            include_schemas=True,
            include_object=lambda obj, name, type_, reflected, compare_to:
                getattr(obj, "schema", None) == "{module}",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

SCRIPT_PY_MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def create_alembic_env(module: str, model_module: str, base_class: str) -> None:
    migrations_dir = ROOT / "app" / "modules" / module / "migrations"
    versions_dir = migrations_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    # env.py
    env_path = migrations_dir / "env.py"
    if not env_path.exists():
        env_path.write_text(ENV_PY_TEMPLATE.format(
            module=module,
            model_module=model_module,
            base_class=base_class,
        ))
        print(f"  Created {env_path}")

    # script.py.mako
    script_path = migrations_dir / "script.py.mako"
    if not script_path.exists():
        script_path.write_text(SCRIPT_PY_MAKO)

    # __init__.py in migrations and versions
    for d in [migrations_dir, versions_dir]:
        init = d / "__init__.py"
        if not init.exists():
            init.write_text("")


if __name__ == "__main__":
    print("Creating Alembic env.py for each module...")
    for module, (model_module, base_class) in MODULE_CONFIG.items():
        print(f"  {module}...")
        create_alembic_env(module, model_module, base_class)
    print("Done. Run `python scripts/migrate.py upgrade` to apply migrations.")
