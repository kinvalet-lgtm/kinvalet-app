"""Operations contracts."""
from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class OperationalItemDTO(BaseModel):
    id: UUID
    household_id: UUID
    category: str
    title: str
    status: str
    assigned_to_member_id: Optional[UUID] = None
    about_member_id: Optional[UUID] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    priority: str = "normal"
    is_archived: bool = False


class OperationsAPI(Protocol):
    async def get_item(self, item_id: UUID, household_id: UUID) -> Optional[OperationalItemDTO]: ...
    async def list_active_items(self, household_id: UUID) -> list[OperationalItemDTO]: ...
