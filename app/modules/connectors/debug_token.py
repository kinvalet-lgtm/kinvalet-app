"""Temporary debug endpoint to retrieve stored Google token. Remove after debugging."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.models import HouseholdConnectorInstance
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


@router.get("/google-token")
async def get_google_token(
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "google_calendar")
    )
    inst = result.scalar_one_or_none()
    if not inst:
        return {"error": "not connected"}

    secret_ref = inst.secret_ref or ""

    # Try to load and return the token
    token_data = None
    if secret_ref:
        try:
            with open(secret_ref) as f:
                import json
                token_data = json.load(f)
        except Exception as e:
            token_data = {"load_error": str(e), "secret_ref": secret_ref}

    return {
        "status": inst.status,
        "account": inst.external_account_ref,
        "secret_ref": secret_ref,
        "secret_ref_empty": not bool(secret_ref),
        "token_data": token_data,
    }
