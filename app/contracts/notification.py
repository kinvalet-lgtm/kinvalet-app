"""Notification contracts."""
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class NotificationRequest(BaseModel):
    member_id: UUID
    household_id: UUID
    template_key: str
    template_vars: dict
    priority: str = "normal"  # critical | normal | low


class NotificationAPI(Protocol):
    async def send(self, request: NotificationRequest) -> str: ...  # Returns notification_log_id
    async def send_whatsapp(
        self,
        phone_e164: str,
        message: str,
        priority: str = "normal",
    ) -> bool: ...
