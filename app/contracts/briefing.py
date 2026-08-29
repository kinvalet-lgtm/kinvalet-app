"""Briefing contracts."""
from typing import Optional, Protocol
from uuid import UUID
from datetime import date
from pydantic import BaseModel


class BriefingDTO(BaseModel):
    id: UUID
    household_id: UUID
    briefing_date: date
    content_json: dict


class BriefingAPI(Protocol):
    async def get_latest_briefing(self, household_id: UUID) -> Optional[BriefingDTO]: ...
