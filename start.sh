#!/bin/bash
# Startup script for Railway — creates DB schemas + tables then starts the server.
set -e

echo "Starting KinValet API..."
echo "Environment: $ENVIRONMENT"
echo "Port: $PORT"

# Create database schemas AND tables (idempotent — safe on every deploy)
echo "Initializing database..."
python3 -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text

async def init():
    from app.platform.config import get_settings
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={'statement_cache_size': 0, 'prepared_statement_cache_size': 0},
    )

    schemas = ['identity','inbound','extraction','operations','connectors','skills',
               'notification','briefing','support','financial','platform']
    async with engine.begin() as conn:
        for s in schemas:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS {s}'))
        print(f'{len(schemas)} schemas ready')

    from app.modules.identity.models import IdentityBase
    from app.modules.inbound.models import InboundBase
    from app.modules.extraction.models import ExtractionBase
    from app.modules.operations.models import OperationsBase
    from app.modules.connectors.models import ConnectorsBase
    from app.modules.connectors.calendar_config import ConnectorsConfigBase
    from app.modules.connectors.email_inbound import MemberInboundAddress, InboundEmailLog
    from app.modules.skills.models import SkillsBase
    from app.modules.notification.models import NotificationBase
    from app.modules.briefing.models import BriefingBase
    from app.modules.support.models import SupportBase
    from app.modules.financial.models import FinancialBase
    from app.platform.events.outbox import PlatformBase
    from app.platform.audit import PlatformAuditBase
    from app.platform.cost_ledger import PlatformLedgerBase

    async with engine.begin() as conn:
        for base in [IdentityBase, InboundBase, ExtractionBase, OperationsBase,
                     ConnectorsBase, ConnectorsConfigBase, SkillsBase, NotificationBase,
                     BriefingBase, SupportBase, FinancialBase, PlatformBase,
                     PlatformAuditBase, PlatformLedgerBase]:
            await conn.run_sync(base.metadata.create_all)
    print('All tables ready')
    await engine.dispose()

asyncio.run(init())
" || echo "Warning: DB init failed (tables may already exist)"

# Start uvicorn with Railway's PORT
echo "Starting API on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 2
