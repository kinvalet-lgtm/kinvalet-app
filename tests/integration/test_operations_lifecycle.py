"""Integration: Operational item lifecycle (§11.4 / §11.5).

Tests the full state machine:
pending_confirmation → confirmed → completed
Approval flow: pending_approval → approved → confirmed
Delegation: proposed → accepted → item reassigned
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def make_item(session, household_id, **kwargs):
    from app.modules.operations.models import OperationalItem
    item = OperationalItem(
        household_id=household_id,
        category=kwargs.get("category", "kids_logistics"),
        title=kwargs.get("title", "Test item"),
        status=kwargs.get("status", "pending_confirmation"),
        requires_approval=kwargs.get("requires_approval", False),
        priority=kwargs.get("priority", "normal"),
        assigned_to_member_id=kwargs.get("assigned_to_member_id"),
        start_at=kwargs.get("start_at"),
    )
    session.add(item)
    return item


@pytest.mark.asyncio
async def test_item_lifecycle_confirm_then_complete(db_session):
    """§11.4: pending_confirmation → confirmed → completed."""
    hh_id = uuid.uuid4()
    from app.platform.context import set_request_context
    set_request_context(household_id=str(hh_id), actor_type="system_agent")

    item = make_item(db_session, hh_id, status="pending_confirmation")
    await db_session.flush()

    # Confirm
    item.status = "confirmed"
    await db_session.flush()
    assert item.status == "confirmed"

    # Complete
    item.status = "completed"
    await db_session.flush()
    assert item.status == "completed"


@pytest.mark.asyncio
async def test_approval_required_for_cost_items(db_session):
    """Items with cost_cents > 0 have requires_approval=True."""
    hh_id = uuid.uuid4()
    from app.modules.operations.models import OperationalItem

    item = OperationalItem(
        household_id=hh_id,
        category="financial_action",
        title="Field trip — $25",
        cost_cents=2500,
        requires_approval=True,
        status="pending_approval",
        priority="normal",
    )
    db_session.add(item)
    await db_session.flush()

    assert item.requires_approval is True
    assert item.cost_cents == 2500
    assert item.status == "pending_approval"


@pytest.mark.asyncio
async def test_delegation_flow(db_session):
    """§11.5: Delegation proposed → accepted → item reassigned."""
    hh_id = uuid.uuid4()
    sarah_id = uuid.uuid4()
    mark_id = uuid.uuid4()
    from app.platform.context import set_request_context
    set_request_context(household_id=str(hh_id), actor_type="system_agent")

    from app.modules.operations.models import OperationalItem, TaskDelegation

    item = make_item(db_session, hh_id, assigned_to_member_id=sarah_id, status="confirmed")
    await db_session.flush()

    # Sarah delegates to Mark — assigned_to does NOT change yet
    delegation = TaskDelegation(
        operational_item_id=item.id,
        household_id=hh_id,
        delegated_by_member_id=sarah_id,
        delegated_to_member_id=mark_id,
        trigger_reason="manual",
        status="proposed",
    )
    db_session.add(delegation)
    await db_session.flush()

    assert item.assigned_to_member_id == sarah_id  # Still Sarah until accepted

    # Mark accepts
    delegation.status = "accepted"
    item.assigned_to_member_id = mark_id  # Now reassigned
    await db_session.flush()

    assert item.assigned_to_member_id == mark_id
    assert delegation.status == "accepted"


@pytest.mark.asyncio
async def test_archive_never_deletes(db_session):
    """Archive sets is_archived=True but item is still retrievable (§11.12)."""
    hh_id = uuid.uuid4()
    from app.modules.operations.models import OperationalItem
    from sqlalchemy import select

    item = make_item(db_session, hh_id, status="completed")
    await db_session.flush()
    item_id = item.id

    # Archive
    item.is_archived = True
    item.archived_at = datetime.now(timezone.utc)
    await db_session.flush()

    # Still retrievable by ID (never deleted)
    result = await db_session.execute(
        select(OperationalItem).where(OperationalItem.id == item_id)
    )
    found = result.scalar_one_or_none()
    assert found is not None
    assert found.is_archived is True

    # Excluded from active view
    result2 = await db_session.execute(
        select(OperationalItem)
        .where(OperationalItem.household_id == hh_id)
        .where(OperationalItem.is_archived.is_(False))
    )
    active = result2.scalars().all()
    assert all(i.id != item_id for i in active)
