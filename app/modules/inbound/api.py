"""Inbound module public API — implements contracts.inbound.InboundAPI."""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.inbound import InboundAPI, InboundMessageDTO
from app.modules.inbound.models import InboundMessage


class InboundService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_message(self, message_id: uuid.UUID) -> Optional[InboundMessageDTO]:
        result = await self._session.execute(
            select(InboundMessage).where(InboundMessage.id == message_id)
        )
        msg = result.scalar_one_or_none()
        if msg is None:
            return None
        return InboundMessageDTO(
            id=msg.id,
            household_id=msg.household_id,
            source=msg.source,
            media_type=msg.media_type,
            status=msg.status,
        )
