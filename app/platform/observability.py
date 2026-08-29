"""Structured logging, tracing, and cost ledger hooks."""
import logging
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

from app.platform.config import get_settings

_settings = get_settings()

# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer() if _settings.is_production
        else structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    wrapper_class=structlog.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def get_logger(name: str = __name__) -> Any:
    return structlog.get_logger(name)


def log_cost_event(
    household_id: str,
    event_type: str,  # llm_tokens | whatsapp_conversation | maps_api | stt_minutes
    amount_cents: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a cost event for tracking unit economics per household."""
    logger.info(
        "cost_event",
        household_id=household_id,
        event_type=event_type,
        amount_cents=amount_cents,
        **(metadata or {}),
    )
    # In production this would also write to platform.cost_ledger via the DB


def log_audit_event(
    action: str,
    actor_type: str,
    actor_id: str | None,
    household_id: str | None,
    target_type: str | None,
    target_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an audit event. Repository implementations write to audit_log table."""
    logger.info(
        "audit_event",
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        household_id=household_id,
        target_type=target_type,
        target_id=target_id,
        **(metadata or {}),
    )
