"""Calendar events API — pull real events from connected Google Calendars.

GET /api/v1/calendar/events?date=2026-08-28  — events for a specific date
GET /api/v1/calendar/events/today             — today's events
GET /api/v1/calendar/events/tomorrow          — tomorrow's events
GET /api/v1/calendar/events/week              — next 7 days

Each member sees events from THEIR configured calendars.
Events include calendar name, color, and source for UI display.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.models import HouseholdConnectorInstance
from app.modules.connectors.calendar_config import MemberCalendarConfig
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])
logger = get_logger(__name__)


async def _get_events_for_range(
    session: AsyncSession,
    household_id,
    member_id,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Fetch events from all connected+configured calendars for a date range."""
    # Get connected calendar instances for this household
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
        # Get configured calendars for this member
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
                secret_ref=inst.secret_ref or "",
            ))
            if creds is None:
                continue
        except Exception as e:
            logger.warning("calendar_events_creds_failed", error=str(e))
            continue

        # Fetch events from each configured calendar
        import httpx
        for cal_id in calendar_ids:
            try:
                resp = httpx.get(
                    f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events",
                    headers={"Authorization": f"Bearer {creds.token}"},
                    params={
                        "timeMin": start.isoformat(),
                        "timeMax": end.isoformat(),
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": "50",
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                cal_name = data.get("summary", cal_id)

                for event in data.get("items", []):
                    event_start = event.get("start", {})
                    event_end = event.get("end", {})

                    # Skip all-day events without specific time
                    start_dt = event_start.get("dateTime")
                    if not start_dt:
                        # All-day event
                        all_events.append({
                            "id": event.get("id"),
                            "title": event.get("summary", "(no title)"),
                            "all_day": True,
                            "date": event_start.get("date"),
                            "start_at": None,
                            "end_at": None,
                            "location": event.get("location"),
                            "calendar_name": cal_name,
                            "calendar_id": cal_id,
                            "status": event.get("status", "confirmed"),
                            "source": "google_calendar",
                        })
                        continue

                    all_events.append({
                        "id": event.get("id"),
                        "title": event.get("summary", "(no title)"),
                        "all_day": False,
                        "start_at": start_dt,
                        "end_at": event_end.get("dateTime"),
                        "location": event.get("location"),
                        "description": (event.get("description") or "")[:200],
                        "calendar_name": cal_name,
                        "calendar_id": cal_id,
                        "status": event.get("status", "confirmed"),
                        "source": "google_calendar",
                    })

            except Exception as e:
                logger.warning("calendar_events_fetch_failed", calendar=cal_id, error=str(e))

    # Sort by start time
    all_events.sort(key=lambda e: e.get("start_at") or e.get("date") or "9999")
    return all_events


@router.get("/events/today")
async def get_today_events(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get all calendar events for today."""
    import pytz
    # Use household timezone
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)

    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    events = await _get_events_for_range(
        session, current_member.household_id, current_member.id,
        start.astimezone(timezone.utc), end.astimezone(timezone.utc),
    )
    return {"date": now.strftime("%Y-%m-%d"), "timezone": tz_name, "events": events}


@router.get("/events/tomorrow")
async def get_tomorrow_events(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get all calendar events for tomorrow."""
    import pytz
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)

    tomorrow = datetime.now(tz) + timedelta(days=1)
    start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    events = await _get_events_for_range(
        session, current_member.household_id, current_member.id,
        start.astimezone(timezone.utc), end.astimezone(timezone.utc),
    )
    return {"date": tomorrow.strftime("%Y-%m-%d"), "timezone": tz_name, "events": events}


@router.get("/events/week")
async def get_week_events(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get all calendar events for the next 7 days."""
    import pytz
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)

    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)

    events = await _get_events_for_range(
        session, current_member.household_id, current_member.id,
        start.astimezone(timezone.utc), end.astimezone(timezone.utc),
    )
    return {"start_date": start.strftime("%Y-%m-%d"), "end_date": (end - timedelta(days=1)).strftime("%Y-%m-%d"), "timezone": tz_name, "events": events}


@router.get("/events")
async def get_events_for_date(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get calendar events for a specific date."""
    import pytz
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)

    target = datetime.strptime(date, "%Y-%m-%d")
    start = tz.localize(target)
    end = start + timedelta(days=1)

    events = await _get_events_for_range(
        session, current_member.household_id, current_member.id,
        start.astimezone(timezone.utc), end.astimezone(timezone.utc),
    )
    return {"date": date, "timezone": tz_name, "events": events}
# deploy Fri Aug 28 18:28:29 PDT 2026
