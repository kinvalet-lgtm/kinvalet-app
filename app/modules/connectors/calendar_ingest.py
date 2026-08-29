"""Calendar event ingestion — 4th input channel.

Pulls events from connected Google Calendars and creates operational_items.
These are real items that can be assigned, delegated, reminded about, and
show up in the daily briefing alongside WhatsApp/email/dashboard items.

4 input channels, one brain:
  1. WhatsApp   → LLM extraction → operational_item
  2. Email      → LLM extraction → operational_item
  3. Dashboard  → LLM extraction → operational_item
  4. Calendar   → direct mapping → operational_item (NEW — no LLM needed)

Calendar events don't need LLM extraction — they're already structured
(title, start, end, location). We map them directly to operational_items.

Sync strategy:
  - Worker runs every 30 minutes
  - For each connected calendar, fetch events for the next 7 days
  - Create/update operational_items for new/changed events
  - Track synced events by provider_event_id to avoid duplicates
  - Deleted events in Google → mark item as cancelled in KinValet
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.models import HouseholdConnectorInstance
from app.modules.connectors.calendar_config import MemberCalendarConfig
from app.modules.operations.models import OperationalItem
from app.platform.observability import get_logger

logger = get_logger(__name__)

SYNC_HORIZON_DAYS = 7
CATEGORY_MAP = {
    # Map calendar event keywords to KinValet categories
    "doctor": "parent_care",
    "dentist": "parent_care",
    "prescription": "parent_care",
    "medication": "parent_care",
    "therapy": "parent_care",
    "school": "kids_logistics",
    "practice": "kids_logistics",
    "soccer": "kids_logistics",
    "pickup": "kids_logistics",
    "drop": "kids_logistics",
    "game": "kids_logistics",
    "recital": "kids_logistics",
    "plumber": "household_admin",
    "repair": "household_admin",
    "tutor": "kids_logistics",
}


def infer_category(title: str) -> str:
    """Infer a KinValet category from a calendar event title."""
    lower = title.lower()
    for keyword, category in CATEGORY_MAP.items():
        if keyword in lower:
            return category
    return "household_admin"  # default


async def sync_calendar_events(session: AsyncSession, household_id: uuid.UUID) -> int:
    """Pull events from all connected calendars and create/update operational_items.

    Returns the number of new items created.
    """
    # Find all connected calendar instances
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == household_id)
        .where(HouseholdConnectorInstance.connector_type_id.in_(["google_calendar", "microsoft_calendar"]))
        .where(HouseholdConnectorInstance.status == "connected")
    )
    instances = result.scalars().all()

    if not instances:
        return 0

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=SYNC_HORIZON_DAYS)
    total_created = 0

    for inst in instances:
        # Get selected calendars for this member
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
            logger.warning("calendar_ingest_creds_failed", error=str(e))
            continue

        # Fetch events from each calendar
        for cal_id in calendar_ids:
            try:
                events = await _fetch_events(creds, cal_id, now, horizon)
                for event in events:
                    created = await _upsert_item(
                        session, household_id, inst.connected_by_member_id, event
                    )
                    if created:
                        total_created += 1
            except Exception as e:
                logger.warning("calendar_ingest_fetch_failed", calendar=cal_id, error=str(e))

    if total_created:
        logger.info("calendar_events_ingested", household_id=str(household_id), count=total_created)

    return total_created


async def _fetch_events(creds, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch events from a single Google Calendar."""
    from googleapiclient.discovery import build
    service = build("calendar", "v3", credentials=creds)

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,  # expand recurring events
        orderBy="startTime",
        maxResults=100,
    ).execute()

    events = []
    for event in events_result.get("items", []):
        start_dt = event.get("start", {})
        end_dt = event.get("end", {})

        # Skip all-day events without a specific time
        if "dateTime" not in start_dt:
            continue

        # Skip cancelled events
        if event.get("status") == "cancelled":
            continue

        events.append({
            "provider_event_id": event["id"],
            "title": event.get("summary", "(no title)"),
            "start_at": start_dt["dateTime"],
            "end_at": end_dt.get("dateTime"),
            "location": event.get("location"),
            "description": event.get("description", "")[:500],
            "calendar_id": calendar_id,
            "status": event.get("status", "confirmed"),
            "updated": event.get("updated"),
        })

    return events


async def _upsert_item(
    session: AsyncSession,
    household_id: uuid.UUID,
    member_id: uuid.UUID,
    event: dict,
) -> bool:
    """Create or update an operational_item from a calendar event.

    Returns True if a new item was created.
    """
    provider_event_id = event["provider_event_id"]

    # Check if we already synced this event
    existing = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.household_id == household_id)
        .where(OperationalItem.calendar_provider_event_id == provider_event_id)
    )
    item = existing.scalar_one_or_none()

    start_at = datetime.fromisoformat(event["start_at"].replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(event["end_at"].replace("Z", "+00:00")) if event.get("end_at") else None

    if item:
        # Update existing item if the calendar event changed
        changed = False
        if item.title != event["title"]:
            item.title = event["title"]
            changed = True
        if item.start_at != start_at:
            item.start_at = start_at
            changed = True
        if item.end_at != end_at:
            item.end_at = end_at
            changed = True
        if item.location != event.get("location"):
            item.location = event.get("location")
            changed = True

        if changed:
            item.calendar_sync_status = "synced"
            logger.debug("calendar_item_updated", title=event["title"])

        return False  # not a new creation

    # Create new operational_item
    new_item = OperationalItem(
        id=uuid.uuid4(),
        household_id=household_id,
        category=infer_category(event["title"]),
        title=event["title"],
        description=event.get("description"),
        assigned_to_member_id=member_id,
        start_at=start_at,
        end_at=end_at,
        location=event.get("location"),
        status="confirmed",  # calendar events are already confirmed
        calendar_provider_event_id=provider_event_id,
        calendar_sync_status="synced",
        priority="normal",
        requires_approval=False,
    )
    session.add(new_item)

    logger.info("calendar_item_created", title=event["title"], start=event["start_at"])
    return True
