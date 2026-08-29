"""Integration test fixtures — real PostgreSQL, real tables, rollback after each test."""
import asyncio
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DB_URL = "postgresql+asyncpg://sandwich:sandwich_dev_password@localhost:5432/sandwich_copilot_test"

# Module-level schemas to create
SCHEMAS = [
    "identity", "inbound", "extraction", "operations",
    "connectors", "skills", "notification", "briefing",
    "support", "financial", "platform",
]


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        TEST_DB_URL,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )
    # Create schemas and tables
    async with engine.begin() as conn:
        for schema in SCHEMAS:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

        from app.modules.identity.models import IdentityBase
        from app.modules.inbound.models import InboundBase
        from app.modules.extraction.models import ExtractionBase
        from app.modules.operations.models import OperationsBase
        from app.modules.connectors.models import ConnectorsBase
        from app.modules.skills.models import SkillsBase
        from app.modules.notification.models import NotificationBase
        from app.modules.briefing.models import BriefingBase
        from app.modules.support.models import SupportBase
        from app.modules.financial.models import FinancialBase
        from app.platform.events.outbox import PlatformBase

        for base in [IdentityBase, InboundBase, ExtractionBase, OperationsBase,
                     ConnectorsBase, SkillsBase, NotificationBase, BriefingBase,
                     SupportBase, FinancialBase, PlatformBase]:
            await conn.run_sync(base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Each test gets a transaction that's rolled back after."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest_asyncio.fixture
async def app_client(db_session):
    """HTTPX client wired to the FastAPI app."""
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def unique_phone():
    """Generate a unique phone number for each test."""
    suffix = str(uuid.uuid4().int)[:7]
    return f"+1555{suffix}"


@pytest.fixture
def unique_email():
    return f"test-{uuid.uuid4().hex[:8]}@example.com"
