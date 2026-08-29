"""Skills contracts."""
from typing import Optional, Protocol
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


class SkillExecutionDTO(BaseModel):
    id: UUID
    operational_item_id: UUID
    skill_type_id: str
    status: str
    scheduled_for: Optional[datetime] = None


class SkillsAPI(Protocol):
    async def schedule_skills_for_item(
        self,
        item_id: UUID,
        household_id: UUID,
    ) -> list[SkillExecutionDTO]: ...

    async def cancel_skills_for_item(self, item_id: UUID) -> None: ...
