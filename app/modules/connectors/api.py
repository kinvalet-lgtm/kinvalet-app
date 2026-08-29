"""Connectors module public API — implements ConnectorsAPI contract."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.connectors import BusyWindow, ConnectorHealth, ConnectorInstanceDTO, ConnectorsAPI
from app.modules.connectors.models import HouseholdConnectorInstance
from app.platform.observability import get_logger

logger = get_logger(__name__)


class ConnectorsService:
    """Satisfies contracts.connectors.ConnectorsAPI structurally."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._adapters: dict = {}  # connector_type_id -> adapter

    async def get_busy_windows(
        self,
        member_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[BusyWindow]:
        """Fetch busy windows from all connected calendars for a member."""
        result = await self._session.execute(
            select(HouseholdConnectorInstance)
            .where(HouseholdConnectorInstance.connected_by_member_id == member_id)
            .where(HouseholdConnectorInstance.status == "connected")
            .where(HouseholdConnectorInstance.connector_type_id.in_(
                ["google_calendar", "microsoft_calendar"]
            ))
        )
        instances = result.scalars().all()

        if not instances:
            return []  # No calendars connected — detection does not run

        all_windows = []
        for instance in instances:
            adapter = self._adapters.get(instance.connector_type_id)
            if adapter is None:
                continue
            try:
                windows = await adapter.get_busy_windows(
                    instance=ConnectorInstanceDTO(
                        id=instance.id,
                        household_id=instance.household_id,
                        connector_type_id=instance.connector_type_id,
                        connected_by_member_id=instance.connected_by_member_id,
                        external_account_ref=instance.external_account_ref,
                        status=instance.status,
                    ),
                    start=window_start,
                    end=window_end,
                )
                all_windows.extend(windows)
            except Exception as e:
                logger.error(
                    "busy_windows_fetch_failed",
                    instance_id=str(instance.id),
                    error=str(e),
                )

        return all_windows

    async def create_calendar_event(
        self,
        member_id: uuid.UUID,
        title: str,
        start_at: datetime,
        end_at: Optional[datetime],
        location: Optional[str],
    ) -> Optional[str]:
        """Create a calendar event in the member's primary connected calendar."""
        result = await self._session.execute(
            select(HouseholdConnectorInstance)
            .where(HouseholdConnectorInstance.connected_by_member_id == member_id)
            .where(HouseholdConnectorInstance.status == "connected")
            .where(HouseholdConnectorInstance.connector_type_id.in_(
                ["google_calendar", "microsoft_calendar"]
            ))
            .limit(1)
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            return None

        adapter = self._adapters.get(instance.connector_type_id)
        if adapter is None:
            return None

        try:
            return await adapter.create_event(
                instance=ConnectorInstanceDTO(
                    id=instance.id,
                    household_id=instance.household_id,
                    connector_type_id=instance.connector_type_id,
                    connected_by_member_id=instance.connected_by_member_id,
                    external_account_ref=instance.external_account_ref,
                    status=instance.status,
                ),
                title=title,
                start_at=start_at,
                end_at=end_at,
                location=location,
            )
        except Exception as e:
            logger.error("calendar_event_creation_failed", error=str(e))
            return None

    async def update_calendar_event(self, member_id, provider_event_id, title, start_at, end_at) -> bool:
        return False  # TODO: implement

    async def delete_calendar_event(self, member_id, provider_event_id) -> bool:
        return False  # TODO: implement

    async def get_instance_health(self, instance_id: uuid.UUID) -> ConnectorHealth:
        return ConnectorHealth(
            status="healthy",
            last_checked_at=datetime.utcnow(),
        )
