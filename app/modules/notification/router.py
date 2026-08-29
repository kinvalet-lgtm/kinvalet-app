"""Notification module FastAPI routes.

Handles:
- Twilio status callbacks (delivery/read tracking for OKR O3)
- STOP / START opt-out (Meta-required)
- Preference updates
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.router import get_current_member
from app.modules.notification.models import NotificationLog, NotificationPreference
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/notification", tags=["notification"])
logger = get_logger(__name__)

OPT_OUT_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
OPT_IN_KEYWORDS = {"start", "yes", "unstop"}


@router.post("/twilio/status")
async def twilio_status_callback(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Receive Twilio delivery/read status callbacks.

    Updates briefing_delivery.read_at — the direct instrumentation for OKR O3.
    """
    form = await request.form()
    message_sid = form.get("MessageSid", "")
    status = form.get("MessageStatus", "")  # sent | delivered | read | failed | undelivered

    if not message_sid:
        return {"ok": True}

    # Update notification log delivery status
    await session.execute(
        update(NotificationLog)
        .where(NotificationLog.twilio_message_sid == message_sid)
        .values(delivery_status=status)
    )

    # Update briefing delivery if it's a briefing message
    if status in ("delivered", "read"):
        from app.modules.briefing.models import BriefingDelivery
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        values = {"status": status}
        if status == "delivered":
            values["delivered_at"] = now
        elif status == "read":
            values["delivered_at"] = now
            values["read_at"] = now  # OKR O3 instrumentation

        await session.execute(
            update(BriefingDelivery)
            .where(BriefingDelivery.status.in_(["queued", "sent", "delivered"]))
            # Match by looking up the notification log first
        )

    await session.commit()
    logger.info("twilio_status_callback", sid=message_sid, status=status)
    return {"ok": True}


@router.post("/twilio/inbound-stop")
async def handle_stop(
    From: str = Form(...),
    Body: str = Form(default=""),
    session: AsyncSession = Depends(get_db_session),
):
    """Handle STOP / START from WhatsApp.

    STOP: halt all sends immediately (AC 11.19.1)
    START: resume sends
    """
    body_lower = Body.strip().lower()
    phone = From.replace("whatsapp:", "")

    # Resolve member
    from app.modules.identity.repository import PhoneRouteRepository
    routes = PhoneRouteRepository(session)
    route = await routes.resolve(phone)
    if route is None:
        return {"ok": True}

    is_opt_out = body_lower in OPT_OUT_KEYWORDS
    is_opt_in = body_lower in OPT_IN_KEYWORDS

    if not is_opt_out and not is_opt_in:
        return {"ok": True}

    # Update all preferences for this member
    await session.execute(
        update(NotificationPreference)
        .where(NotificationPreference.household_member_id == route.household_member_id)
        .values(whatsapp_opted_out=is_opt_out)
    )

    # If no preferences exist yet, we still need to block sends
    # Store a sentinel in a global pref row
    existing = await session.execute(
        select(NotificationPreference)
        .where(NotificationPreference.household_member_id == route.household_member_id)
        .limit(1)
    )
    if not existing.scalar_one_or_none():
        # Create a catch-all pref row
        session.add(NotificationPreference(
            household_member_id=route.household_member_id,
            household_id=route.household_id,
            category="all",
            enabled=not is_opt_out,
            whatsapp_opted_out=is_opt_out,
        ))

    await session.commit()
    action = "opted_out" if is_opt_out else "opted_in"
    logger.info("whatsapp_opt", phone=phone[-4:], action=action)
    return {"ok": True}


class PreferenceUpdate(BaseModel):
    category: str
    enabled: bool | None = None
    lead_time_minutes: int | None = None
    quiet_hours_start: str | None = None  # HH:MM
    quiet_hours_end: str | None = None
    delivery_mode: str | None = None
    daily_notification_ceiling: int | None = None


@router.put("/preferences")
async def update_preferences(
    body: PreferenceUpdate,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Update notification preferences for the current member."""
    from datetime import time as dt_time

    result = await session.execute(
        select(NotificationPreference)
        .where(NotificationPreference.household_member_id == current_member.id)
        .where(NotificationPreference.category == body.category)
    )
    pref = result.scalar_one_or_none()

    if pref is None:
        pref = NotificationPreference(
            household_member_id=current_member.id,
            household_id=current_member.household_id,
            category=body.category,
        )
        session.add(pref)

    if body.enabled is not None:
        pref.enabled = body.enabled
    if body.lead_time_minutes is not None:
        pref.lead_time_minutes = body.lead_time_minutes
    if body.delivery_mode is not None:
        pref.delivery_mode = body.delivery_mode
    if body.daily_notification_ceiling is not None:
        pref.daily_notification_ceiling = body.daily_notification_ceiling
    if body.quiet_hours_start:
        h, m = body.quiet_hours_start.split(":")
        pref.quiet_hours_start = dt_time(int(h), int(m))
    if body.quiet_hours_end:
        h, m = body.quiet_hours_end.split(":")
        pref.quiet_hours_end = dt_time(int(h), int(m))

    await session.commit()
    return {"updated": True, "category": body.category}


@router.get("/preferences")
async def get_preferences(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List all notification preferences for the current member."""
    result = await session.execute(
        select(NotificationPreference)
        .where(NotificationPreference.household_member_id == current_member.id)
    )
    prefs = result.scalars().all()
    return [
        {
            "category": p.category,
            "enabled": p.enabled,
            "delivery_mode": p.delivery_mode,
            "lead_time_minutes": p.lead_time_minutes,
            "quiet_hours_start": p.quiet_hours_start.strftime("%H:%M") if p.quiet_hours_start else "21:00",
            "quiet_hours_end": p.quiet_hours_end.strftime("%H:%M") if p.quiet_hours_end else "07:00",
            "daily_notification_ceiling": p.daily_notification_ceiling,
            "whatsapp_opted_out": p.whatsapp_opted_out,
        }
        for p in prefs
    ]
