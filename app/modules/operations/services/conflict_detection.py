"""Conflict detection service (§11.6).

Detects calendar conflicts for items with a time and an assignee.
Cross-module reads go through declared interfaces — never direct DB queries
into connectors' schema.

The three outcomes:
1. Exactly one free candidate → auto-propose delegation
2. Multiple free candidates → surface picker (manual choice required)
3. Zero free candidates → flag as unresolved
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.connectors import ConnectorsAPI
from app.contracts.identity import IdentityAPI
from app.modules.operations.models import CalendarConflict, OperationalItem, TaskDelegation
from app.platform.events.bus import EventPublisher
from app.platform.observability import get_logger

logger = get_logger(__name__)

CONFLICT_BUFFER_MINUTES = 15  # Configurable per §11.6


@dataclass
class ConflictOutcome:
    status: str  # no_conflict | auto_delegation_proposed | manual_pick_required | unresolved
    conflict_id: Optional[UUID] = None
    delegation_id: Optional[UUID] = None
    free_candidates: list[UUID] = None  # type: ignore

    @classmethod
    def no_conflict(cls) -> "ConflictOutcome":
        return cls(status="no_conflict")

    @classmethod
    def auto_proposed(cls, conflict_id: UUID, delegation_id: UUID) -> "ConflictOutcome":
        return cls(status="auto_delegation_proposed", conflict_id=conflict_id, delegation_id=delegation_id)

    @classmethod
    def manual_pick_required(cls, conflict_id: UUID, candidates: list[UUID]) -> "ConflictOutcome":
        return cls(status="manual_pick_required", conflict_id=conflict_id, free_candidates=candidates)

    @classmethod
    def unresolved(cls, conflict_id: UUID) -> "ConflictOutcome":
        return cls(status="unresolved", conflict_id=conflict_id)


class ConflictDetector:
    def __init__(
        self,
        session: AsyncSession,
        connectors: ConnectorsAPI,
        identity: IdentityAPI,
        events: EventPublisher,
    ) -> None:
        self._session = session
        self._connectors = connectors  # Protocol — not an implementation
        self._identity = identity      # Protocol — not an implementation
        self._events = events

    async def check(self, item: OperationalItem) -> ConflictOutcome:
        """Check for calendar conflicts and propose resolution."""
        if not item.start_at or not item.assigned_to_member_id:
            return ConflictOutcome.no_conflict()

        # Cross-module READ through declared interface
        window_start = item.start_at - timedelta(minutes=CONFLICT_BUFFER_MINUTES)
        window_end = item.effective_end_at() or (item.start_at + timedelta(minutes=30))
        window_end = window_end + timedelta(minutes=CONFLICT_BUFFER_MINUTES)

        busy_windows = await self._connectors.get_busy_windows(
            member_id=item.assigned_to_member_id,
            window_start=window_start,
            window_end=window_end,
        )

        if not busy_windows:
            return ConflictOutcome.no_conflict()

        # Conflict found — check for free alternatives
        adults = await self._identity.list_adult_members(item.household_id)
        other_adults = [
            m for m in adults
            if m.id != item.assigned_to_member_id
        ]

        free_candidates = []
        for candidate in other_adults:
            candidate_busy = await self._connectors.get_busy_windows(
                member_id=candidate.id,
                window_start=window_start,
                window_end=window_end,
            )
            if not candidate_busy:
                free_candidates.append(candidate.id)

        # Record the conflict — assign UUID explicitly (SQLAlchemy default only runs at flush)
        import uuid as _uuid
        conflict_event_ref = {
            "title": busy_windows[0].event_title or "Busy",
            "start": busy_windows[0].start.isoformat(),
            "end": busy_windows[0].end.isoformat(),
        }
        conflict = CalendarConflict(
            id=_uuid.uuid4(),
            operational_item_id=item.id,
            household_id=item.household_id,
            busy_member_id=item.assigned_to_member_id,
            conflicting_event_ref=conflict_event_ref,
            resolution=self._resolution_label(len(free_candidates)),
        )
        self._session.add(conflict)
        await self._session.flush()

        if len(free_candidates) == 1:
            delegation = await self._create_delegation(
                item=item,
                delegated_to=free_candidates[0],
                trigger_reason="conflict_detected",
            )
            conflict.resulting_delegation_id = delegation.id

            from app.contracts.events import DelegationProposed, ConflictDetected
            await self._events.publish(ConflictDetected(
                calendar_conflict_id=conflict.id,
                operational_item_id=item.id,
                household_id=item.household_id,
                busy_member_id=item.assigned_to_member_id,
                resolution="auto_delegation_proposed",
            ))
            await self._events.publish(DelegationProposed(
                delegation_id=delegation.id,
                operational_item_id=item.id,
                household_id=item.household_id,
                delegated_by_member_id=item.assigned_to_member_id,
                delegated_to_member_id=free_candidates[0],
                item_title=item.title,
                trigger_reason="conflict_detected",
            ))
            return ConflictOutcome.auto_proposed(conflict.id, delegation.id)

        elif len(free_candidates) > 1:
            from app.contracts.events import ConflictDetected
            await self._events.publish(ConflictDetected(
                calendar_conflict_id=conflict.id,
                operational_item_id=item.id,
                household_id=item.household_id,
                busy_member_id=item.assigned_to_member_id,
                resolution="manual_pick_required",
            ))
            return ConflictOutcome.manual_pick_required(conflict.id, free_candidates)

        else:
            from app.contracts.events import ConflictDetected, ConflictUnresolved
            await self._events.publish(ConflictDetected(
                calendar_conflict_id=conflict.id,
                operational_item_id=item.id,
                household_id=item.household_id,
                busy_member_id=item.assigned_to_member_id,
                resolution="unresolved",
            ))
            await self._events.publish(ConflictUnresolved(
                operational_item_id=item.id,
                household_id=item.household_id,
            ))
            return ConflictOutcome.unresolved(conflict.id)

    async def _create_delegation(
        self,
        item: OperationalItem,
        delegated_to: UUID,
        trigger_reason: str,
    ) -> TaskDelegation:
        import uuid as _uuid
        delegation = TaskDelegation(
            id=_uuid.uuid4(),
            operational_item_id=item.id,
            household_id=item.household_id,
            delegated_by_member_id=item.assigned_to_member_id,
            delegated_to_member_id=delegated_to,
            trigger_reason=trigger_reason,
            status="proposed",
        )
        self._session.add(delegation)
        await self._session.flush()
        return delegation

    @staticmethod
    def _resolution_label(num_candidates: int) -> str:
        if num_candidates == 1:
            return "auto_delegation_proposed"
        if num_candidates > 1:
            return "manual_pick_required"
        return "unresolved"
