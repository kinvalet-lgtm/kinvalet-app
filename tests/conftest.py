"""Shared test fixtures and configuration."""
import asyncio
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Use a test database — set TEST_DATABASE_URL in environment or use SQLite for fast tests
TEST_DATABASE_URL = "postgresql+asyncpg://sandwich:sandwich_dev_password@localhost:5432/sandwich_copilot_test"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session-scoped async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create test database engine. Runs once per session."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )

    # Create schemas
    async with engine.connect() as conn:
        schemas = [
            "identity", "inbound", "extraction", "operations",
            "connectors", "skills", "notification", "briefing",
            "support", "platform",
        ]
        for schema in schemas:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.commit()

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional test session that rolls back after each test."""
    SessionFactory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with SessionFactory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture
def household_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def member_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def set_context(household_id, member_id):
    """Set request context for tests that require it."""
    from app.platform.context import set_request_context
    return set_request_context(
        household_id=str(household_id),
        member_id=str(member_id),
        actor_type="household_member",
    )
