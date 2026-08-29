"""Delegation accept/decline via signed token — WhatsApp one-tap actions (§11.5).

The delegate receives a WhatsApp message with one-tap buttons carrying
a signed, single-use, short-lived token. No login required.

Token scope: one specific delegation, single-use, expires in 48h.
It grants exactly one action and confers no session, no dashboard access,
and no read access to anything else (§10.3).
"""
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operations.models import OperationalItem, TaskDelegation
from app.platform.config import get_settings
from app.platform.db import get_db_session
from app.platform.events.outbox import add_event_to_outbox
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/delegation", tags=["delegation"])
logger = get_logger(__name__)
_settings = get_settings()

TOKEN_EXPIRY_HOURS = 48


def generate_delegation_token(delegation_id: uuid.UUID) -> str:
    """Generate a signed, time-limited token for one-tap delegation response."""
    ts = str(int(time.time()))
    payload = f"{delegation_id}:{ts}"
    sig = hmac.new(_settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{delegation_id}:{ts}:{sig}"


def verify_delegation_token(token: str) -> uuid.UUID:
    """Verify and extract delegation_id from a signed token. Raises on invalid/expired."""
    parts = token.split(":")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    delegation_id_str, ts, sig = parts

    # Verify signature
    payload = f"{delegation_id_str}:{ts}"
    expected_sig = hmac.new(_settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Invalid token signature")

    # Check expiry
    created = int(ts)
    if time.time() - created > TOKEN_EXPIRY_HOURS * 3600:
        raise ValueError("Token expired")

    return uuid.UUID(delegation_id_str)


# ── One-tap endpoints (no auth — token-scoped) ────────────────────────────────

@router.post("/accept")
async def accept_delegation(
    token: str,
    session: AsyncSession = Depends(get_db_session),
):
    """Accept a delegation via signed token — one-tap WhatsApp action.

    §11.5: Accept → status→accepted, item reassigned immediately.
    The delegator is notified. No further confirmation from delegator required
    (differs from money-approval flow which does require payer approval).
    """
    try:
        delegation_id = verify_delegation_token(token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await session.execute(
        select(TaskDelegation).where(TaskDelegation.id == delegation_id)
    )
    delegation = result.scalar_one_or_none()
    if delegation is None:
        raise HTTPException(status_code=404, detail="Delegation not found")

    # Idempotent guard: proposed → accepted happens exactly once (§11.5)
    if delegation.status != "proposed":
        return {
            "already_resolved": True,
            "status": delegation.status,
            "note": "Looks like this is already sorted!" if delegation.status == "accepted" else f"Status: {delegation.status}",
        }

    now = datetime.now(timezone.utc)
    delegation.status = "accepted"
    delegation.responded_at = now
    delegation.response_channel = "whatsapp_button"

    # Reassign the item
    await session.execute(
        update(OperationalItem)
        .where(OperationalItem.id == delegation.operational_item_id)
        .values(
            assigned_to_member_id=delegation.delegated_to_member_id,
            updated_at=now,
        )
    )

    # Publish events
    await add_event_to_outbox(session, "DelegationAccepted", {
        "delegation_id": str(delegation.id),
        "operational_item_id": str(delegation.operational_item_id),
        "household_id": str(delegation.household_id),
        "delegated_to_member_id": str(delegation.delegated_to_member_id),
    })

    await session.commit()

    logger.info("delegation_accepted", delegation_id=str(delegation_id))
    return {"accepted": True, "delegation_id": str(delegation_id)}


@router.post("/decline")
async def decline_delegation(
    token: str,
    session: AsyncSession = Depends(get_db_session),
):
    """Decline a delegation — item stays with the original assignee.

    §11.5: Decline → item stays with delegator, who is notified.
    """
    try:
        delegation_id = verify_delegation_token(token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await session.execute(
        select(TaskDelegation).where(TaskDelegation.id == delegation_id)
    )
    delegation = result.scalar_one_or_none()
    if delegation is None:
        raise HTTPException(status_code=404, detail="Delegation not found")

    if delegation.status != "proposed":
        return {"already_resolved": True, "status": delegation.status}

    delegation.status = "declined"
    delegation.responded_at = datetime.now(timezone.utc)
    delegation.response_channel = "whatsapp_button"

    await add_event_to_outbox(session, "DelegationDeclined", {
        "delegation_id": str(delegation.id),
        "operational_item_id": str(delegation.operational_item_id),
        "household_id": str(delegation.household_id),
        "delegated_to_member_id": str(delegation.delegated_to_member_id),
    })

    await session.commit()

    logger.info("delegation_declined", delegation_id=str(delegation_id))
    return {"declined": True, "delegation_id": str(delegation_id)}
