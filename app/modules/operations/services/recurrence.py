"""Recurrence materialization service (§11.18).

Materializes occurrences on a rolling 60-day horizon.
Each occurrence is an independent OperationalItem linked by recurrence_parent_id.

Why materialize rows (not compute on the fly):
Each occurrence needs its own assignee, delegation, conflict check,
completion state, and leave-by ETA. A virtual occurrence can't carry
'Mark took this specific Tuesday.' (Architecture §11.18 ★)

RRULE subset supported: daily, weekly with weekday set, monthly by date.
All times interpreted in the household's IANA timezone so local time
is stable across DST transitions.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pytz
from dateutil.rrule import rrulestr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operations.models import OperationalItem, RecurrenceRule
from app.platform.observability import get_logger

logger = get_logger(__name__)

ROLLING_HORIZON_DAYS = 60


class RecurrenceMaterializer:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def materialize_all_active(self, household_id: uuid.UUID) -> int:
        """Extend materialization for all active recurrence rules in a household."""
        result = await self._session.execute(
            select(RecurrenceRule)
            .where(RecurrenceRule.household_id == household_id)
            .where(RecurrenceRule.status == "active")
        )
        rules = result.scalars().all()
        total = 0
        for rule in rules:
            total += await self.materialize_rule(rule)
        return total

    async def materialize_rule(self, rule: RecurrenceRule) -> int:
        """Materialize occurrences for a single rule up to the rolling horizon."""
        tz = pytz.timezone(_get_household_timezone(rule.household_id))  # type: ignore
        horizon = date.today() + timedelta(days=ROLLING_HORIZON_DAYS)

        # Start from where we left off (materialized_through + 1 day)
        start_date = (
            rule.materialized_through + timedelta(days=1)
            if rule.materialized_through
            else date.today()
        )

        if rule.until_date and start_date > rule.until_date:
            await self._end_rule(rule)
            return 0

        # Parse the RRULE and generate occurrences.
        # Use naive datetimes throughout to avoid offset-naive/offset-aware comparison errors.
        # We convert back to timezone-aware when writing to the DB below.
        try:
            start_naive = datetime.combine(start_date, _parse_time(rule.start_time))
            end_naive = datetime.combine(horizon, _parse_time(rule.start_time))
            occurrences = list(rrulestr(
                f"DTSTART:{start_naive.strftime('%Y%m%dT%H%M%S')}\n{rule.rrule}",
                ignoretz=True,
            ).between(start_naive, end_naive, inc=True))
        except Exception as e:
            logger.error("rrule_parse_failed", rule_id=str(rule.id), error=str(e))
            return 0

        created = 0
        for occurrence_dt in occurrences:
            occ_date = occurrence_dt.date()

            # Check for already-materialized occurrence on this date
            existing = await self._session.execute(
                select(OperationalItem)
                .where(OperationalItem.recurrence_parent_id == rule.id)
                .where(OperationalItem.occurrence_date == occ_date)
            )
            if existing.scalar_one_or_none():
                continue

            start_at = tz.localize(datetime.combine(occ_date, _parse_time(rule.start_time)))
            end_at = start_at + timedelta(minutes=rule.duration_minutes)

            item = OperationalItem(
                id=uuid.uuid4(),
                household_id=rule.household_id,
                category=rule.category,
                title=rule.template_title,
                location=rule.location,
                assigned_to_member_id=rule.default_assignee_member_id,
                about_member_id=rule.about_member_id,
                start_at=start_at.astimezone(pytz.utc),
                end_at=end_at.astimezone(pytz.utc),
                recurrence_parent_id=rule.id,
                occurrence_date=occ_date,
                status="pending_confirmation",
                priority="normal",
            )
            self._session.add(item)
            created += 1

        # Update watermark
        if occurrences:
            await self._session.execute(
                update(RecurrenceRule)
                .where(RecurrenceRule.id == rule.id)
                .values(materialized_through=horizon)
            )

        if created:
            logger.info(
                "occurrences_materialized",
                rule_id=str(rule.id),
                count=created,
                horizon=horizon.isoformat(),
            )
        return created

    async def _end_rule(self, rule: RecurrenceRule) -> None:
        await self._session.execute(
            update(RecurrenceRule).where(RecurrenceRule.id == rule.id).values(status="ended")
        )


def _parse_time(time_str: str):
    """Parse HH:MM string into a time object."""
    from datetime import time
    h, m = time_str.split(":")
    return time(int(h), int(m))


def _get_household_timezone(household_id) -> str:
    """Placeholder — in production, query the identity module via interface."""
    return "America/New_York"
