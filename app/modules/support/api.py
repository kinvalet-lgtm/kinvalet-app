"""Support module public API."""
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.support import SupportAPI, SupportTicketDTO
from app.modules.support.models import SupportTicket


class SupportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_ticket(
        self,
        household_id: uuid.UUID,
        member_id: uuid.UUID,
        category: str,
        description: str,
        related_item_id: Optional[uuid.UUID] = None,
    ) -> SupportTicketDTO:
        ticket = SupportTicket(
            household_id=household_id,
            raised_by_member_id=member_id,
            related_operational_item_id=related_item_id,
            channel="whatsapp",
            category=category,
            description=description,
            status="open",
        )
        self._session.add(ticket)
        await self._session.flush()
        return SupportTicketDTO(
            id=ticket.id,
            household_id=ticket.household_id,
            category=ticket.category,
            status=ticket.status,
        )
