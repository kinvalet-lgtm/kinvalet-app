"""Connectors module FastAPI routes — OAuth flows and connection management."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.models import (
    HouseholdConnectorEntitlement,
    HouseholdConnectorInstance,
    ConnectorType,
)
from app.modules.connectors.registry import get_adapter
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session
from app.platform.errors import AuthorizationError, NotFoundError
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])
logger = get_logger(__name__)


@router.get("/available")
async def list_available_connectors(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List connector types entitled to this household (AC 7.1 — absent, not disabled)."""
    entitlements = await session.execute(
        select(HouseholdConnectorEntitlement)
        .where(HouseholdConnectorEntitlement.household_id == current_member.household_id)
        .where(HouseholdConnectorEntitlement.revoked_at.is_(None))
    )
    entitled_ids = [e.connector_type_id for e in entitlements.scalars()]
    if not entitled_ids:
        return []

    connector_types = await session.execute(
        select(ConnectorType).where(ConnectorType.id.in_(entitled_ids))
    )

    instances = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
    )
    instance_map = {i.connector_type_id: i for i in instances.scalars()}

    return [
        {
            "id": ct.id,
            "display_name": ct.display_name,
            "category": ct.category,
            "auth_method": ct.auth_method,
            "copy_what_we_read": ct.copy_what_we_read,
            "copy_what_we_write": ct.copy_what_we_write,
            "copy_used_for": ct.copy_used_for,
            "connected": ct.id in instance_map and instance_map[ct.id].status == "connected",
            "status": instance_map[ct.id].status if ct.id in instance_map else "not_connected",
            "external_account_ref": instance_map[ct.id].external_account_ref if ct.id in instance_map else None,
            "last_synced_at": instance_map[ct.id].last_synced_at.isoformat() if ct.id in instance_map and instance_map[ct.id].last_synced_at else None,
        }
        for ct in connector_types.scalars()
    ]


@router.post("/{connector_type_id}/connect")
async def initiate_connect(
    connector_type_id: str,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Start the connection flow — returns OAuth URL or setup instructions."""
    # Verify entitlement (AC 7.1)
    ent = await session.execute(
        select(HouseholdConnectorEntitlement)
        .where(HouseholdConnectorEntitlement.household_id == current_member.household_id)
        .where(HouseholdConnectorEntitlement.connector_type_id == connector_type_id)
        .where(HouseholdConnectorEntitlement.revoked_at.is_(None))
    )
    if not ent.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="This connector is not available for your household.")

    if current_member.role == "secondary_readonly":
        raise HTTPException(status_code=403, detail="Read-only members cannot connect integrations.")

    adapter = get_adapter(connector_type_id)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"No adapter registered for {connector_type_id}")

    auth_url = await adapter.initiate_auth(current_member.household_id, current_member.id)
    return {"auth_url": auth_url, "connector_type_id": connector_type_id}


@router.get("/google/callback")
async def google_oauth_callback(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Handle Google Calendar OAuth callback — complete connection, redirect to dashboard."""
    from fastapi.responses import RedirectResponse
    params = dict(request.query_params)

    # Handle user denying access or Google errors
    if "error" in params:
        error = params.get("error", "unknown")
        logger.warning("google_oauth_denied", error=error)
        return RedirectResponse(f"https://app.kinvalet.com/settings/connected-services?error={error}")

    code = params.get("code", "")
    state = params.get("state", "")
    if not code:
        return RedirectResponse("https://app.kinvalet.com/settings/connected-services?error=no_code")

    try:
        await _complete_oauth_callback("google_calendar", {"code": code, "state": state}, session)
        return RedirectResponse("https://app.kinvalet.com/settings/connected-services?connected=google_calendar")
    except Exception as e:
        logger.error("google_oauth_callback_failed", error=str(e))
        return RedirectResponse(f"https://app.kinvalet.com/settings/connected-services?error=oauth_failed")


@router.get("/gmail/callback")
async def gmail_oauth_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_db_session),
):
    """Handle Gmail OAuth callback — complete connection, redirect to dashboard."""
    await _complete_oauth_callback("gmail", {"code": code, "state": state}, session)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("https://app.kinvalet.com/settings/connected-services?connected=gmail")


@router.get("/microsoft/callback")
async def microsoft_oauth_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_db_session),
):
    """Handle Microsoft OAuth callback — complete connection."""
    return await _complete_oauth_callback("microsoft_calendar", {"code": code, "state": state}, session)


async def _complete_oauth_callback(connector_type_id: str, payload: dict, session: AsyncSession):
    """Shared OAuth completion logic."""
    adapter = get_adapter(connector_type_id)
    if adapter is None:
        raise HTTPException(status_code=500, detail="Adapter not found")

    try:
        instance_dto = await adapter.handle_auth_callback(payload)
    except Exception as e:
        logger.error("oauth_callback_failed", connector=connector_type_id, error=str(e))
        raise HTTPException(status_code=400, detail=f"OAuth failed: {e}")

    # Upsert the connector instance
    existing = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == instance_dto.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == connector_type_id)
        .where(HouseholdConnectorInstance.connected_by_member_id == instance_dto.connected_by_member_id)
    )
    inst = existing.scalar_one_or_none()

    if inst:
        inst.status = "connected"
        inst.external_account_ref = instance_dto.external_account_ref
        inst.secret_ref = instance_dto.secret_ref  # update credentials on reconnect
    else:
        inst = HouseholdConnectorInstance(
            household_id=instance_dto.household_id,
            connector_type_id=connector_type_id,
            connected_by_member_id=instance_dto.connected_by_member_id,
            external_account_ref=instance_dto.external_account_ref,
            secret_ref=instance_dto.secret_ref if hasattr(instance_dto, "secret_ref") else "",
            status="connected",
        )
        session.add(inst)

    # Write consent record
    from app.modules.connectors.models import ConnectorType as CT
    from datetime import datetime, timezone
    from app.modules.identity.models import HouseholdMember

    await session.commit()

    # Publish ConnectorConnected event
    from app.contracts.events import ConnectorConnected
    from app.platform.events.outbox import add_event_to_outbox
    await session.begin()
    await add_event_to_outbox(session, "ConnectorConnected", {
        "household_id": str(instance_dto.household_id),
        "connector_instance_id": str(inst.id) if inst.id else "",
        "connector_type_id": connector_type_id,
        "connected_by_member_id": str(instance_dto.connected_by_member_id),
    })
    await session.commit()

    logger.info("connector_connected", connector=connector_type_id,
                household=str(instance_dto.household_id))
    return {"connected": True, "account": instance_dto.external_account_ref}


@router.delete("/{connector_type_id}/disconnect")
async def disconnect_connector(
    connector_type_id: str,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Disconnect a connector (disconnected_by_user — distinct from revoked_by_admin per §7.3)."""
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == connector_type_id)
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Connector not found")

    if current_member.role == "secondary_readonly":
        raise HTTPException(status_code=403, detail="Read-only members cannot disconnect integrations.")

    # Revoke Google OAuth tokens directly
    if connector_type_id in ("google_calendar", "gmail"):
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
            if creds and creds.token:
                import httpx
                # Revoke the access token at Google's endpoint
                await httpx.AsyncClient().post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": creds.token},
                )
                logger.info("google_token_revoked", account=inst.external_account_ref)
        except Exception as e:
            logger.warning("google_revoke_failed", error=str(e))
    else:
        adapter = get_adapter(connector_type_id)
        if adapter:
            from app.contracts.connectors import ConnectorInstanceDTO
            try:
                await adapter.revoke(ConnectorInstanceDTO(
                    id=inst.id, household_id=inst.household_id,
                    connector_type_id=inst.connector_type_id,
                    connected_by_member_id=inst.connected_by_member_id,
                    external_account_ref=inst.external_account_ref,
                    status=inst.status,
                ))
            except Exception as e:
                logger.warning("connector_revoke_failed", error=str(e))

    # Delete the stored secret/token
    if inst.secret_ref:
        try:
            import os
            if os.path.exists(inst.secret_ref):
                os.remove(inst.secret_ref)
        except Exception:
            pass

    inst.status = "disconnected_by_user"
    inst.secret_ref = ""
    await session.commit()
    logger.info("connector_disconnected", connector=connector_type_id, account=inst.external_account_ref)
    return {"disconnected": True}


@router.get("/{connector_type_id}/health")
async def connector_health(
    connector_type_id: str,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Check connector health (used by Admin Console §13.6)."""
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == connector_type_id)
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Connector not connected")

    adapter = get_adapter(connector_type_id)
    if not adapter:
        raise HTTPException(status_code=404, detail="No adapter registered")

    from app.contracts.connectors import ConnectorInstanceDTO
    health = await adapter.health_check(ConnectorInstanceDTO(
        id=inst.id, household_id=inst.household_id,
        connector_type_id=inst.connector_type_id,
        connected_by_member_id=inst.connected_by_member_id,
        external_account_ref=inst.external_account_ref,
        status=inst.status,
    ))
    return {"status": health.status, "last_checked_at": health.last_checked_at.isoformat(),
            "error": health.error_message}
