"""Connectors contracts."""
from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class BusyWindow(BaseModel):
    start: datetime
    end: datetime
    event_title: Optional[str] = None  # for audit / conflict record only


class ConnectorInstanceDTO(BaseModel):
    id: UUID
    household_id: UUID
    connector_type_id: str
    connected_by_member_id: UUID
    external_account_ref: str
    status: str
    secret_ref: str = ""  # pointer to stored OAuth credentials


class ConnectorHealth(BaseModel):
    status: str  # healthy | degraded | error
    last_checked_at: datetime
    error_message: Optional[str] = None


class ConnectorsAPI(Protocol):
    async def get_busy_windows(
        self,
        member_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[BusyWindow]: ...

    async def create_calendar_event(
        self,
        member_id: UUID,
        title: str,
        start_at: datetime,
        end_at: Optional[datetime],
        location: Optional[str],
    ) -> Optional[str]: ...  # Returns provider_event_id or None

    async def update_calendar_event(
        self,
        member_id: UUID,
        provider_event_id: str,
        title: str,
        start_at: datetime,
        end_at: Optional[datetime],
    ) -> bool: ...

    async def delete_calendar_event(
        self,
        member_id: UUID,
        provider_event_id: str,
    ) -> bool: ...

    async def get_instance_health(
        self,
        instance_id: UUID,
    ) -> ConnectorHealth: ...
