"""Briefing background tasks."""
import uuid
from datetime import date, datetime, timezone

import procrastinate
from app.platform.db import AsyncSessionFactory
from app.platform.observability import get_logger
from app.platform.queue import QUEUE_SCHEDULED, async_queue_app

logger = get_logger(__name__)


@async_queue_app.task(queue=QUEUE_SCHEDULED, retry=procrastinate.RetryStrategy(max_attempts=3))
async def generate_briefing_task(household_id: str) -> None:
    """Generate and send the daily briefing for a household."""
    from app.modules.briefing.generator import BriefingGenerator
    from app.modules.briefing.models import BriefingDelivery
    from app.modules.identity.api import IdentityService
    from app.modules.notification.api import NotificationService
    from app.contracts.events import BriefingReady
    from app.platform.events.bus import EventPublisher

    hh_uuid = uuid.UUID(household_id)
    today = date.today()

    async with AsyncSessionFactory() as session:
        try:
            generator = BriefingGenerator(session)
            briefing = await generator.generate(household_id=hh_uuid, briefing_date=today)

            # Get primary member for delivery
            identity_svc = IdentityService(session)
            members = await identity_svc.list_adult_members(hh_uuid)
            primary = next((m for m in members if m.role == "primary_admin"), None)

            # Format WhatsApp message
            message = await generator.format_whatsapp_message(briefing)

            # Create delivery record
            if primary:
                delivery = BriefingDelivery(
                    briefing_id=briefing.id,
                    household_id=hh_uuid,
                    household_member_id=primary.id,
                    channel="whatsapp",
                    status="queued",
                )
                session.add(delivery)

            # Publish event
            publisher = EventPublisher(session)
            await publisher.publish(BriefingReady(
                briefing_id=briefing.id,
                household_id=hh_uuid,
                briefing_date=today.isoformat(),
            ))

            await session.commit()

            logger.info(
                "briefing_task_complete",
                household_id=household_id,
                briefing_id=str(briefing.id),
            )

        except Exception as e:
            logger.error("briefing_task_failed", household_id=household_id, error=str(e))
            await session.rollback()
            raise
