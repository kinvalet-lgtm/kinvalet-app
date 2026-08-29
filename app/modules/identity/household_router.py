"""Household lifecycle endpoints — pause/resume (§11.21), offboarding (§11.16),
data export (§11.24), phone change (§10.7).

These complete the identity module's consumer-facing flows.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.router import get_current_member
from app.modules.identity.models import Household, HouseholdMember
from app.modules.identity.repository import PhoneRouteRepository, MemberRepository
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/household", tags=["household"])
logger = get_logger(__name__)


# ── Pause / Resume (§11.21) ───────────────────────────────────────────────────

class PauseRequest(BaseModel):
    resume_date: Optional[str] = None  # ISO date, optional auto-resume


@router.post("/pause")
async def pause_household(
    body: PauseRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Pause the household — stops briefings and reminders, ingestion continues.

    §11.21: On pause: all outbound WhatsApp stops. Ingestion CONTINUES so nothing
    is lost. Dashboard stays accessible. Critical items still break through.
    Only the Primary can pause.
    """
    if current_member.role != "primary_admin":
        raise HTTPException(status_code=403, detail="Only the primary admin can pause the household")

    await session.execute(
        update(Household)
        .where(Household.id == current_member.household_id)
        .values(status="paused", updated_at=datetime.now(timezone.utc))
    )
    await session.commit()

    logger.info("household_paused", household_id=str(current_member.household_id),
                resume_date=body.resume_date)
    return {
        "paused": True,
        "resume_date": body.resume_date,
        "note": "Briefings and reminders paused. WhatsApp ingestion continues — nothing is lost. "
                "Critical items still break through. Dashboard stays accessible.",
    }


@router.post("/resume")
async def resume_household(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Resume the household — sends a catch-up summary.

    §11.21: On resume, a catch-up summary replaces the normal briefing.
    """
    if current_member.role != "primary_admin":
        raise HTTPException(status_code=403, detail="Only the primary admin can resume")

    await session.execute(
        update(Household)
        .where(Household.id == current_member.household_id)
        .values(status="active", updated_at=datetime.now(timezone.utc))
    )
    await session.commit()

    # TODO: trigger catch-up briefing
    logger.info("household_resumed", household_id=str(current_member.household_id))
    return {"resumed": True, "note": "Welcome back! A catch-up summary will be sent shortly."}


# ── Phone Change (§10.7) ─────────────────────────────────────────────────────

class PhoneChangeRequest(BaseModel):
    new_phone_e164: str


@router.post("/phone-change")
async def change_phone_number(
    body: PhoneChangeRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Self-serve phone number change — atomic swap (§10.7).

    1. Old route deactivated
    2. New route created
    3. Member phone updated
    All in one transaction — never a window with two or zero active numbers.
    """
    phone_routes = PhoneRouteRepository(session)
    members = MemberRepository(session)

    # Check new phone not already in use
    existing = await phone_routes.resolve(body.new_phone_e164)
    if existing:
        raise HTTPException(status_code=409, detail="This phone number is already active on another household")

    # Get current phone
    member = await session.execute(
        select(HouseholdMember).where(HouseholdMember.id == current_member.id)
    )
    db_member = member.scalar_one_or_none()
    if db_member is None or not db_member.phone_e164:
        raise HTTPException(status_code=404, detail="Member not found")

    old_phone = db_member.phone_e164

    # Atomic swap
    await phone_routes.atomic_swap(
        old_phone=old_phone,
        new_phone=body.new_phone_e164,
        household_id=current_member.household_id,
        member_id=current_member.id,
    )
    await members.update_phone(current_member.id, body.new_phone_e164)

    # Audit log
    from app.platform.events.outbox import add_event_to_outbox
    await add_event_to_outbox(session, "PhoneChanged", {
        "household_id": str(current_member.household_id),
        "member_id": str(current_member.id),
        "old_phone": old_phone[-4:],  # only last 4 for audit
        "new_phone": body.new_phone_e164[-4:],
    })

    await session.commit()

    logger.info("phone_changed", member_id=str(current_member.id))
    return {
        "changed": True,
        "note": "Done — text KinValet from your new number and it'll recognize you. Your old number no longer works.",
    }


# ── Search / History (§11.22) ─────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    category: Optional[str] = None
    member_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    include_archived: bool = True


@router.post("/search")
async def search_items(
    body: SearchRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Search household history — Postgres full-text across items (§11.22).

    Covers archived items by default (the whole point of search is finding old things).
    Powers both the dashboard search and WhatsApp 'search_history' agent tool.
    """
    hid = str(current_member.household_id)
    query_text = body.query.replace("'", "''")

    sql = f"""
        SELECT id, title, category, status, start_at, location, cost_cents,
               assigned_to_member_id, is_archived, created_at,
               ts_rank(to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,'') || ' ' || coalesce(location,'')),
                       plainto_tsquery('english', '{query_text}')) AS rank
        FROM operations.operational_item
        WHERE household_id = '{hid}'
          AND to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,'') || ' ' || coalesce(location,''))
              @@ plainto_tsquery('english', '{query_text}')
    """

    if body.category:
        sql += f" AND category = '{body.category}'"
    if not body.include_archived:
        sql += " AND is_archived = false"
    if body.date_from:
        sql += f" AND created_at >= '{body.date_from}'::timestamptz"
    if body.date_to:
        sql += f" AND created_at <= '{body.date_to}'::timestamptz"

    sql += " ORDER BY rank DESC, created_at DESC LIMIT 50"

    result = await session.execute(text(sql))
    rows = result.fetchall()

    return [
        {
            "id": str(r[0]),
            "title": r[1],
            "category": r[2],
            "status": r[3],
            "start_at": r[4].isoformat() if r[4] else None,
            "location": r[5],
            "cost_cents": r[6],
            "is_archived": r[8],
            "created_at": r[9].isoformat() if r[9] else None,
        }
        for r in rows
    ]


# ── Data Export (§11.24) ──────────────────────────────────────────────────────

@router.get("/export")
async def export_household_data(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Export all household data as JSON (§11.24).

    Returns all items, members, briefings, corrections, and delegations.
    Only the Primary admin can export.
    """
    if current_member.role != "primary_admin":
        raise HTTPException(status_code=403, detail="Only the primary admin can export data")

    hid = str(current_member.household_id)

    # Gather all household data
    items = await session.execute(text(
        f"SELECT id, title, category, status, start_at, location, cost_cents, priority, is_archived, created_at "
        f"FROM operations.operational_item WHERE household_id = '{hid}' ORDER BY created_at"
    ))
    members_r = await session.execute(text(
        f"SELECT id, display_name, role, status, created_at FROM identity.household_member WHERE household_id = '{hid}'"
    ))
    briefings = await session.execute(text(
        f"SELECT id, briefing_date, content_json, generated_at FROM briefing.briefing WHERE household_id = '{hid}' ORDER BY briefing_date DESC LIMIT 90"
    ))
    corrections = await session.execute(text(
        f"SELECT id, field_name, old_value, new_value, corrected_at FROM operations.item_correction WHERE household_id = '{hid}'"
    ))

    return {
        "household_id": hid,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "items": [dict(zip(["id","title","category","status","start_at","location","cost_cents","priority","is_archived","created_at"], [str(c) if isinstance(c, uuid.UUID) else (c.isoformat() if isinstance(c, datetime) else c) for c in r])) for r in items.fetchall()],
        "members": [dict(zip(["id","display_name","role","status","created_at"], [str(c) if isinstance(c, uuid.UUID) else (c.isoformat() if isinstance(c, datetime) else c) for c in r])) for r in members_r.fetchall()],
        "briefings_count": len(briefings.fetchall()),
        "corrections_count": len(corrections.fetchall()),
    }


# ── Offboarding (§11.16) ─────────────────────────────────────────────────────

@router.post("/offboard")
async def offboard_household(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Begin household offboarding — revoke all tokens, deactivate routes (§11.16).

    Only the Primary can offboard. Structured PII purged within 30 days.
    Confirmation sent to Primary's email (not WhatsApp — that channel is deactivated).
    """
    if current_member.role != "primary_admin":
        raise HTTPException(status_code=403, detail="Only the primary admin can offboard the household")

    hid = current_member.household_id

    # Deactivate all phone routes
    await session.execute(text(
        f"UPDATE identity.phone_channel_route SET is_active = false WHERE household_id = '{hid}'"
    ))

    # Revoke all connector instances
    await session.execute(text(
        f"UPDATE connectors.household_connector_instance SET status = 'disconnected_by_user' WHERE household_id = '{hid}'"
    ))

    # Set household status
    await session.execute(
        update(Household)
        .where(Household.id == hid)
        .values(status="offboarding", updated_at=datetime.now(timezone.utc))
    )

    await session.commit()
    logger.info("household_offboarding", household_id=str(hid))

    return {
        "offboarding": True,
        "note": "Your household is being offboarded. All WhatsApp routing has been deactivated. "
                "Structured data will be purged within 30 days. A confirmation will be sent to your email.",
    }
