"""Briefing scheduler — fires daily briefing at 8:00 AM per household timezone.

Runs in the worker via a 30-second poll loop.
Uses database-backed scheduling (not APScheduler / cron) so:
- Schedules survive restarts and deploys
- Cancellation is an UPDATE
- DST is handled natively via IANA timezone (§11.7)

DST edge case: IANA zones are explicit so 8:00 AM stays 8:00 AM local time
across spring/fall transitions. Must be covered by tests at both US DST dates.
"""
from datetime import date, datetime, timedelta, timezone

import pytz
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.briefing.models import Briefing
from app.platform.db import AsyncSessionFactory
from app.platform.observability import get_logger

logger = get_logger(__name__)

BRIEFING_HOUR = 8   # 8:00 AM local
WINDOW_MINUTES = 3  # ±3 minute window per architecture spec


async def run_briefing_scheduler() -> None:
    """Check which households are due for their 8 AM briefing and enqueue them."""
    async with AsyncSessionFactory() as session:
        due_households = await _find_due_households(session)
        for household_id, tz_name in due_households:
            # Avoid duplicate briefings for the same day
            today_local = _local_today(tz_name)
            existing = await session.execute(
                select(Briefing)
                .where(Briefing.household_id == household_id)
                .where(Briefing.briefing_date == today_local)
            )
            if existing.scalar_one_or_none():
                continue

            # Enqueue briefing generation
            try:
                from app.modules.briefing.tasks import generate_briefing_task
                await generate_briefing_task.defer_async(household_id=str(household_id))
                logger.info("briefing_enqueued", household_id=str(household_id), tz=tz_name)
            except Exception as e:
                logger.error("briefing_enqueue_failed", household_id=str(household_id), error=str(e))


async def _find_due_households(session: AsyncSession) -> list[tuple]:
    """Find households whose local time is within 8:00 AM ±3 minutes."""
    now_utc = datetime.now(timezone.utc)

    # Query active households
    result = await session.execute(
        text("""
            SELECT id, timezone
            FROM identity.household
            WHERE status = 'active'
        """)
    )
    rows = result.fetchall()

    due = []
    for row in rows:
        household_id = row[0]
        tz_name = row[1]
        try:
            tz = pytz.timezone(tz_name)
            local_now = now_utc.astimezone(tz)
            # Check if we're within the ±3 minute window of 8:00 AM
            target = local_now.replace(hour=BRIEFING_HOUR, minute=0, second=0, microsecond=0)
            diff_minutes = abs((local_now - target).total_seconds() / 60)
            if diff_minutes <= WINDOW_MINUTES:
                due.append((household_id, tz_name))
        except pytz.exceptions.UnknownTimeZoneError:
            logger.warning("unknown_timezone", household_id=str(household_id), tz=tz_name)

    return due


def _local_today(tz_name: str) -> date:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


async def send_welcome_briefing(household_id: str, session: AsyncSession) -> None:
    """Send an immediate welcome summary when a household signs up after 8 AM (§11.7)."""
    try:
        from app.modules.briefing.tasks import generate_briefing_task
        await generate_briefing_task.defer_async(household_id=household_id)
        logger.info("welcome_briefing_enqueued", household_id=household_id)
    except Exception as e:
        logger.error("welcome_briefing_failed", household_id=household_id, error=str(e))
