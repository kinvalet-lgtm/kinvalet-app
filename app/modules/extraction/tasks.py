"""Extraction background tasks — procrastinate job definitions.

These run in the worker process, separate from the API process.
A 6-second LLM call must never occupy a request handler that owes
Twilio a webhook response in under 500ms.
"""
import uuid

import procrastinate

from app.platform.db import AsyncSessionFactory
from app.platform.observability import get_logger
from app.platform.queue import QUEUE_EXTRACTION, async_queue_app

logger = get_logger(__name__)


@async_queue_app.task(queue=QUEUE_EXTRACTION, retry=procrastinate.RetryStrategy(max_attempts=3))
async def extract_message_task(
    message_id: str,
    household_id: str,
    member_id: str,
) -> None:
    """Extract structured data from an inbound message.

    Enqueued by the inbound webhook handler; runs in the worker.
    Commits the extraction result and publishes ExtractionCompleted event.
    """
    from sqlalchemy import select
    from app.modules.inbound.models import InboundMessage
    from app.modules.extraction.orchestrator import ExtractionOrchestrator
    from app.contracts.events import ExtractionCompleted, ClarificationNeeded
    from app.platform.events.bus import EventPublisher
    from app.platform.context import set_request_context

    msg_uuid = uuid.UUID(message_id)
    hh_uuid = uuid.UUID(household_id)
    member_uuid = uuid.UUID(member_id)

    set_request_context(
        household_id=household_id,
        member_id=member_id,
        actor_type="system_agent",
    )

    async with AsyncSessionFactory() as session:
        try:
            # Fetch the inbound message
            result = await session.execute(
                select(InboundMessage).where(InboundMessage.id == msg_uuid)
            )
            msg = result.scalar_one_or_none()
            if msg is None:
                logger.error("extraction_message_not_found", message_id=message_id)
                return

            # Fetch household members for context
            from app.modules.identity.api import IdentityService
            identity_svc = IdentityService(session)
            members = await identity_svc.list_adult_members(hh_uuid)
            member_context = [
                {"display_name": m.display_name, "role": m.role, "id": str(m.id)}
                for m in members
            ]

            orchestrator = ExtractionOrchestrator(session)
            extraction = await orchestrator.extract(
                message_id=msg_uuid,
                household_id=hh_uuid,
                member_id=member_uuid,
                raw_text=msg.raw_text,
                media_type=msg.media_type,
                media_url=msg.raw_media_url,
                household_members=member_context,
            )

            # Publish ExtractionCompleted event via outbox
            publisher = EventPublisher(session)
            await publisher.publish(ExtractionCompleted(
                extraction_result_id=extraction.id,
                inbound_message_id=msg_uuid,
                household_id=hh_uuid,
                confidence_score=float(extraction.confidence_score),
                requires_human_review=extraction.requires_human_review,
            ))

            # Update inbound message status
            from sqlalchemy import update
            await session.execute(
                update(InboundMessage)
                .where(InboundMessage.id == msg_uuid)
                .values(status="extracted")
            )

            await session.commit()

            # If human review not needed, trigger operations item creation
            if not extraction.requires_human_review:
                await create_items_from_extraction_task.defer_async(
                    extraction_result_id=str(extraction.id),
                    household_id=household_id,
                    member_id=member_id,
                )

        except Exception as e:
            logger.error(
                "extraction_task_failed",
                message_id=message_id,
                error=str(e),
                exc_info=True,
            )
            await session.rollback()
            raise  # Let procrastinate handle retry


@async_queue_app.task(queue=QUEUE_EXTRACTION, retry=procrastinate.RetryStrategy(max_attempts=3))
async def create_items_from_extraction_task(
    extraction_result_id: str,
    household_id: str,
    member_id: str,
) -> None:
    """Create operational items from an extraction result.

    Called after extraction completes (either auto or after ops review).
    """
    from sqlalchemy import select
    from app.modules.extraction.models import ExtractionResult
    from app.platform.context import set_request_context

    set_request_context(
        household_id=household_id,
        member_id=member_id,
        actor_type="system_agent",
    )

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(ExtractionResult)
            .where(ExtractionResult.id == uuid.UUID(extraction_result_id))
        )
        extraction = result.scalar_one_or_none()
        if extraction is None:
            logger.error("extraction_result_not_found", extraction_id=extraction_result_id)
            return

        tool_calls = extraction.extracted_json.get("tool_calls", [])
        items_created = 0

        for tc in tool_calls:
            if tc.get("name") == "create_draft_item":
                args = tc.get("arguments", {})
                try:
                    await _create_item_from_tool_call(
                        session=session,
                        args=args,
                        extraction_id=uuid.UUID(extraction_result_id),
                        household_id=uuid.UUID(household_id),
                        inbound_message_id=extraction.inbound_message_id,
                    )
                    items_created += 1
                except Exception as e:
                    logger.error(
                        "item_creation_failed",
                        tool_call=tc,
                        error=str(e),
                    )

        await session.commit()
        logger.info(
            "items_created_from_extraction",
            extraction_id=extraction_result_id,
            count=items_created,
        )


async def _create_item_from_tool_call(
    session,
    args: dict,
    extraction_id: uuid.UUID,
    household_id: uuid.UUID,
    inbound_message_id: uuid.UUID,
) -> None:
    """Create a single OperationalItem from a create_draft_item tool call."""
    from app.modules.operations.models import OperationalItem
    from datetime import datetime

    start_at = None
    if args.get("start_at"):
        start_at = datetime.fromisoformat(args["start_at"].replace("Z", "+00:00"))

    end_at = None
    if args.get("end_at"):
        end_at = datetime.fromisoformat(args["end_at"].replace("Z", "+00:00"))

    cost_cents = args.get("cost_cents")
    requires_approval = cost_cents is not None and cost_cents > 0

    item = OperationalItem(
        household_id=household_id,
        category=args["category"],
        title=args["title"],
        source_inbound_message_id=inbound_message_id,
        start_at=start_at,
        end_at=end_at,
        location=args.get("location"),
        cost_cents=cost_cents,
        requires_approval=requires_approval,
        status="pending_confirmation" if not requires_approval else "pending_approval",
        priority="normal",
    )

    if args.get("assigned_to_member_id"):
        item.assigned_to_member_id = uuid.UUID(args["assigned_to_member_id"])
    if args.get("about_member_id"):
        item.about_member_id = uuid.UUID(args["about_member_id"])

    session.add(item)
    await session.flush()

    # Publish ItemCreated event
    from app.contracts.events import ItemCreated
    from app.platform.events.bus import EventPublisher

    publisher = EventPublisher(session)
    await publisher.publish(ItemCreated(
        operational_item_id=item.id,
        household_id=household_id,
        category=item.category,
        assigned_to_member_id=item.assigned_to_member_id,
        start_at=item.start_at,
        location=item.location,
    ))
