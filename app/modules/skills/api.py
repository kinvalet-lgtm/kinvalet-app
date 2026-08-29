"""Skills module public API."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.skills import SkillExecutionDTO, SkillsAPI
from app.modules.skills.models import HouseholdMemberAddress, SkillExecution
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# Default lead times by category (§8.3)
LEAD_TIMES = {
    "kids_logistics": 60,
    "parent_care": 90,
    "default": 30,
}


class SkillsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def schedule_skills_for_item(
        self,
        item_id: uuid.UUID,
        household_id: uuid.UUID,
    ) -> list[SkillExecutionDTO]:
        """Schedule appropriate skills for a new/updated item."""
        # Fetch item details (via operations API in production — simplified here)
        from app.modules.operations.models import OperationalItem
        result = await self._session.execute(
            select(OperationalItem)
            .where(OperationalItem.id == item_id)
            .where(OperationalItem.household_id == household_id)
        )
        item = result.scalar_one_or_none()
        if item is None or item.start_at is None:
            return []

        executions = []

        # Try Logistics/ETA skill first if item has a location
        if item.location and item.assigned_to_member_id:
            eta_execution = await self._schedule_logistics_skill(item)
            if eta_execution:
                executions.append(eta_execution)
                return [self._to_dto(e) for e in executions]

        # Fall back to Reminder skill
        reminder_execution = await self._schedule_reminder_skill(item)
        if reminder_execution:
            executions.append(reminder_execution)

        return [self._to_dto(e) for e in executions]

    async def cancel_skills_for_item(self, item_id: uuid.UUID) -> None:
        """Cancel all pending skill executions for an item (on reassignment, cancellation, etc.)."""
        await self._session.execute(
            update(SkillExecution)
            .where(SkillExecution.operational_item_id == item_id)
            .where(SkillExecution.status == "scheduled")
            .values(status="cancelled")
        )

    async def _schedule_reminder_skill(self, item) -> Optional[SkillExecution]:
        lead_time = LEAD_TIMES.get(item.category, LEAD_TIMES["default"])
        scheduled_for = item.start_at - timedelta(minutes=lead_time)

        if scheduled_for <= datetime.now(timezone.utc):
            # Fire immediately
            scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=1)

        execution = SkillExecution(
            operational_item_id=item.id,
            household_id=item.household_id,
            skill_type_id="reminder_notification",
            status="scheduled",
            scheduled_for=scheduled_for,
        )
        self._session.add(execution)
        await self._session.flush()
        return execution

    async def _schedule_logistics_skill(self, item) -> Optional[SkillExecution]:
        """Schedule ETA calculation via Google Maps (§8.4)."""
        if not _settings.google_maps_api_key:
            logger.warning("google_maps_not_configured", item_id=str(item.id))
            return None

        # Check for member's home address
        address_result = await self._session.execute(
            select(HouseholdMemberAddress)
            .where(HouseholdMemberAddress.household_member_id == item.assigned_to_member_id)
            .where(HouseholdMemberAddress.label == "home")
        )
        address = address_result.scalar_one_or_none()

        if address is None:
            logger.info("no_home_address", member_id=str(item.assigned_to_member_id))
            return None  # Will fall back to reminder skill in caller

        # Schedule ETA check 45 minutes before start_at
        scheduled_for = item.start_at - timedelta(minutes=45)
        if scheduled_for <= datetime.now(timezone.utc):
            scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=1)

        execution = SkillExecution(
            operational_item_id=item.id,
            household_id=item.household_id,
            skill_type_id="logistics_eta",
            status="scheduled",
            scheduled_for=scheduled_for,
            output_json={"origin_address": address.address_text},
        )
        self._session.add(execution)
        await self._session.flush()
        return execution

    @staticmethod
    def _to_dto(execution: SkillExecution) -> SkillExecutionDTO:
        return SkillExecutionDTO(
            id=execution.id,
            operational_item_id=execution.operational_item_id,
            skill_type_id=execution.skill_type_id,
            status=execution.status,
            scheduled_for=execution.scheduled_for,
        )
