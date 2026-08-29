"""Briefing module public API."""
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.briefing import BriefingAPI, BriefingDTO
from app.modules.briefing.models import Briefing


class BriefingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_briefing(self, household_id: uuid.UUID) -> Optional[BriefingDTO]:
        result = await self._session.execute(
            select(Briefing)
            .where(Briefing.household_id == household_id)
            .order_by(Briefing.briefing_date.desc())
            .limit(1)
        )
        briefing = result.scalar_one_or_none()
        if briefing is None:
            return None
        return BriefingDTO(
            id=briefing.id,
            household_id=briefing.household_id,
            briefing_date=briefing.briefing_date,
            content_json=briefing.content_json,
        )
