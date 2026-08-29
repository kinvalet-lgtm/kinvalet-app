"""Support contracts."""
from typing import Optional, Protocol
from uuid import UUID
from pydantic import BaseModel


class SupportTicketDTO(BaseModel):
    id: UUID
    household_id: UUID
    category: str
    status: str


class SupportAPI(Protocol):
    async def open_ticket(
        self,
        household_id: UUID,
        member_id: UUID,
        category: str,
        description: str,
        related_item_id: Optional[UUID] = None,
    ) -> SupportTicketDTO: ...
