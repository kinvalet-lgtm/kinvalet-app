"""Async database engine, session factory, and schema helpers.

Uses asyncpg with NullPool (Supabase transaction-mode pooler manages pooling).
Prepared statements are disabled — required for Supabase's transaction-mode pooler.
"""
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.platform.config import get_settings

_settings = get_settings()

# NullPool: pooler (Supabase port 6543) manages the pool externally.
# statement_cache_size=0: transaction mode does not support prepared statements.
engine = create_async_engine(
    _settings.database_url,
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
    echo=_settings.is_development,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session and closes it after the request."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def set_rls_context(session: AsyncSession, household_id: str) -> None:
    """Set Row-Level Security context for the current transaction.

    Uses SET LOCAL so it scopes to the transaction — safe with connection pooling.
    Never use SET (session-level) — it leaks across pooled connections.
    """
    await session.execute(
        text("SET LOCAL app.household_id = :hid"),
        {"hid": household_id},
    )


MODULE_SCHEMAS = [
    "identity",
    "inbound",
    "extraction",
    "operations",
    "connectors",
    "skills",
    "notification",
    "briefing",
    "support",
    "platform",
]


async def create_schemas(session: AsyncSession) -> None:
    """Create all module schemas. Called during migrations / test setup."""
    for schema in MODULE_SCHEMAS:
        await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    await session.commit()
