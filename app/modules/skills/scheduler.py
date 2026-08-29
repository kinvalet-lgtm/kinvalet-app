"""Skills scheduler — polls skill_executions table and fires due skills.

Runs in the worker process every 30 seconds.
Uses SELECT ... FOR UPDATE SKIP LOCKED to safely handle concurrent workers.

Two skill types:
1. reminder_notification — sends a WhatsApp reminder with one-tap Done
2. logistics_eta — calls Google Maps, computes leave_by_at, sends ETA message
   with a 30-45 minute re-check for traffic updates (§8.4)
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.skills.models import SkillExecution
from app.platform.db import AsyncSessionFactory
from app.platform.observability import get_logger

logger = get_logger(__name__)

BATCH_SIZE = 50
ETA_RECHECK_THRESHOLD_MINUTES = 5  # Re-send if ETA shifts by >5 min


async def run_skills_scheduler() -> None:
    """Called by worker every 30 seconds. Fires all due skill executions."""
    async with AsyncSessionFactory() as session:
        now = datetime.now(timezone.utc)

        result = await session.execute(
            select(SkillExecution)
            .where(SkillExecution.status == "scheduled")
            .where(SkillExecution.scheduled_for <= now)
            .order_by(SkillExecution.scheduled_for)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        due = result.scalars().all()

        for execution in due:
            try:
                await _fire_skill(session, execution)
            except Exception as e:
                logger.error(
                    "skill_execution_failed",
                    execution_id=str(execution.id),
                    skill_type=execution.skill_type_id,
                    error=str(e),
                )
                execution.retry_count += 1
                if execution.retry_count >= 3:
                    execution.status = "failed"
                    execution.failure_reason = str(e)
                else:
                    # Back off: retry in 5 minutes
                    execution.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=5)

        await session.commit()


async def _fire_skill(session: AsyncSession, execution: SkillExecution) -> None:
    """Dispatch to the appropriate skill handler."""
    if execution.skill_type_id == "reminder_notification":
        await _fire_reminder(session, execution)
    elif execution.skill_type_id == "logistics_eta":
        await _fire_logistics_eta(session, execution)
    else:
        logger.warning("unknown_skill_type", skill_type=execution.skill_type_id)
        execution.status = "failed"
        execution.failure_reason = f"Unknown skill type: {execution.skill_type_id}"


async def _fire_reminder(session: AsyncSession, execution: SkillExecution) -> None:
    """Send a WhatsApp reminder for an operational item."""
    from sqlalchemy import select as sa_select
    from app.modules.operations.models import OperationalItem

    result = await session.execute(
        sa_select(OperationalItem).where(OperationalItem.id == execution.operational_item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        execution.status = "failed"
        execution.failure_reason = "Item not found"
        return

    # Build reminder message
    time_str = item.start_at.strftime("%-I:%M %p") if item.start_at else ""
    location_str = f" at {item.location}" if item.location else ""
    message = f"Reminder: {item.title}{f' — {time_str}' if time_str else ''}{location_str}. ✅ Done"

    # Publish skill-fired event (notification module handles the send)
    from app.contracts.events import SkillFired
    from app.platform.events.outbox import add_event_to_outbox
    await add_event_to_outbox(session, "SkillFired", {
        "skill_execution_id": str(execution.id),
        "operational_item_id": str(execution.operational_item_id),
        "household_id": str(execution.household_id),
        "skill_type_id": "reminder_notification",
        "message": message,
        "assignee_member_id": str(item.assigned_to_member_id) if item.assigned_to_member_id else None,
    })

    execution.status = "executed"
    execution.executed_at = datetime.now(timezone.utc)
    execution.output_json = {"message": message}

    logger.info("reminder_fired", item_id=str(item.id), title=item.title)


async def _fire_logistics_eta(session: AsyncSession, execution: SkillExecution) -> None:
    """Call Google Maps, compute leave_by_at, send ETA message (§8.4)."""
    from sqlalchemy import select as sa_select
    from app.modules.operations.models import OperationalItem
    from app.modules.skills.models import HouseholdMemberAddress
    from app.modules.skills.services.logistics import compute_eta

    result = await session.execute(
        sa_select(OperationalItem).where(OperationalItem.id == execution.operational_item_id)
    )
    item = result.scalar_one_or_none()
    if item is None or not item.start_at or not item.location:
        execution.status = "failed"
        execution.failure_reason = "Item missing start_at or location"
        return

    # Get origin from stored output or fetch fresh
    prior_output = execution.output_json or {}
    origin = prior_output.get("origin_address")

    if not origin and item.assigned_to_member_id:
        addr_result = await session.execute(
            sa_select(HouseholdMemberAddress)
            .where(HouseholdMemberAddress.household_member_id == item.assigned_to_member_id)
            .where(HouseholdMemberAddress.label == "home")
        )
        addr = addr_result.scalar_one_or_none()
        if addr:
            origin = addr.address_text

    if not origin:
        # Fall back to reminder skill — log failure for ops visibility
        execution.status = "failed"
        execution.failure_reason = "no_origin_available"
        await _schedule_fallback_reminder(session, item)
        return

    try:
        eta_result = await compute_eta(
            origin=origin,
            destination=item.location,
            arrival_time=item.start_at,
        )

        # Check if ETA shifted significantly since last check (re-check logic)
        prior_eta = prior_output.get("eta_minutes")
        eta_changed = (
            prior_eta is not None
            and abs(eta_result["eta_minutes"] - prior_eta) > ETA_RECHECK_THRESHOLD_MINUTES
        )

        if eta_changed:
            # Mark prior execution superseded, this is the updated send
            logger.info(
                "eta_updated",
                item_id=str(item.id),
                old_eta=prior_eta,
                new_eta=eta_result["eta_minutes"],
            )

        leave_by = eta_result["leave_by_at"]
        message = (
            f"Traffic looks like {eta_result['eta_minutes']} min to {item.location} "
            f"— leave by {leave_by.strftime('%-I:%M %p')} for {item.title}."
        )

        # Check if item was created less than lead time before start (fire immediately)
        now = datetime.now(timezone.utc)
        if item.start_at - now < timedelta(minutes=15):
            message = f"Head out now — about {eta_result['eta_minutes']} min to {item.location}."

        from app.platform.events.outbox import add_event_to_outbox
        await add_event_to_outbox(session, "SkillFired", {
            "skill_execution_id": str(execution.id),
            "operational_item_id": str(execution.operational_item_id),
            "household_id": str(execution.household_id),
            "skill_type_id": "logistics_eta",
            "message": message,
            "assignee_member_id": str(item.assigned_to_member_id) if item.assigned_to_member_id else None,
            "eta_changed": eta_changed,
        })

        execution.status = "executed"
        execution.executed_at = datetime.now(timezone.utc)
        execution.output_json = {
            **eta_result,
            "origin_address": origin,
            "message": message,
        }

        # Schedule the re-check (30 min before leave_by_at)
        recheck_at = leave_by - timedelta(minutes=30)
        if recheck_at > datetime.now(timezone.utc):
            new_exec = SkillExecution(
                id=uuid.uuid4(),
                operational_item_id=execution.operational_item_id,
                household_id=execution.household_id,
                skill_type_id="logistics_eta",
                status="scheduled",
                scheduled_for=recheck_at,
                output_json={"origin_address": origin, "eta_minutes": eta_result["eta_minutes"]},
            )
            session.add(new_exec)

    except Exception as e:
        logger.error("logistics_eta_failed", item_id=str(item.id), error=str(e))
        execution.status = "failed"
        execution.failure_reason = str(e)
        await _schedule_fallback_reminder(session, item)


async def _schedule_fallback_reminder(session: AsyncSession, item) -> None:
    """Schedule a basic reminder when the Logistics skill can't run."""
    if not item.start_at:
        return
    lead_time = {"kids_logistics": 60, "parent_care": 90}.get(item.category, 30)
    scheduled_for = item.start_at - timedelta(minutes=lead_time)
    if scheduled_for <= datetime.now(timezone.utc):
        scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=1)

    session.add(SkillExecution(
        id=uuid.uuid4(),
        operational_item_id=item.id,
        household_id=item.household_id,
        skill_type_id="reminder_notification",
        status="scheduled",
        scheduled_for=scheduled_for,
    ))
    logger.info("fallback_reminder_scheduled", item_id=str(item.id))
