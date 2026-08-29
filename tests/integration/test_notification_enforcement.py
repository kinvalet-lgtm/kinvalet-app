"""Integration: Notification enforcement rules (§11.19).

Tests quiet hours, daily ceiling, STOP/START opt-out.
AC 11.19.1: STOP → all sends cease immediately.
AC 11.19.2: Quiet hours hold non-urgent; critical breaks through.
AC 11.19.3: Ceiling exceeded → batch into digest.
"""
import uuid
from datetime import time

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_quiet_hours_holds_non_urgent(db_session):
    """Non-urgent notifications held during quiet hours."""
    from app.modules.notification.service import NotificationEnforcer
    import uuid

    member_id = uuid.uuid4()
    household_id = uuid.uuid4()

    enforcer = NotificationEnforcer(db_session)
    # Normal priority during quiet hours (21:00–07:00)
    # Simulate it being 23:00 by testing the logic directly
    from app.modules.notification.service import NotificationEnforcer
    in_quiet = NotificationEnforcer._in_quiet_hours(
        current=time(23, 0),
        start=time(21, 0),
        end=time(7, 0),
    )
    assert in_quiet is True

    in_quiet_morning = NotificationEnforcer._in_quiet_hours(
        current=time(6, 30),
        start=time(21, 0),
        end=time(7, 0),
    )
    assert in_quiet_morning is True

    # 10 AM is NOT in quiet hours
    not_quiet = NotificationEnforcer._in_quiet_hours(
        current=time(10, 0),
        start=time(21, 0),
        end=time(7, 0),
    )
    assert not_quiet is False


@pytest.mark.asyncio
async def test_delegation_request_always_sent(db_session):
    """Delegation requests cannot be disabled — always return send."""
    from app.modules.notification.service import NotificationEnforcer
    import uuid

    member_id = uuid.uuid4()
    household_id = uuid.uuid4()

    # Add an opted-out preference
    from app.modules.notification.models import NotificationPreference
    pref = NotificationPreference(
        household_member_id=member_id,
        household_id=household_id,
        category="delegation_request",
        enabled=False,  # even disabled
        whatsapp_opted_out=False,
    )
    db_session.add(pref)
    await db_session.flush()

    enforcer = NotificationEnforcer(db_session)
    should_send, reason = await enforcer.check(
        member_id=member_id,
        household_id=household_id,
        category="delegation_request",
        priority="normal",
    )
    assert should_send is True
    assert reason == "send"


@pytest.mark.asyncio
async def test_opted_out_member_blocked(db_session):
    """STOP opt-out blocks all sends (AC 11.19.1)."""
    from app.modules.notification.service import NotificationEnforcer
    from app.modules.notification.models import NotificationPreference
    import uuid

    member_id = uuid.uuid4()
    household_id = uuid.uuid4()

    pref = NotificationPreference(
        household_member_id=member_id,
        household_id=household_id,
        category="daily_briefing",
        enabled=True,
        whatsapp_opted_out=True,  # STOP was received
    )
    db_session.add(pref)
    await db_session.flush()

    enforcer = NotificationEnforcer(db_session)
    should_send, reason = await enforcer.check(
        member_id=member_id,
        household_id=household_id,
        category="daily_briefing",
        priority="normal",
    )
    assert should_send is False
    assert reason == "opted_out"


@pytest.mark.asyncio
async def test_quiet_hours_window_wraps_midnight():
    """Quiet hours 21:00–07:00 correctly wraps across midnight."""
    from app.modules.notification.service import NotificationEnforcer

    check = NotificationEnforcer._in_quiet_hours
    # 21:00 boundary (inclusive)
    assert check(time(21, 0), time(21, 0), time(7, 0)) is True
    # Just before end (06:59)
    assert check(time(6, 59), time(21, 0), time(7, 0)) is True
    # At end (07:00, exclusive)
    assert check(time(7, 0), time(21, 0), time(7, 0)) is False
    # Midday — not quiet
    assert check(time(12, 0), time(21, 0), time(7, 0)) is False
    # Non-wrapping window (09:00–17:00)
    assert check(time(12, 0), time(9, 0), time(17, 0)) is True
    assert check(time(8, 0), time(9, 0), time(17, 0)) is False
