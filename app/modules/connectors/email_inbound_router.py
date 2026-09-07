"""Inbound email webhook + member address management API.

Webhooks (unauthenticated — providers send these):
  POST /api/v1/email/inbound/mailgun
  POST /api/v1/email/inbound/sendgrid
  POST /api/v1/email/inbound/postmark

Member address API (authenticated):
  GET  /api/v1/email/addresses      — list all member addresses for household
  POST /api/v1/email/addresses      — generate address for a member
  GET  /api/v1/email/setup-guide    — step-by-step Gmail/Outlook instructions
  GET  /api/v1/email/history        — recent forwarded emails
  GET  /api/v1/email/confirmation   — get pending Gmail confirmation code
"""
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.email_inbound import (
    generate_member_address,
    get_all_member_addresses,
    parse_mailgun_webhook,
    parse_postmark_webhook,
    parse_sendgrid_webhook,
    receive_and_queue,
    InboundEmailLog,
    INBOUND_DOMAIN,
)
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session, AsyncSessionFactory
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/email", tags=["email"])
logger = get_logger(__name__)


# ── Webhooks (unauthenticated) ────────────────────────────────────────────────

@router.post("/inbound/mailgun")
async def mailgun_webhook(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    parsed = parse_mailgun_webhook(dict(form))
    background_tasks.add_task(_process, parsed, "mailgun")
    return {"ok": True}


@router.post("/inbound/sendgrid")
async def sendgrid_webhook(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    parsed = parse_sendgrid_webhook(dict(form))
    background_tasks.add_task(_process, parsed, "sendgrid")
    return {"ok": True}


@router.post("/inbound/postmark")
async def postmark_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}
    parsed = parse_postmark_webhook(payload)
    background_tasks.add_task(_process, parsed, "postmark")
    return {"ok": True}


@router.post("/inbound/cloudflare")
async def cloudflare_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Receive forwarded email from Cloudflare Email Worker.

    The Cloudflare worker POSTs a JSON payload with the parsed email.
    No form parsing needed — it's already structured.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    parsed = {
        "message_id": payload.get("message_id", str(uuid.uuid4())),
        "from": payload.get("from", ""),
        "to": payload.get("to", ""),
        "subject": payload.get("subject", ""),
        "body_plain": payload.get("body_plain", ""),
        "attachments": payload.get("attachments", False),
    }
    background_tasks.add_task(_process, parsed, "cloudflare")
    return {"ok": True}


async def _process(parsed: dict, provider: str):
    async with AsyncSessionFactory() as session:
        try:
            await receive_and_queue(session, parsed, provider)
        except Exception as e:
            logger.error("email_process_failed", provider=provider, error=str(e))


@router.get("/confirmation-code")
async def get_confirmation_code(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get the pending Gmail forwarding verification code.

    When a user adds their KinValet address as a Gmail forwarding destination,
    Gmail sends a verification email with a code. We detect it and store it here.
    The dashboard and assistant can then show it to the user.
    """
    from app.modules.connectors.email_inbound import MemberInboundAddress
    result = await session.execute(
        select(MemberInboundAddress)
        .where(MemberInboundAddress.household_id == current_member.household_id)
        .where(MemberInboundAddress.display_label.startswith("VERIFICATION CODE:"))
    )
    addr = result.scalar_one_or_none()
    if addr:
        code = addr.display_label.replace("VERIFICATION CODE: ", "")
        return {"has_code": True, "code": code, "address": addr.address}
    return {"has_code": False, "code": None}


# ── Member address management (authenticated) ─────────────────────────────────

@router.get("/addresses")
async def list_member_addresses(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List all inbound email addresses for this household.

    Each family member gets their own: shahdadpuri+1, shahdadpuri+2, etc.
    """
    addresses = await get_all_member_addresses(session, current_member.household_id)
    return [
        {
            "member_id": str(a.household_member_id),
            "address": a.address,
            "display_label": a.display_label,
            "member_number": a.member_number,
            "emails_received": a.emails_received,
            "last_received_at": a.last_received_at.isoformat() if a.last_received_at else None,
            "is_active": a.is_active,
        }
        for a in addresses
    ]


class GenerateAddressRequest(BaseModel):
    member_id: Optional[uuid.UUID] = None  # defaults to current member


@router.post("/addresses")
async def create_member_address(
    body: GenerateAddressRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Generate a new inbound address for a family member."""
    target_member_id = body.member_id or current_member.id

    # Get household name for the slug
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    household = await identity_svc.get_household(current_member.household_id)
    member = await identity_svc.get_member(target_member_id)

    if household is None or member is None:
        raise HTTPException(status_code=404, detail="Household or member not found")

    addr = await generate_member_address(
        session=session,
        household_id=current_member.household_id,
        household_member_id=target_member_id,
        household_name=household.name,
        member_display_name=member.display_name,
    )
    await session.commit()

    return {
        "address": addr.address,
        "display_label": addr.display_label,
        "member_number": addr.member_number,
    }


@router.get("/setup-guide")
async def setup_guide(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Step-by-step setup instructions with the member's unique address."""
    # Ensure address exists
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    household = await identity_svc.get_household(current_member.household_id)
    member_dto = await identity_svc.get_member(current_member.id)

    addr = await generate_member_address(
        session, current_member.household_id, current_member.id,
        household.name if household else "household",
        member_dto.display_name if member_dto else "member",
    )
    await session.commit()
    address = addr.address

    return {
        "your_address": address,
        "domain": INBOUND_DOMAIN,
        "instructions": {
            "gmail": {
                "title": "Gmail — forward specific emails to KinValet",
                "steps": [
                    "Open Gmail → Settings (gear icon) → See all settings",
                    "Go to 'Forwarding and POP/IMAP' tab",
                    f"Click 'Add a forwarding address' → enter: {address}",
                    "Gmail sends a VERIFICATION EMAIL to that address (not a code you type)",
                    f"The verification email arrives at KinValet — check your email history below or ask the Assistant: 'show my verification email'",
                    "Click the verification link in that email, OR find the confirmation code and enter it in Gmail",
                    "Once verified, create a filter: click the search filter icon (▼) in Gmail search bar",
                    "Set criteria (e.g. From: *@school.edu, or Has label: Healthcare)",
                    f"Click 'Create filter' → check 'Forward it to: {address}'",
                    "Done — matching emails arrive in KinValet within seconds",
                ],
                "note": "Gmail sends a verification email (not a code) to your KinValet address. You can read it in your email history below, or ask the KinValet Assistant to show it to you.",
            },
            "outlook": {
                "title": "Outlook — set up a forwarding rule",
                "steps": [
                    "Outlook.com → Settings → Mail → Rules → Add new rule",
                    "Name: 'Forward to KinValet'",
                    "Condition: e.g. 'From contains school.edu'",
                    f"Action: Forward to → {address}",
                    "Save",
                ],
                "note": "Only emails matching your rule are forwarded.",
            },
        },
        "tips": [
            "Start with one filter (e.g. school emails) and expand later",
            "Create multiple filters for different sources (school, healthcare, etc.)",
            "To stop: delete the filter in Gmail/Outlook — KinValet stops receiving immediately",
            "Each family member gets their own address — Sarah and Mark forward independently",
        ],
    }


@router.get("/history")
async def email_history(
    limit: int = 20,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    result = await session.execute(
        select(InboundEmailLog)
        .where(InboundEmailLog.household_id == current_member.household_id)
        .order_by(InboundEmailLog.received_at.desc())
        .limit(limit)
    )
    emails = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "from": e.from_email,
            "subject": e.subject,
            # Show full body for verification emails so users can click the link
            "preview": e.body_preview if (e.body_preview and "forwarding" in (e.subject or "").lower()) else (e.body_preview[:100] if e.body_preview else None),
            "full_body": e.body_preview if "forwarding" in (e.subject or "").lower() else None,
            "is_verification": "forwarding" in (e.subject or "").lower(),
            "status": e.status,
            "received_at": e.received_at.isoformat(),
            "member_id": str(e.household_member_id) if e.household_member_id else None,
        }
        for e in emails
    ]
