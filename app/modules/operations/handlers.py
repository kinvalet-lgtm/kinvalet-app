"""Operations event handlers.

Triggered when items are created/confirmed — runs conflict detection and skill scheduling.
Idempotent: all handlers check event_dedupe before running.
"""
from app.platform.events.registry import subscribe
from app.platform.observability import get_logger

logger = get_logger(__name__)


@subscribe(type("ItemCreated", (), {}))
async def on_item_created(payload: dict) -> None:
    """When an item is created: run conflict detection + schedule skills."""
    from app.platform.db import AsyncSessionFactory
    from app.platform.context import set_request_context
    import uuid

    household_id = payload.get("household_id")
    item_id = payload.get("operational_item_id")
    if not household_id or not item_id:
        return

    set_request_context(household_id=household_id, actor_type="system_agent")

    async with AsyncSessionFactory() as session:
        from sqlalchemy import select
        from app.modules.operations.models import OperationalItem

        result = await session.execute(
            select(OperationalItem)
            .where(OperationalItem.id == uuid.UUID(item_id))
        )
        item = result.scalar_one_or_none()
        if item is None:
            return

        # Only run conflict detection on time-bound items with an assignee
        if item.start_at and item.assigned_to_member_id:
            try:
                from app.modules.operations.services.conflict_detection import ConflictDetector
                from app.modules.connectors.api import ConnectorsService
                from app.modules.identity.api import IdentityService
                from app.platform.events.bus import EventPublisher

                connectors_svc = ConnectorsService(session)
                identity_svc = IdentityService(session)
                publisher = EventPublisher(session)

                detector = ConflictDetector(session, connectors_svc, identity_svc, publisher)
                await detector.check(item)
            except Exception as e:
                logger.error("conflict_detection_failed", item_id=item_id, error=str(e))

        # Schedule applicable skills
        try:
            from app.modules.skills.api import SkillsService
            skills_svc = SkillsService(session)
            await skills_svc.schedule_skills_for_item(item.id, item.household_id)
        except Exception as e:
            logger.error("skill_scheduling_failed", item_id=item_id, error=str(e))

        await session.commit()


@subscribe(type("ItemConfirmed", (), {}))
async def on_item_confirmed(payload: dict) -> None:
    """When item is confirmed: sync to calendar."""
    from app.platform.db import AsyncSessionFactory
    from app.platform.context import set_request_context
    import uuid

    household_id = payload.get("household_id")
    item_id = payload.get("operational_item_id")
    if not household_id or not item_id:
        return

    set_request_context(household_id=household_id, actor_type="system_agent")

    async with AsyncSessionFactory() as session:
        from sqlalchemy import select, update
        from app.modules.operations.models import OperationalItem

        result = await session.execute(
            select(OperationalItem)
            .where(OperationalItem.id == uuid.UUID(item_id))
        )
        item = result.scalar_one_or_none()
        if not item or not item.start_at or not item.assigned_to_member_id:
            return

        try:
            from app.modules.connectors.api import ConnectorsService
            connectors_svc = ConnectorsService(session)
            provider_event_id = await connectors_svc.create_calendar_event(
                member_id=item.assigned_to_member_id,
                title=item.title,
                start_at=item.start_at,
                end_at=item.end_at,
                location=item.location,
            )
            if provider_event_id:
                await session.execute(
                    update(OperationalItem)
                    .where(OperationalItem.id == item.id)
                    .values(
                        calendar_provider_event_id=provider_event_id,
                        calendar_sync_status="synced",
                    )
                )
                await session.commit()
        except Exception as e:
            from sqlalchemy import update
            await session.execute(
                update(OperationalItem)
                .where(OperationalItem.id == item.id)
                .values(calendar_sync_status="sync_failed")
            )
            await session.commit()
            logger.error("calendar_sync_failed", item_id=item_id, error=str(e))


@subscribe(type("DelegationAccepted", (), {}))
async def on_delegation_accepted(payload: dict) -> None:
    """When delegation accepted: reassign item, cancel old skills, schedule new ones."""
    from app.platform.db import AsyncSessionFactory
    from app.platform.context import set_request_context
    import uuid

    household_id = payload.get("household_id")
    item_id = payload.get("operational_item_id")
    delegation_id = payload.get("delegation_id")
    new_member_id = payload.get("delegated_to_member_id")

    if not all([household_id, item_id, new_member_id]):
        return

    set_request_context(household_id=household_id, actor_type="system_agent")

    async with AsyncSessionFactory() as session:
        from sqlalchemy import select, update
        from app.modules.operations.models import OperationalItem, TaskDelegation
        from datetime import datetime, timezone

        # Reassign item
        await session.execute(
            update(OperationalItem)
            .where(OperationalItem.id == uuid.UUID(item_id))
            .values(
                assigned_to_member_id=uuid.UUID(new_member_id),
                updated_at=datetime.now(timezone.utc),
            )
        )

        # Mark delegation completed
        await session.execute(
            update(TaskDelegation)
            .where(TaskDelegation.id == uuid.UUID(delegation_id))
            .values(status="accepted", responded_at=datetime.now(timezone.utc))
        )

        # Cancel old skill executions, schedule new ones (AC 8.2)
        from app.modules.skills.api import SkillsService
        skills_svc = SkillsService(session)
        await skills_svc.cancel_skills_for_item(uuid.UUID(item_id))

        result = await session.execute(
            select(OperationalItem).where(OperationalItem.id == uuid.UUID(item_id))
        )
        item = result.scalar_one_or_none()
        if item:
            await skills_svc.schedule_skills_for_item(item.id, item.household_id)

        await session.commit()
