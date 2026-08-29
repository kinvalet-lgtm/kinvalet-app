"""Integration: Recurrence materialization (§11.18).

Tests that occurrences are created on the rolling 60-day horizon
and that each occurrence is independently assignable/completable.
AC 11.18.1: User says 'every Tuesday' → occurrences materialize.
AC 11.18.2: Edit one occurrence → only that one changes.
"""
import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_weekly_recurrence_materializes(db_session):
    """FREQ=WEEKLY;BYDAY=TU creates occurrences on the 60-day horizon."""
    from app.modules.operations.models import OperationalItem, RecurrenceRule
    from app.modules.operations.services.recurrence import RecurrenceMaterializer
    from app.platform.context import set_request_context

    hh_id = uuid.uuid4()
    set_request_context(household_id=str(hh_id), actor_type="system_agent")

    rule = RecurrenceRule(
        household_id=hh_id,
        template_title="Leo's soccer practice",
        category="kids_logistics",
        location="Field B",
        rrule="FREQ=WEEKLY;BYDAY=TU",
        start_time="18:00",
        duration_minutes=90,
        until_date=None,
        status="active",
    )
    db_session.add(rule)
    await db_session.flush()

    materializer = RecurrenceMaterializer(db_session)
    created = await materializer.materialize_rule(rule)
    await db_session.flush()

    # Should have created several Tuesday occurrences
    assert created > 0, "Should have materialized at least one Tuesday"
    assert created <= 9, "60-day horizon has at most 9 Tuesdays"

    # Each occurrence is a separate OperationalItem row
    from sqlalchemy import select
    result = await db_session.execute(
        select(OperationalItem)
        .where(OperationalItem.recurrence_parent_id == rule.id)
        .order_by(OperationalItem.occurrence_date)
    )
    items = result.scalars().all()
    assert len(items) == created

    # Each occurrence has the correct fields
    for item in items:
        assert item.title == "Leo's soccer practice"
        assert item.category == "kids_logistics"
        assert item.recurrence_parent_id == rule.id
        assert item.occurrence_date is not None
        # Occurrences are Tuesdays (weekday 1)
        assert item.occurrence_date.weekday() == 1, f"Expected Tuesday, got {item.occurrence_date}"


@pytest.mark.asyncio
async def test_no_duplicate_materialization(db_session):
    """Running materialization twice doesn't create duplicate occurrences."""
    from app.modules.operations.models import RecurrenceRule, OperationalItem
    from app.modules.operations.services.recurrence import RecurrenceMaterializer
    from app.platform.context import set_request_context
    from sqlalchemy import select, func

    hh_id = uuid.uuid4()
    set_request_context(household_id=str(hh_id), actor_type="system_agent")

    rule = RecurrenceRule(
        household_id=hh_id,
        template_title="Dad's dialysis",
        category="parent_care",
        rrule="FREQ=WEEKLY;BYDAY=MO,TH",
        start_time="09:00",
        duration_minutes=240,
        status="active",
    )
    db_session.add(rule)
    await db_session.flush()

    materializer = RecurrenceMaterializer(db_session)
    created1 = await materializer.materialize_rule(rule)
    await db_session.flush()

    # Run again — should create 0 new (already materialized through horizon)
    created2 = await materializer.materialize_rule(rule)
    await db_session.flush()

    assert created2 == 0, "Second run should not create duplicates"

    result = await db_session.execute(
        select(func.count()).select_from(OperationalItem)
        .where(OperationalItem.recurrence_parent_id == rule.id)
    )
    assert result.scalar() == created1


@pytest.mark.asyncio
async def test_occurrences_are_independently_completable(db_session):
    """Each occurrence can be completed without affecting the series."""
    from app.modules.operations.models import RecurrenceRule, OperationalItem
    from app.modules.operations.services.recurrence import RecurrenceMaterializer
    from app.platform.context import set_request_context
    from sqlalchemy import select

    hh_id = uuid.uuid4()
    set_request_context(household_id=str(hh_id), actor_type="system_agent")

    rule = RecurrenceRule(
        household_id=hh_id,
        template_title="Weekly grocery run",
        category="household_admin",
        rrule="FREQ=WEEKLY;BYDAY=SA",
        start_time="10:00",
        duration_minutes=60,
        status="active",
    )
    db_session.add(rule)
    await db_session.flush()

    materializer = RecurrenceMaterializer(db_session)
    await materializer.materialize_rule(rule)
    await db_session.flush()

    result = await db_session.execute(
        select(OperationalItem)
        .where(OperationalItem.recurrence_parent_id == rule.id)
        .order_by(OperationalItem.occurrence_date)
        .limit(3)
    )
    items = result.scalars().all()
    assert len(items) >= 2

    # Complete the first occurrence
    items[0].status = "completed"
    await db_session.flush()

    # Other occurrences are unaffected
    for item in items[1:]:
        assert item.status == "pending_confirmation"

    # Rule itself is still active
    await db_session.refresh(rule)
    assert rule.status == "active"
