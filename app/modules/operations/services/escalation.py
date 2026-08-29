"""Priority escalation ladder for critical items (§11.20).

Escalation steps for `priority=critical` items when unacknowledged:
1. Reminder to assignee at normal lead time
2. At 50% of remaining time → second reminder, marked urgent
3. Under 30 minutes remaining → notify another adult member (delegation proposal)
4. Deadline passed → log as missed critical item (distinct metric)

The missed-critical-item rate is the honest measure of whether this product
carries the weight a family hands it (PRD §11.20 ★).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operations.models import OperationalItem, TaskDelegation
from app.platform.observability import get_logger

logger = get_logger(__name__)

# Cap: no more than 20% of items per household per week can be critical
CRITICAL_WEEKLY_CAP_PERCENT = 0.20


class EscalationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run_escalation_check(self, household_id: uuid.UUID) -> None:
        """Check all critical items in a household and escalate as needed."""
        now = datetime.now(timezone.utc)

        result = await self._session.execute(
            select(OperationalItem)
            .where(OperationalItem.household_id == household_id)
            .where(OperationalItem.priority == "critical")
            .where(OperationalItem.status.in_(["confirmed", "pending_confirmation"]))
            .where(OperationalItem.is_archived.is_(False))
            .where(OperationalItem.start_at > now - timedelta(hours=1))  # near or future
        )
        items = result.scalars().all()

        for item in items:
            await self._escalate_item(item, now)

    async def _escalate_item(self, item: OperationalItem, now: datetime) -> None:
        if not item.start_at:
            return

        time_remaining = item.start_at - now
        total_window = item.start_at - item.created_at.replace(tzinfo=timezone.utc)

        if time_remaining.total_seconds() < 0:
            # Deadline passed — log as missed critical
            await self._mark_missed_critical(item)
            return

        if time_remaining < timedelta(minutes=30) and item.assigned_to_member_id:
            # Step 3: Notify another adult
            await self._escalate_to_another_adult(item)

        elif time_remaining < total_window / 2:
            # Step 2: Second urgent reminder
            await self._send_urgent_reminder(item)

    async def _mark_missed_critical(self, item: OperationalItem) -> None:
        """Log missed critical item — surfaced in next briefing as a distinct metric."""
        logger.warning(
            "missed_critical_item",
            item_id=str(item.id),
            title=item.title,
            household_id=str(item.household_id),
            start_at=item.start_at.isoformat() if item.start_at else None,
        )
        # Publish event so notification module surfaces it in next briefing
        from app.contracts.events import ItemCancelled
        from app.platform.events.outbox import add_event_to_outbox
        await add_event_to_outbox(self._session, "MissedCriticalItem", {
            "operational_item_id": str(item.id),
            "household_id": str(item.household_id),
            "title": item.title,
        })

    async def _escalate_to_another_adult(self, item: OperationalItem) -> None:
        """Create an emergency delegation proposal to another free adult (step 3)."""
        # Check if a delegation already exists for this item
        existing = await self._session.execute(
            select(TaskDelegation)
            .where(TaskDelegation.operational_item_id == item.id)
            .where(TaskDelegation.status == "proposed")
        )
        if existing.scalar_one_or_none():
            return  # Already escalated

        from app.contracts.events import DelegationProposed
        from app.platform.events.outbox import add_event_to_outbox

        # In full impl: query identity API for other adults, check connectors for free ones
        # For now, publish the escalation event and let handlers pick it up
        await add_event_to_outbox(self._session, "CriticalEscalationNeeded", {
            "operational_item_id": str(item.id),
            "household_id": str(item.household_id),
            "title": item.title,
            "current_assignee_id": str(item.assigned_to_member_id),
        })
        logger.warning(
            "critical_escalation_step3",
            item_id=str(item.id),
            title=item.title,
        )

    async def _send_urgent_reminder(self, item: OperationalItem) -> None:
        """Send a second urgent reminder (step 2)."""
        from app.platform.events.outbox import add_event_to_outbox
        await add_event_to_outbox(self._session, "UrgentReminderNeeded", {
            "operational_item_id": str(item.id),
            "household_id": str(item.household_id),
            "title": item.title,
            "assignee_id": str(item.assigned_to_member_id),
            "priority": "critical",
        })


async def run_archive_scheduler(household_id: uuid.UUID, session: AsyncSession) -> int:
    """Auto-archive completed items older than 30 days (§11.12).

    archived_by_member_id=NULL means system auto-archive (distinguishable from manual).
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    result = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.household_id == household_id)
        .where(OperationalItem.status == "completed")
        .where(OperationalItem.is_archived.is_(False))
        .where(OperationalItem.updated_at < cutoff)
    )
    items = result.scalars().all()

    count = 0
    now = datetime.now(timezone.utc)
    for item in items:
        item.is_archived = True
        item.archived_at = now
        item.archived_by_member_id = None  # NULL = system auto-archive
        count += 1

    if count:
        logger.info("auto_archived", count=count, household_id=str(household_id))

    return count
