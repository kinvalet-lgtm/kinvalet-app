"""Calendar event sync — pulls events from connected Google Calendars into KinValet.

This is what makes the daily briefing aware of calendar events, not just
WhatsApp/email-originated items. Without this, a dentist appointment in
Google Calendar is invisible to the briefing.

Two modes:
1. Briefing-time pull: BriefingGenerator calls this to get today's events
2. Background sync: Worker periodically pulls events to detect new conflicts

Events from Google Calendar are NOT stored as operational_items (they already
live in Google). Instead, they're pulled at briefing assembly time and merged
into the briefing content_json alongside operational_items.

For conflict detection, we use the freebusy API (already built in ConnectorsService).
For the briefing, we need actual event details (title, time, location).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.models import HouseholdConnectorInstance
from app.modules.connectors.calendar_config import MemberCalendarConfig
from app.platform.observability import get_logger

logger = get_logger(__name__)


async def fetch_calendar_events_for_date(
    session: AsyncSession,
    household_id: UUID,
    target_date: datetime,
) -> list[dict]:
    """Fetch all calendar events for a household on a given date.

    Queries each connected member's selected calendars via Google Calendar API.
    Returns a merged list of events with member attribution.

    Called by BriefingGenerator to include calendar events in the daily briefing.
    """
    from app.platform.config import get_settings
    settings = get_settings()

    # Find all connected calendar instances for this household
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == household_id)
        .where(HouseholdConnectorInstance.connector_type_id.in_(["google_calendar", "microsoft_calendar"]))
        .where(HouseholdConnectorInstance.status == "connected")
    )
    instances = result.scalars().all()

    if not instances:
        return []

    all_events = []

    for inst in instances:
        # Get this member's selected calendars
        config_result = await session.execute(
            select(MemberCalendarConfig)
            .where(MemberCalendarConfig.connector_instance_id == inst.id)
        )
        config = config_result.scalar_one_or_none()
        calendar_ids = config.sync_calendar_list if config else ["primary"]

        # Load credentials
        try:
            from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
            from app.contracts.connectors import ConnectorInstanceDTO
            adapter = GoogleCalendarAdapter()
            creds = await adapter._load_credentials(ConnectorInstanceDTO(
                id=inst.id, household_id=inst.household_id,
                connector_type_id=inst.connector_type_id,
                connected_by_member_id=inst.connected_by_member_id,
                external_account_ref=inst.external_account_ref,
                status=inst.status,
            ))
            if creds is None:
                continue
        except Exception as e:
            logger.warning("calendar_sync_creds_failed", error=str(e))
            continue

        # Query each selected calendar for events on the target date
        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        for cal_id in calendar_ids:
            try:
                from googleapiclient.discovery import build
                service = build("calendar", "v3", credentials=creds)
                events_result = service.events().list(
                    calendarId=cal_id,
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                ).execute()

                for event in events_result.get("items", []):
                    start = event.get("start", {})
                    end = event.get("end", {})

                    # Skip all-day events or events without a specific time
                    if "dateTime" not in start:
                        continue

                    all_events.append({
                        "source": "google_calendar",
                        "calendar_id": cal_id,
                        "calendar_name": events_result.get("summary", cal_id),
                        "member_id": str(inst.connected_by_member_id),
                        "member_account": inst.external_account_ref,
                        "event_id": event.get("id"),
                        "title": event.get("summary", "(no title)"),
                        "start_at": start.get("dateTime"),
                        "end_at": end.get("dateTime"),
                        "location": event.get("location"),
                        "status": event.get("status", "confirmed"),
                        "is_external": True,  # from Google, not created by KinValet
                    })

            except Exception as e:
                logger.warning("calendar_events_fetch_failed", calendar=cal_id, error=str(e))

    # Sort by start time
    all_events.sort(key=lambda e: e.get("start_at", ""))

    logger.info(
        "calendar_events_fetched",
        household_id=str(household_id),
        event_count=len(all_events),
        calendars_queried=len(instances),
    )

    return all_events
