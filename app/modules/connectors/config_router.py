"""Connector configuration API — calendar picker + Gmail label picker.

After OAuth the user selects:
- Which calendars to sync (read) and which ONE to write new events to
- Which Gmail labels/folders to watch

All selections are per-member — each family member configures their own.

Calendar write access policy (answering the user's question):
  We write ONLY to the calendar the member explicitly selects as writable.
  Read-only calendars (shared school calendars, "Holidays in US") are used
  for conflict detection ONLY. We display a clear distinction in the UI:
    [READ] School calendar — conflict detection only
    [READ+WRITE] Sarah's Calendar — new events created here  ← user picks this
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.calendar_config import MemberCalendarConfig, MemberGmailConfig
from app.modules.connectors.models import HouseholdConnectorInstance
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/connectors/configure", tags=["connectors"])
logger = get_logger(__name__)


# ── Calendar: list available calendars after OAuth ─────────────────────────────

@router.get("/google/calendars")
async def list_google_calendars(
    member_id: Optional[uuid.UUID] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List all calendars in the member's Google account.

    Called immediately after OAuth to populate the calendar picker.
    Returns calendars with their access role so UI can distinguish
    read-only vs read+write calendars.
    """
    target_member_id = member_id or current_member.id

    # Get the connected instance
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "google_calendar")
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
        .where(HouseholdConnectorInstance.status == "connected")
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Google Calendar not connected for this member")

    calendars = await _fetch_google_calendars(inst)

    # Get existing config (if member already selected calendars)
    config_result = await session.execute(
        select(MemberCalendarConfig)
        .where(MemberCalendarConfig.household_member_id == target_member_id)
        .where(MemberCalendarConfig.connector_instance_id == inst.id)
    )
    existing = config_result.scalar_one_or_none()

    return {
        "member_id": str(target_member_id),
        "account": inst.external_account_ref,
        "calendars": calendars,
        "current_config": {
            "sync_calendar_ids": existing.sync_calendar_list if existing else [],
            "write_calendar_id": existing.write_calendar_id if existing else None,
            "write_calendar_name": existing.write_calendar_name if existing else None,
        } if existing else None,
    }


@router.get("/microsoft/calendars")
async def list_microsoft_calendars(
    member_id: Optional[uuid.UUID] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List all Outlook calendars for the member."""
    target_member_id = member_id or current_member.id

    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "microsoft_calendar")
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
        .where(HouseholdConnectorInstance.status == "connected")
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Microsoft Calendar not connected for this member")

    calendars = await _fetch_microsoft_calendars(inst)

    config_result = await session.execute(
        select(MemberCalendarConfig)
        .where(MemberCalendarConfig.household_member_id == target_member_id)
        .where(MemberCalendarConfig.connector_instance_id == inst.id)
    )
    existing = config_result.scalar_one_or_none()

    return {
        "member_id": str(target_member_id),
        "account": inst.external_account_ref,
        "calendars": calendars,
        "current_config": {
            "sync_calendar_ids": existing.sync_calendar_list if existing else [],
            "write_calendar_id": existing.write_calendar_id if existing else None,
        } if existing else None,
    }


class CalendarSelectionRequest(BaseModel):
    member_id: Optional[uuid.UUID] = None
    connector_type_id: str  # google_calendar | microsoft_calendar
    sync_calendar_ids: list[str]       # all calendars to read for conflict detection
    sync_calendar_names: list[str]     # display names (parallel list)
    write_calendar_id: str             # the ONE calendar we may write new events to
    write_calendar_name: str           # display name of write calendar


@router.post("/calendars/select")
async def save_calendar_selection(
    body: CalendarSelectionRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Save the member's calendar selection.

    write_calendar_id must be in sync_calendar_ids.
    We will ONLY create new events in write_calendar_id.
    All other calendars in sync_calendar_ids are read-only (conflict detection).
    """
    target_member_id = body.member_id or current_member.id

    if body.write_calendar_id not in body.sync_calendar_ids:
        raise HTTPException(
            status_code=422,
            detail="write_calendar_id must be one of the selected sync calendars"
        )
    if not body.sync_calendar_ids:
        raise HTTPException(status_code=422, detail="Select at least one calendar to sync")

    # Get instance
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == body.connector_type_id)
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Connector not found for this member")

    # Upsert config
    existing = await session.execute(
        select(MemberCalendarConfig)
        .where(MemberCalendarConfig.household_member_id == target_member_id)
        .where(MemberCalendarConfig.connector_instance_id == inst.id)
    )
    config = existing.scalar_one_or_none()

    if config:
        config.sync_calendar_ids = ",".join(body.sync_calendar_ids)
        config.sync_calendar_names = ",".join(body.sync_calendar_names)
        config.write_calendar_id = body.write_calendar_id
        config.write_calendar_name = body.write_calendar_name
    else:
        config = MemberCalendarConfig(
            household_id=current_member.household_id,
            household_member_id=target_member_id,
            connector_instance_id=inst.id,
            sync_calendar_ids=",".join(body.sync_calendar_ids),
            sync_calendar_names=",".join(body.sync_calendar_names),
            write_calendar_id=body.write_calendar_id,
            write_calendar_name=body.write_calendar_name,
        )
        session.add(config)

    await session.commit()

    logger.info(
        "calendar_config_saved",
        member_id=str(target_member_id),
        sync_count=len(body.sync_calendar_ids),
        write_calendar=body.write_calendar_name,
    )
    return {
        "saved": True,
        "sync_calendars": list(zip(body.sync_calendar_ids, body.sync_calendar_names)),
        "write_calendar": {"id": body.write_calendar_id, "name": body.write_calendar_name},
        "note": f"New events will be created in '{body.write_calendar_name}'. "
                f"Other selected calendars are used for conflict detection only.",
    }


# ── Gmail: list labels and save selection ─────────────────────────────────────

@router.get("/gmail/labels")
async def list_gmail_labels(
    member_id: Optional[uuid.UUID] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List all Gmail labels/folders for the member's account."""
    target_member_id = member_id or current_member.id

    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "gmail")
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
        .where(HouseholdConnectorInstance.status == "connected")
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Gmail not connected for this member")

    from app.modules.connectors.adapters.gmail import GmailAdapter
    from app.contracts.connectors import ConnectorInstanceDTO
    adapter = GmailAdapter()
    labels = await adapter.list_labels(ConnectorInstanceDTO(
        id=inst.id,
        household_id=inst.household_id,
        connector_type_id=inst.connector_type_id,
        connected_by_member_id=inst.connected_by_member_id,
        external_account_ref=inst.external_account_ref,
        status=inst.status,
        secret_ref=inst.secret_ref,
    ))

    # Get existing config
    config_result = await session.execute(
        select(MemberGmailConfig)
        .where(MemberGmailConfig.household_member_id == target_member_id)
    )
    existing = config_result.scalar_one_or_none()

    return {
        "member_id": str(target_member_id),
        "account": inst.external_account_ref,
        "labels": labels,
        "current_selection": {
            "label_ids": existing.label_list,
            "label_names": existing.label_name_list,
        } if existing else None,
        "suggested": [
            {"id": "INBOX", "name": "Inbox", "reason": "Catch all incoming emails"},
        ],
    }


class GmailLabelSelectionRequest(BaseModel):
    member_id: Optional[uuid.UUID] = None
    label_ids: list[str]  # empty list = stop reading entirely
    label_names: list[str]


@router.post("/gmail/labels/select")
async def save_gmail_label_selection(
    body: GmailLabelSelectionRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Save or clear the member's Gmail label selection.

    Empty label_ids = stop reading from Gmail entirely.
    When a label is removed, KinValet immediately stops reading from it
    on the next sync cycle (no additional Google consent needed).
    """
    target_member_id = body.member_id or current_member.id

    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "gmail")
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Gmail not connected")

    config_result = await session.execute(
        select(MemberGmailConfig)
        .where(MemberGmailConfig.household_member_id == target_member_id)
    )
    config = config_result.scalar_one_or_none()

    # Track what changed for audit
    old_labels = config.label_list if config else []
    new_labels = body.label_ids
    added = set(new_labels) - set(old_labels)
    removed = set(old_labels) - set(new_labels)

    if config:
        config.selected_label_ids = ",".join(body.label_ids) if body.label_ids else ""
        config.selected_label_names = ",".join(body.label_names) if body.label_names else ""
    else:
        config = MemberGmailConfig(
            household_id=current_member.household_id,
            household_member_id=target_member_id,
            connector_instance_id=inst.id,
            selected_label_ids=",".join(body.label_ids) if body.label_ids else "",
            selected_label_names=",".join(body.label_names) if body.label_names else "",
        )
        session.add(config)

    await session.commit()

    # Log the change — removed labels stop being read immediately on next sync
    if removed:
        logger.info(
            "gmail_labels_removed",
            member_id=str(target_member_id),
            removed_labels=list(removed),
            note="KinValet will stop reading from these labels immediately",
        )
    if added:
        logger.info(
            "gmail_labels_added",
            member_id=str(target_member_id),
            added_labels=list(added),
        )

    is_stopped = len(body.label_ids) == 0
    return {
        "saved": True,
        "watching": list(zip(body.label_ids, body.label_names)) if body.label_ids else [],
        "stopped": is_stopped,
        "labels_added": list(added),
        "labels_removed": list(removed),
        "note": "Stopped reading from Gmail." if is_stopped
                else "We'll extract actionable items from emails in these folders only. Read-only — we never send, delete, or modify your email.",
    }


@router.delete("/gmail/stop")
async def stop_gmail_reading(
    member_id: Optional[uuid.UUID] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Stop reading from Gmail entirely — clears all selected labels.

    Takes effect immediately on next sync cycle. No additional Google
    consent needed. The OAuth connection remains but nothing is read.
    """
    target_member_id = member_id or current_member.id

    config_result = await session.execute(
        select(MemberGmailConfig)
        .where(MemberGmailConfig.household_member_id == target_member_id)
    )
    config = config_result.scalar_one_or_none()

    if config:
        old_labels = config.label_list
        config.selected_label_ids = ""
        config.selected_label_names = ""
        await session.commit()
        logger.info(
            "gmail_reading_stopped",
            member_id=str(target_member_id),
            previously_watching=old_labels,
        )

    return {"stopped": True, "note": "KinValet has stopped reading from your Gmail. You can re-select folders anytime."}


# ── Per-member connector summary ──────────────────────────────────────────────

@router.get("/member/{member_id}/summary")
async def member_connector_summary(
    member_id: uuid.UUID,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Return all connector configs for a specific family member.

    Used by Settings → Members to show each member's integrations.
    """
    if current_member.household_id != current_member.household_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Calendar instances
    cal_result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connected_by_member_id == member_id)
        .where(HouseholdConnectorInstance.connector_type_id.in_(["google_calendar", "microsoft_calendar"]))
    )
    cal_instances = cal_result.scalars().all()

    calendars_summary = []
    for inst in cal_instances:
        config_r = await session.execute(
            select(MemberCalendarConfig)
            .where(MemberCalendarConfig.household_member_id == member_id)
            .where(MemberCalendarConfig.connector_instance_id == inst.id)
        )
        config = config_r.scalar_one_or_none()
        calendars_summary.append({
            "connector_type": inst.connector_type_id,
            "account": inst.external_account_ref,
            "status": inst.status,
            "sync_calendars": list(zip(
                config.sync_calendar_list if config else [],
                config.sync_calendar_name_list if config else [],
            )),
            "write_calendar": {
                "id": config.write_calendar_id,
                "name": config.write_calendar_name,
            } if config and config.write_calendar_id else None,
            "configured": config is not None,
        })

    # Gmail instance
    gmail_r = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connected_by_member_id == member_id)
        .where(HouseholdConnectorInstance.connector_type_id == "gmail")
    )
    gmail_inst = gmail_r.scalar_one_or_none()

    gmail_summary = None
    if gmail_inst:
        gmail_config_r = await session.execute(
            select(MemberGmailConfig)
            .where(MemberGmailConfig.household_member_id == member_id)
        )
        gmail_config = gmail_config_r.scalar_one_or_none()
        gmail_summary = {
            "account": gmail_inst.external_account_ref,
            "status": gmail_inst.status,
            "watching_labels": list(zip(
                gmail_config.label_list if gmail_config else [],
                gmail_config.label_name_list if gmail_config else [],
            )),
            "configured": gmail_config is not None,
        }

    return {
        "member_id": str(member_id),
        "calendars": calendars_summary,
        "gmail": gmail_summary,
    }


# ── Google Calendar API helper ─────────────────────────────────────────────────

async def _fetch_google_calendars(inst: HouseholdConnectorInstance) -> list[dict]:
    """Fetch all calendars from Google for this connected instance."""
    try:
        from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
        from app.contracts.connectors import ConnectorInstanceDTO
        import json

        logger.info("calendar_list_starting",
                     account=inst.external_account_ref,
                     secret_ref=inst.secret_ref[:50] if inst.secret_ref else "EMPTY")

        adapter = GoogleCalendarAdapter()
        creds = await adapter._load_credentials(ConnectorInstanceDTO(
            id=inst.id, household_id=inst.household_id,
            connector_type_id=inst.connector_type_id,
            connected_by_member_id=inst.connected_by_member_id,
            external_account_ref=inst.external_account_ref,
            status=inst.status,
            secret_ref=inst.secret_ref or "",
        ))
        if not creds:
            logger.error("calendar_list_no_creds",
                         account=inst.external_account_ref,
                         secret_ref=inst.secret_ref[:50] if inst.secret_ref else "EMPTY")
            return [{"id": "primary", "name": "Primary Calendar", "access_role": "owner", "can_write": True, "primary": True}]

        logger.info("calendar_list_creds_loaded",
                     token_present=bool(creds.token),
                     refresh_present=bool(creds.refresh_token),
                     token_preview=creds.token[:20] + "..." if creds.token else "NONE")

        # Direct HTTP call to Google Calendar API (more debuggable than googleapiclient)
        import httpx
        resp = httpx.get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )

        logger.info("calendar_list_api_response",
                     status=resp.status_code,
                     body_preview=resp.text[:300])

        if resp.status_code != 200:
            logger.error("calendar_list_api_failed",
                         status=resp.status_code,
                         body=resp.text[:500],
                         curl=f"curl -s 'https://www.googleapis.com/calendar/v3/users/me/calendarList' -H 'Authorization: Bearer {creds.token}'")
            return [{"id": "primary", "name": "Primary Calendar", "access_role": "owner", "can_write": True, "primary": True}]

        result = resp.json()
        calendars = []
        for cal in result.get("items", []):
            access_role = cal.get("accessRole", "reader")
            calendars.append({
                "id": cal["id"],
                "name": cal.get("summary", cal["id"]),
                "description": cal.get("description", ""),
                "color": cal.get("backgroundColor", "#4285F4"),
                "primary": cal.get("primary", False),
                "access_role": access_role,
                "can_write": access_role in ("owner", "writer"),
                "type": "primary" if cal.get("primary") else (
                    "shared" if "@group.calendar.google.com" in cal["id"] else "other"
                ),
            })

        logger.info("calendar_list_success", count=len(calendars),
                     names=[c["name"] for c in calendars])

        return sorted(calendars, key=lambda c: (not c["primary"], c["name"]))
    except Exception as e:
        logger.error("google_calendar_list_failed", error=str(e), error_type=type(e).__name__)
        return [{"id": "primary", "name": "Primary Calendar", "access_role": "owner", "can_write": True, "primary": True}]


async def _fetch_microsoft_calendars(inst: HouseholdConnectorInstance) -> list[dict]:
    """Fetch all Outlook calendars for this connected instance."""
    try:
        from app.modules.connectors.adapters.microsoft_calendar import MicrosoftCalendarAdapter
        from app.contracts.connectors import ConnectorInstanceDTO

        adapter = MicrosoftCalendarAdapter()
        access_token = await adapter._get_access_token(ConnectorInstanceDTO(
            id=inst.id, household_id=inst.household_id,
            connector_type_id=inst.connector_type_id,
            connected_by_member_id=inst.connected_by_member_id,
            external_account_ref=inst.external_account_ref,
            status=inst.status,
        ))
        if not access_token:
            return [{"id": "default", "name": "Calendar", "can_write": True, "primary": True}]

        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/calendars",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            calendars = resp.json().get("value", [])

        return [
            {
                "id": cal["id"],
                "name": cal.get("name", "Calendar"),
                "color": cal.get("color", "auto"),
                "primary": cal.get("isDefaultCalendar", False),
                "can_write": cal.get("canEdit", False),
                "access_role": "owner" if cal.get("canEdit") else "reader",
                "type": "primary" if cal.get("isDefaultCalendar") else "other",
            }
            for cal in calendars
        ]
    except Exception as e:
        logger.error("microsoft_calendar_list_failed", error=str(e))
        return [{"id": "default", "name": "Calendar", "can_write": True, "primary": True}]


# ── Calendar events (pull from connected Google Calendars) ──────────────────

@router.get("/calendar/events/today")
async def get_today_events(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get all calendar events for today from connected calendars."""
    import pytz
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)
    from datetime import datetime, timedelta, timezone
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = await _fetch_events_for_range(session, current_member.household_id, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    return {"date": now.strftime("%Y-%m-%d"), "timezone": tz_name, "events": events}


@router.get("/calendar/events/tomorrow")
async def get_tomorrow_events(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    import pytz
    from app.modules.identity.api import IdentityService
    from datetime import datetime, timedelta, timezone
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)
    tomorrow = datetime.now(tz) + timedelta(days=1)
    start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = await _fetch_events_for_range(session, current_member.household_id, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    return {"date": tomorrow.strftime("%Y-%m-%d"), "timezone": tz_name, "events": events}


@router.get("/calendar/events/week")
async def get_week_events(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    import pytz
    from app.modules.identity.api import IdentityService
    from datetime import datetime, timedelta, timezone
    identity_svc = IdentityService(session)
    tz_name = await identity_svc.household_timezone(current_member.household_id)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    events = await _fetch_events_for_range(session, current_member.household_id, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    return {"start_date": start.strftime("%Y-%m-%d"), "end_date": (end - timedelta(days=1)).strftime("%Y-%m-%d"), "timezone": tz_name, "events": events}


async def _fetch_events_for_range(session, household_id, start, end):
    """Fetch events from all connected Google Calendars for a date range."""
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == household_id)
        .where(HouseholdConnectorInstance.connector_type_id.in_(["google_calendar"]))
        .where(HouseholdConnectorInstance.status == "connected")
    )
    instances = result.scalars().all()
    if not instances:
        return []

    all_events = []
    for inst in instances:
        config_result = await session.execute(
            select(MemberCalendarConfig).where(MemberCalendarConfig.connector_instance_id == inst.id)
        )
        config = config_result.scalar_one_or_none()
        calendar_ids = config.sync_calendar_list if config else ["primary"]

        try:
            from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
            from app.contracts.connectors import ConnectorInstanceDTO
            adapter = GoogleCalendarAdapter()
            creds = await adapter._load_credentials(ConnectorInstanceDTO(
                id=inst.id, household_id=inst.household_id, connector_type_id=inst.connector_type_id,
                connected_by_member_id=inst.connected_by_member_id, external_account_ref=inst.external_account_ref,
                status=inst.status, secret_ref=inst.secret_ref or "",
            ))
            if not creds:
                continue
        except:
            continue

        import httpx
        for cal_id in calendar_ids:
            try:
                resp = httpx.get(
                    f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events",
                    headers={"Authorization": f"Bearer {creds.token}"},
                    params={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime", "maxResults": "50"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                cal_name = data.get("summary", cal_id)
                for event in data.get("items", []):
                    es = event.get("start", {})
                    ee = event.get("end", {})
                    sdt = es.get("dateTime")
                    all_events.append({
                        "id": event.get("id"), "title": event.get("summary", "(no title)"),
                        "all_day": "dateTime" not in es, "date": es.get("date"),
                        "start_at": sdt, "end_at": ee.get("dateTime"),
                        "location": event.get("location"), "calendar_name": cal_name, "source": "google_calendar",
                    })
            except:
                continue

    all_events.sort(key=lambda e: e.get("start_at") or e.get("date") or "9999")
    return all_events
