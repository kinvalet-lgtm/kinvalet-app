"""Inbound contracts."""
from typing import Optional, Protocol
from uuid import UUID
from pydantic import BaseModel


class InboundMessageDTO(BaseModel):
    id: UUID
    household_id: Optional[UUID]
    source: str
    media_type: str
    status: str


class InboundAPI(Protocol):
    async def get_message(self, message_id: UUID) -> Optional[InboundMessageDTO]: ...
