"""Admin Console API — portfolio metrics, household health, entitlements, ops queue.

Internal-only. Protected by a simple API key in MVP (production: corporate SSO).
Provides the write actions Metabase cannot: entitlement grant/revoke, flag toggles,
support ticket resolution.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.config import get_settings
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
logger = get_logger(__name__)
_settings = get_settings()


def require_admin_key(x_admin_key: str = Header(...)) -> str:
    """Simple API key guard for MVP. Replace with SSO in production."""
    # In dev, any key works. In production, validate against a stored secret.
    if _settings.is_production and x_admin_key != _settings.secret_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return x_admin_key


# ── Portfolio metrics (OKR dashboard) ─────────────────────────────────────────

@router.get("/metrics/okr")
async def okr_metrics(
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """Return OKR O1/O2/O3 metrics across all households."""
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    # O1: 30-day active household retention
    active_hh = await session.execute(text("""
        SELECT COUNT(DISTINCT household_id)
        FROM inbound.inbound_message
        WHERE received_at >= :cutoff
          AND status != 'unrouted'
    """), {"cutoff": thirty_days_ago})

    total_hh = await session.execute(text("SELECT COUNT(*) FROM identity.household WHERE status = 'active'"))

    # O2: avg completed tasks per household per week
    completed_7d = await session.execute(text("""
        SELECT COUNT(*) FROM operations.operational_item
        WHERE status = 'completed' AND updated_at >= :cutoff
    """), {"cutoff": seven_days_ago})

    # O3: briefing open rate (read within 60 min of send)
    briefings_sent = await session.execute(text("""
        SELECT COUNT(*) FROM briefing.briefing_delivery WHERE status IN ('delivered', 'read')
        AND sent_at >= :cutoff
    """), {"cutoff": seven_days_ago})

    briefings_read = await session.execute(text("""
        SELECT COUNT(*) FROM briefing.briefing_delivery
        WHERE status = 'read'
          AND read_at IS NOT NULL
          AND read_at - sent_at <= interval '60 minutes'
          AND sent_at >= :cutoff
    """), {"cutoff": seven_days_ago})

    total = total_hh.scalar() or 1
    sent = briefings_sent.scalar() or 1

    return {
        "o1_active_households_30d": active_hh.scalar() or 0,
        "o1_total_households": total,
        "o1_retention_rate": round((active_hh.scalar() or 0) / total * 100, 1),
        "o2_completed_tasks_7d": completed_7d.scalar() or 0,
        "o2_per_household_per_week": round((completed_7d.scalar() or 0) / total, 1),
        "o3_briefings_sent_7d": sent,
        "o3_read_within_60min": briefings_read.scalar() or 0,
        "o3_open_rate_pct": round((briefings_read.scalar() or 0) / sent * 100, 1),
    }


# ── Household health roster ─────────────────────────────────────────────────

@router.get("/households")
async def list_households(
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """List all households with health indicators."""
    result = await session.execute(text("""
        SELECT
            h.id, h.name, h.status, h.plan_tier, h.created_at,
            COUNT(DISTINCT hm.id) AS member_count
        FROM identity.household h
        LEFT JOIN identity.household_member hm ON hm.household_id = h.id
            AND hm.status = 'active'
        GROUP BY h.id, h.name, h.status, h.plan_tier, h.created_at
        ORDER BY h.created_at DESC
    """))
    rows = result.fetchall()
    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "status": row[2],
            "plan_tier": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "member_count": row[5],
        }
        for row in rows
    ]


# ── Connector entitlement management ──────────────────────────────────────────

class EntitlementGrant(BaseModel):
    household_id: uuid.UUID
    connector_type_id: str


@router.post("/entitlements/grant")
async def grant_entitlement(
    body: EntitlementGrant,
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """Grant a connector entitlement to a household (§11.13).

    Only an internal admin can do this — households cannot self-grant.
    """
    from app.modules.connectors.models import HouseholdConnectorEntitlement

    # Check if already entitled
    existing = await session.execute(
        select(HouseholdConnectorEntitlement)
        .where(HouseholdConnectorEntitlement.household_id == body.household_id)
        .where(HouseholdConnectorEntitlement.connector_type_id == body.connector_type_id)
        .where(HouseholdConnectorEntitlement.revoked_at.is_(None))
    )
    if existing.scalar_one_or_none():
        return {"granted": True, "already_existed": True}

    ent = HouseholdConnectorEntitlement(
        household_id=body.household_id,
        connector_type_id=body.connector_type_id,
        enabled_by_internal_user_id=uuid.UUID(int=0),  # TODO: real internal user
    )
    session.add(ent)
    await session.commit()

    logger.info("entitlement_granted", household=str(body.household_id),
                connector=body.connector_type_id)
    return {"granted": True, "already_existed": False}


@router.post("/entitlements/revoke")
async def revoke_entitlement(
    body: EntitlementGrant,
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """Revoke a connector entitlement (§7.3 — distinct from user disconnect)."""
    from app.modules.connectors.models import HouseholdConnectorEntitlement, HouseholdConnectorInstance

    await session.execute(
        update(HouseholdConnectorEntitlement)
        .where(HouseholdConnectorEntitlement.household_id == body.household_id)
        .where(HouseholdConnectorEntitlement.connector_type_id == body.connector_type_id)
        .where(HouseholdConnectorEntitlement.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    # Also mark active instances as revoked_by_admin
    await session.execute(
        update(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == body.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == body.connector_type_id)
        .where(HouseholdConnectorInstance.status == "connected")
        .values(status="revoked_by_admin")
    )
    await session.commit()
    logger.info("entitlement_revoked", household=str(body.household_id),
                connector=body.connector_type_id)
    return {"revoked": True}


# ── Ops confidence queue ───────────────────────────────────────────────────────

@router.get("/ops/queue")
async def ops_confidence_queue(
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """List extraction results pending human review (§11.14).

    PII masked by default — household shown as token, phones masked.
    """
    result = await session.execute(text("""
        SELECT
            er.id,
            CONCAT('HH-', LEFT(er.household_id::text, 4)) AS household_token,
            er.confidence_score,
            er.model_name,
            er.requires_human_review,
            er.created_at,
            er.reviewed_at
        FROM extraction.extraction_result er
        WHERE er.requires_human_review = true
          AND er.reviewed_at IS NULL
        ORDER BY er.created_at ASC
        LIMIT :limit
    """), {"limit": limit})

    rows = result.fetchall()
    return [
        {
            "extraction_id": str(row[0]),
            "household_token": row[1],
            "confidence_score": float(row[2]),
            "model_name": row[3],
            "created_at": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]


@router.post("/ops/queue/{extraction_id}/review")
async def review_extraction(
    extraction_id: uuid.UUID,
    body: dict,
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """Mark an extraction as reviewed (§11.14). Triggers user confirmation."""
    from app.modules.extraction.models import ExtractionResult
    await session.execute(
        update(ExtractionResult)
        .where(ExtractionResult.id == extraction_id)
        .values(
            reviewed_at=datetime.now(timezone.utc),
            requires_human_review=False,
        )
    )
    await session.commit()
    return {"reviewed": True}


# ── Extraction accuracy metrics ────────────────────────────────────────────────

@router.get("/metrics/extraction")
async def extraction_metrics(
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_admin_key),
):
    """Extraction quality metrics — correction rate by field (§26.4)."""
    result = await session.execute(text("""
        SELECT
            field_name,
            COUNT(*) AS correction_count,
            COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0) AS pct
        FROM operations.item_correction
        WHERE corrected_at >= NOW() - INTERVAL '30 days'
        GROUP BY field_name
        ORDER BY correction_count DESC
    """))
    rows = result.fetchall()

    confidence_result = await session.execute(text("""
        SELECT
            AVG(confidence_score) AS avg_confidence,
            COUNT(*) FILTER (WHERE requires_human_review) * 100.0 / NULLIF(COUNT(*), 0) AS review_rate_pct
        FROM extraction.extraction_result
        WHERE created_at >= NOW() - INTERVAL '30 days'
    """))
    conf = confidence_result.fetchone()

    return {
        "avg_confidence_30d": float(conf[0] or 0),
        "human_review_rate_pct": float(conf[1] or 0),
        "corrections_by_field": [
            {"field": row[0], "count": row[1], "pct": float(row[2] or 0)}
            for row in rows
        ],
    }
