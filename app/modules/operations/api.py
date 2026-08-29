"""Operations module public API."""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.operations import OperationalItemDTO, OperationsAPI
from app.modules.operations.models import OperationalItem


class OperationsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_item(self, item_id: uuid.UUID, household_id: uuid.UUID) -> Optional[OperationalItemDTO]:
        result = await self._session.execute(
            select(OperationalItem)
            .where(OperationalItem.id == item_id)
            .where(OperationalItem.household_id == household_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            return None
        return self._to_dto(item)

    async def list_active_items(self, household_id: uuid.UUID) -> list[OperationalItemDTO]:
        result = await self._session.execute(
            select(OperationalItem)
            .where(OperationalItem.household_id == household_id)
            .where(OperationalItem.is_archived == False)
            .where(OperationalItem.status.notin_(["cancelled", "declined"]))
        )
        return [self._to_dto(i) for i in result.scalars().all()]

    @staticmethod
    def _to_dto(item: OperationalItem) -> OperationalItemDTO:
        return OperationalItemDTO(
            id=item.id,
            household_id=item.household_id,
            category=item.category,
            title=item.title,
            status=item.status,
            assigned_to_member_id=item.assigned_to_member_id,
            about_member_id=item.about_member_id,
            start_at=item.start_at,
            end_at=item.end_at,
            location=item.location,
            priority=item.priority,
            is_archived=item.is_archived,
        )
