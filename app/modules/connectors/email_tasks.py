"""Async email processing tasks — procrastinate jobs for extraction.

Queued by the inbound webhook, picked up by the worker.
Creates an inbound_message row and feeds it to the same LLM extraction
pipeline that processes WhatsApp messages.

The extracted items surface in:
  - The daily 8 AM briefing
  - The tasks board on the dashboard
  - Reminder/ETA skills (if time-bound with a location)
"""
import uuid
from datetime import datetime, timezone

import procrastinate

from app.platform.db import AsyncSessionFactory
from app.platform.observability import get_logger
from app.platform.queue import QUEUE_EXTRACTION, async_queue_app

logger = get_logger(__name__)


@async_queue_app.task(queue=QUEUE_EXTRACTION, retry=procrastinate.RetryStrategy(max_attempts=3))
async def process_email_task(
    email_log_id: str,
    household_id: str,
    member_id: str,
    subject: str,
    body: str,
    from_email: str,
) -> None:
    """Process a forwarded email through the extraction pipeline.

    Same pipeline as WhatsApp messages — the LLM agent sees:
      "Subject: Leo's field trip permission slip\nFrom: school@edu.com\n\n..."
    And creates operational items via the same tool schema.
    """
    from sqlalchemy import select, update
    from app.modules.inbound.models import InboundMessage
    from app.modules.connectors.email_inbound import InboundEmailLog
    from app.modules.extraction.orchestrator import ExtractionOrchestrator
    from app.modules.identity.api import IdentityService
    from app.platform.context import set_request_context

    set_request_context(household_id=household_id, member_id=member_id, actor_type="system_agent")

    async with AsyncSessionFactory() as session:
        try:
            # Create an inbound_message row (same table as WhatsApp messages)
            inbound_msg = InboundMessage(
                household_id=uuid.UUID(household_id),
                household_member_id=uuid.UUID(member_id) if member_id else None,
                source="email",
                provider_message_id=f"email:{email_log_id}",
                sender_email=from_email,
                media_type="text",
                raw_text=f"Subject: {subject}\nFrom: {from_email}\n\n{body}",
                status="processing",
            )
            session.add(inbound_msg)
            await session.flush()

            # Get household context for the LLM
            identity_svc = IdentityService(session)
            members = await identity_svc.list_adult_members(uuid.UUID(household_id))
            member_context = [
                {"display_name": m.display_name, "role": m.role, "id": str(m.id)}
                for m in members
            ]

            # Run LLM extraction (same orchestrator as WhatsApp)
            orchestrator = ExtractionOrchestrator(session)
            extraction = await orchestrator.extract(
                message_id=inbound_msg.id,
                household_id=uuid.UUID(household_id),
                member_id=uuid.UUID(member_id) if member_id else uuid.UUID(int=0),
                raw_text=inbound_msg.raw_text,
                media_type="text",
                household_members=member_context,
            )

            # Update inbound message status
            await session.execute(
                update(InboundMessage)
                .where(InboundMessage.id == inbound_msg.id)
                .values(status="extracted")
            )

            # Update email log
            await session.execute(
                update(InboundEmailLog)
                .where(InboundEmailLog.id == uuid.UUID(email_log_id))
                .values(
                    status="extracted",
                    processed_at=datetime.now(timezone.utc),
                    extraction_result_id=extraction.id,
                )
            )

            # If high-confidence, create operational items
            if not extraction.requires_human_review:
                from app.modules.extraction.tasks import create_items_from_extraction_task
                await create_items_from_extraction_task.defer_async(
                    extraction_result_id=str(extraction.id),
                    household_id=household_id,
                    member_id=member_id,
                )

            await session.commit()

            logger.info(
                "email_extraction_complete",
                email_log_id=email_log_id,
                confidence=float(extraction.confidence_score),
                requires_review=extraction.requires_human_review,
            )

        except Exception as e:
            logger.error("email_extraction_failed", email_log_id=email_log_id, error=str(e))
            # Mark as failed
            try:
                await session.execute(
                    update(InboundEmailLog)
                    .where(InboundEmailLog.id == uuid.UUID(email_log_id))
                    .values(status="failed")
                )
                await session.commit()
            except Exception:
                pass
            raise
