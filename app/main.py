"""FastAPI application factory — router mounting and dependency injection wiring.

Architecture decisions:
- DI container wires module APIs to their Protocol contracts
- When a module is extracted to its own service, change ONE line of DI wiring
- All other callers (using Protocols) are unaware and untouched
"""
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.platform.config import get_settings
from app.platform.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    HouseholdScopeError,
    NotFoundError,
    RateLimitError,
    WebhookSignatureError,
)
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info(
        "application_starting",
        environment=_settings.environment,
        version="0.1.0",
    )

    # Import and register event handlers from all modules
    # This triggers the @subscribe decorators
    import app.modules.notification.handlers  # noqa: F401
    import app.modules.operations.handlers    # noqa: F401

    # Verify database connectivity (non-fatal — server starts regardless)
    try:
        from app.platform.db import engine
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        logger.info("database_connected")
    except Exception as e:
        logger.warning("database_not_available", error=str(e),
                       note="API started without DB — endpoints requiring DB will return 503")

    # Seed connector type registry rows
    await _seed_connector_types()

    # Open the procrastinate async queue app so defer_async() works
    from app.platform.queue import async_queue_app
    try:
        await async_queue_app.open_async()
        logger.info("queue_opened")
    except Exception as e:
        logger.warning("queue_open_failed", error=str(e))

    yield

    # Close the queue app cleanly on shutdown
    try:
        await async_queue_app.close_async()
    except Exception:
        pass

    logger.info("application_shutting_down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="KinValet API",
        description="AI-powered family operations platform for the Sandwich Generation",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not _settings.is_production else None,
        redoc_url="/redoc" if not _settings.is_production else None,
    )

    # ── CORS ────────────────────────────────────────────────────────────────────
    origins = [
        "http://localhost:3000",
        "https://dashboard-production-4b07.up.railway.app",
        "https://app.kinvalet.com",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ───────────────────────────────────────────────────────

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.error_code,
                "message": exc.message,
                "detail": exc.detail,
            },
        )

    @app.exception_handler(AuthenticationError)
    async def auth_error_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "authentication_required"})

    @app.exception_handler(WebhookSignatureError)
    async def webhook_sig_handler(request: Request, exc: WebhookSignatureError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "webhook_signature_invalid"})

    # ── Health check ─────────────────────────────────────────────────────────────

    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        return {"status": "ok", "version": "0.2.0", "build": "cal-events", "environment": _settings.environment}

    # ── Mount module routers ─────────────────────────────────────────────────────

    from app.modules.identity.router import router as identity_router
    from app.modules.identity.household_router import router as household_router
    from app.modules.inbound.meta_whatsapp import router as inbound_router  # Meta WhatsApp Cloud API
    from app.modules.inbound.dashboard_input import router as assistant_router  # Dashboard input channel
    from app.modules.operations.router import router as operations_router
    from app.modules.operations.delegation_router import router as delegation_router
    from app.modules.connectors.debug_token import router as debug_router
    from app.modules.connectors.router import router as connectors_router
    from app.modules.connectors.calendar_events_router import router as calendar_events_router
    from app.modules.connectors.config_router import router as connectors_config_router
    from app.modules.connectors.email_inbound_router import router as email_inbound_router
    from app.modules.notification.router import router as notification_router
    from app.modules.financial.router import router as financial_router
    from app.modules.financial.plaid_webhook import router as plaid_webhook_router
    from app.modules.admin.router import router as admin_router

    app.include_router(identity_router)
    app.include_router(household_router)
    app.include_router(inbound_router)
    app.include_router(assistant_router)
    app.include_router(operations_router)
    app.include_router(delegation_router)
    app.include_router(debug_router)
    app.include_router(connectors_router)
    app.include_router(calendar_events_router)
    app.include_router(connectors_config_router)
    app.include_router(email_inbound_router)
    app.include_router(notification_router)
    app.include_router(financial_router)
    app.include_router(plaid_webhook_router)
    app.include_router(admin_router)

    # Briefing router
    from fastapi import APIRouter, Depends
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.platform.db import get_db_session
    from app.modules.identity.router import get_current_member

    briefing_router = APIRouter(prefix="/api/v1/briefing", tags=["briefing"])

    @briefing_router.get("/latest")
    async def get_latest_briefing(
        current_member=Depends(get_current_member),
        session: AsyncSession = Depends(get_db_session),
    ):
        from app.modules.briefing.api import BriefingService
        svc = BriefingService(session)
        briefing = await svc.get_latest_briefing(current_member.household_id)
        if briefing is None:
            raise HTTPException(status_code=404, detail="No briefing found")
        return briefing

    app.include_router(briefing_router)

    logger.info("routers_mounted")
    return app


async def _seed_connector_types() -> None:
    """Ensure connector_type rows exist for all registered adapters."""
    from app.modules.connectors.registry import CONNECTOR_TYPE_SEEDS
    from app.modules.connectors.models import ConnectorType
    from app.platform.db import AsyncSessionFactory
    from sqlalchemy import select
    try:
        async with AsyncSessionFactory() as session:
            for seed in CONNECTOR_TYPE_SEEDS:
                existing = await session.execute(
                    select(ConnectorType).where(ConnectorType.id == seed["id"])
                )
                if existing.scalar_one_or_none() is None:
                    session.add(ConnectorType(**seed))
            await session.commit()
    except Exception as e:
        logger.warning("connector_seed_failed", error=str(e))


app = create_app()
