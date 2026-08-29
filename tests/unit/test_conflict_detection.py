"""Unit tests for conflict detection service (§11.6).

Tested without a database — uses mock interfaces (Protocol implementations).
The test of a correct boundary: operations does not know Google from Microsoft.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.connectors import BusyWindow
from app.contracts.identity import MemberDTO
from app.modules.operations.models import OperationalItem
from app.modules.operations.services.conflict_detection import ConflictDetector


def make_member(member_id: uuid.UUID, household_id: uuid.UUID) -> MemberDTO:
    return MemberDTO(
        id=member_id,
        household_id=household_id,
        display_name="Test Member",
        role="co_parent",
        is_adult=True,
        timezone="America/New_York",
        status="active",
    )


def make_item(
    household_id: uuid.UUID,
    assigned_to: uuid.UUID,
    start_at: datetime,
) -> OperationalItem:
    item = MagicMock(spec=OperationalItem)
    item.id = uuid.uuid4()
    item.household_id = household_id
    item.assigned_to_member_id = assigned_to
    item.start_at = start_at
    item.end_at = None
    item.title = "Leo's soccer pickup"
    item.effective_end_at.return_value = start_at + timedelta(minutes=30)
    return item


class TestConflictDetector:
    @pytest.fixture
    def household_id(self):
        return uuid.uuid4()

    @pytest.fixture
    def sarah_id(self):
        return uuid.uuid4()

    @pytest.fixture
    def mark_id(self):
        return uuid.uuid4()

    @pytest.fixture
    def now(self):
        return datetime.now(timezone.utc) + timedelta(hours=2)

    def make_detector(self, household_id, sarah_id, mark_id, connectors_busy_fn, identity_members):
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        connectors = AsyncMock()
        connectors.get_busy_windows = AsyncMock(side_effect=connectors_busy_fn)

        identity = AsyncMock()
        identity.list_adult_members = AsyncMock(return_value=identity_members)

        events = AsyncMock()
        events.publish = AsyncMock()

        return ConflictDetector(session, connectors, identity, events)

    @pytest.mark.asyncio
    async def test_no_conflict_when_assignee_is_free(self, household_id, sarah_id, mark_id, now):
        """AC 11.6.1 precondition: assignee is free → no conflict detected."""
        item = make_item(household_id, sarah_id, now)

        async def no_busy(member_id, window_start, window_end):
            return []  # No busy windows

        detector = self.make_detector(
            household_id, sarah_id, mark_id,
            no_busy,
            [make_member(sarah_id, household_id), make_member(mark_id, household_id)],
        )

        outcome = await detector.check(item)
        assert outcome.status == "no_conflict"

    @pytest.mark.asyncio
    async def test_auto_delegation_when_one_free_candidate(self, household_id, sarah_id, mark_id, now):
        """AC 11.6.1: conflict found, exactly one free candidate → auto-propose delegation."""
        item = make_item(household_id, sarah_id, now)

        async def sarah_busy_mark_free(member_id, window_start, window_end):
            if member_id == sarah_id:
                return [BusyWindow(start=now - timedelta(minutes=30), end=now + timedelta(hours=1))]
            return []  # Mark is free

        detector = self.make_detector(
            household_id, sarah_id, mark_id,
            sarah_busy_mark_free,
            [make_member(sarah_id, household_id), make_member(mark_id, household_id)],
        )

        outcome = await detector.check(item)
        assert outcome.status == "auto_delegation_proposed"
        assert outcome.delegation_id is not None

    @pytest.mark.asyncio
    async def test_unresolved_when_everyone_busy(self, household_id, sarah_id, mark_id, now):
        """AC 11.6.2: conflict found, no free candidates → unresolved."""
        item = make_item(household_id, sarah_id, now)

        async def everyone_busy(member_id, window_start, window_end):
            return [BusyWindow(start=now - timedelta(minutes=30), end=now + timedelta(hours=1))]

        detector = self.make_detector(
            household_id, sarah_id, mark_id,
            everyone_busy,
            [make_member(sarah_id, household_id), make_member(mark_id, household_id)],
        )

        outcome = await detector.check(item)
        assert outcome.status == "unresolved"

    @pytest.mark.asyncio
    async def test_no_conflict_when_item_has_no_start_time(self, household_id, sarah_id, now):
        """Items without start_at should not run conflict detection."""
        item = make_item(household_id, sarah_id, now)
        item.start_at = None

        connectors = AsyncMock()
        connectors.get_busy_windows = AsyncMock()  # Should NOT be called

        detector = ConflictDetector(
            AsyncMock(), connectors, AsyncMock(), AsyncMock()
        )
        outcome = await detector.check(item)
        assert outcome.status == "no_conflict"
        connectors.get_busy_windows.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_conflict_when_item_has_no_assignee(self, household_id, sarah_id, now):
        """Items without an assignee should not run conflict detection."""
        item = make_item(household_id, sarah_id, now)
        item.assigned_to_member_id = None

        connectors = AsyncMock()
        connectors.get_busy_windows = AsyncMock()

        detector = ConflictDetector(
            AsyncMock(), connectors, AsyncMock(), AsyncMock()
        )
        outcome = await detector.check(item)
        assert outcome.status == "no_conflict"
        connectors.get_busy_windows.assert_not_called()
