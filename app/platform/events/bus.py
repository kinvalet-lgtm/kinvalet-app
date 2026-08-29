"""Event bus — dispatches outbox events to registered handlers.

The dispatcher polls platform.outbox every few seconds, delivers to handlers,
marks published. Failures retry with backoff; permanent failures → dead-letter.

When a module is extracted to its own service, this dispatcher publishes to
a broker instead — event schemas and all handlers remain unchanged.
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import AsyncSessionFactory
from app.platform.events.outbox import OutboxEvent, is_already_processed, mark_processed
from app.platform.events.registry import get_handlers
from app.platform.observability import get_logger

logger = get_logger(__name__)

MAX_RETRY_COUNT = 5
POLL_INTERVAL_SECONDS = 3
BATCH_SIZE = 50


class EventPublisher:
    """Dependency-injected into domain services for publishing events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(self, event: Any) -> None:
        """Publish an event to the outbox within the current transaction."""
        from app.platform.events.outbox import add_event_to_outbox

        event_type = type(event).__name__
        payload = event.model_dump() if hasattr(event, "model_dump") else vars(event)
        await add_event_to_outbox(self._session, event_type, payload)


async def dispatch_pending_events() -> None:
    """Poll outbox and dispatch unpublished events to registered handlers.

    Called by the worker process on a fixed interval.
    """
    async with AsyncSessionFactory() as session:
        # Fetch pending events with a non-blocking lock
        result = await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.published.is_(False))
            .where(OutboxEvent.retry_count < MAX_RETRY_COUNT)
            .order_by(OutboxEvent.created_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        events = result.scalars().all()

        for event in events:
            await _dispatch_event(session, event)

        await session.commit()


async def _dispatch_event(session: AsyncSession, event: OutboxEvent) -> None:
    handlers = get_handlers(event.event_type)
    payload = json.loads(event.payload)
    event_id = event.id

    if not handlers:
        # No handlers registered — mark published (not an error)
        event.published = True
        event.published_at = datetime.now(timezone.utc)
        logger.debug("event_no_handlers", event_type=event.event_type, event_id=str(event_id))
        return

    all_succeeded = True
    for handler in handlers:
        handler_name = handler.__name__

        # Idempotency check: skip if already processed by this handler
        if await is_already_processed(session, event_id, handler_name):
            logger.debug(
                "event_already_processed",
                event_type=event.event_type,
                handler=handler_name,
            )
            continue

        try:
            await handler(payload)
            await mark_processed(session, event_id, handler_name)
            logger.info(
                "event_dispatched",
                event_type=event.event_type,
                handler=handler_name,
            )
        except Exception as exc:
            all_succeeded = False
            event.retry_count += 1
            event.last_error = str(exc)
            logger.error(
                "event_dispatch_failed",
                event_type=event.event_type,
                handler=handler_name,
                error=str(exc),
                retry_count=event.retry_count,
            )

    if all_succeeded:
        event.published = True
        event.published_at = datetime.now(timezone.utc)


async def run_dispatcher_loop() -> None:
    """Long-running loop for the worker process."""
    logger.info("event_dispatcher_started", poll_interval=POLL_INTERVAL_SECONDS)
    while True:
        try:
            await dispatch_pending_events()
        except Exception as exc:
            logger.error("dispatcher_loop_error", error=str(exc))
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
