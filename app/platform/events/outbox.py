"""Transactional outbox — events commit atomically with the domain write.

Why the outbox matters:
1. Correctness: event + data commit together or neither does. No phantom events.
2. Extraction path: when notification becomes its own service, the dispatcher
   publishes to a broker instead — publishing code and event schemas unchanged.

Handlers must be idempotent (at-least-once delivery).
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.platform.observability import get_logger

logger = get_logger(__name__)


class PlatformBase(DeclarativeBase):
    pass


class OutboxEvent(PlatformBase):
    __tablename__ = "outbox"
    __table_args__ = {"schema": "platform"}

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventDedupeRecord(PlatformBase):
    __tablename__ = "event_dedupe"
    __table_args__ = {"schema": "platform"}

    event_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    handler_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


async def add_event_to_outbox(
    session: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    event_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Add an event to the outbox within the current session's transaction.

    Must be called inside an active transaction — the event will commit atomically
    with whatever domain write the caller is performing.
    """
    eid = event_id or uuid.uuid4()
    event = OutboxEvent(
        id=eid,
        event_type=event_type,
        payload=json.dumps(payload, default=str),
    )
    session.add(event)
    logger.debug("outbox_event_added", event_type=event_type, event_id=str(eid))
    return eid


async def is_already_processed(
    session: AsyncSession,
    event_id: uuid.UUID,
    handler_name: str,
) -> bool:
    """Check if this handler already processed this event (deduplication)."""
    result = await session.execute(
        text(
            "SELECT 1 FROM platform.event_dedupe "
            "WHERE event_id = :eid AND handler_name = :handler"
        ),
        {"eid": event_id, "handler": handler_name},
    )
    return result.scalar() is not None


async def mark_processed(
    session: AsyncSession,
    event_id: uuid.UUID,
    handler_name: str,
) -> None:
    """Record that this handler processed this event."""
    record = EventDedupeRecord(event_id=event_id, handler_name=handler_name)
    session.add(record)
