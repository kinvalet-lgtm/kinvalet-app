"""Financial module routes — alert review, Plaid Link token."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.financial.models import FinancialAlert
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/financial", tags=["financial"])
logger = get_logger(__name__)


@router.get("/alerts")
async def list_financial_alerts(
    status: Optional[str] = "new",
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List financial anomaly alerts for the household."""
    stmt = (
        select(FinancialAlert)
        .where(FinancialAlert.household_id == current_member.household_id)
        .order_by(FinancialAlert.surfaced_at.desc())
    )
    if status:
        stmt = stmt.where(FinancialAlert.status == status)

    result = await session.execute(stmt)
    alerts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "alert_type": a.alert_type,
            "status": a.status,
            "transaction": a.transaction_ref,
            "surfaced_at": a.surfaced_at.isoformat(),
        }
        for a in alerts
    ]


class AlertDecision(BaseModel):
    decision: str  # confirmed_issue | false_positive


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: uuid.UUID,
    body: AlertDecision,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Mark a financial alert as resolved (§11.8).

    The system never takes financial action — review only.
    Confirmed issues include a suggested next action (e.g. call the bank).
    """
    if current_member.role not in ("primary_admin", "co_parent"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to resolve alerts.")

    if body.decision not in ("confirmed_issue", "false_positive"):
        raise HTTPException(status_code=422, detail="Decision must be confirmed_issue or false_positive")

    from datetime import datetime, timezone
    await session.execute(
        update(FinancialAlert)
        .where(FinancialAlert.id == alert_id)
        .where(FinancialAlert.household_id == current_member.household_id)
        .values(
            status=body.decision,
            resolved_at=datetime.now(timezone.utc),
            resolved_by_member_id=current_member.id,
        )
    )
    await session.commit()

    suggested_action = None
    if body.decision == "confirmed_issue":
        suggested_action = "Contact your bank's fraud line immediately and consider freezing this account."

    return {"resolved": True, "suggested_action": suggested_action}


@router.post("/plaid/link-token")
async def create_plaid_link_token(
    current_member=Depends(get_current_member),
):
    """Generate a Plaid Link token for the household to connect their account."""
    from app.platform.config import get_settings
    settings = get_settings()

    if not settings.plaid_client_id:
        raise HTTPException(status_code=503, detail="Plaid not configured")

    try:
        import plaid
        from plaid.api import plaid_api
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
        from plaid.model.products import Products
        from plaid.model.country_code import CountryCode

        env_map = {"sandbox": plaid.Environment.Sandbox, "production": plaid.Environment.Production}
        configuration = plaid.Configuration(
            host=env_map.get(settings.plaid_env, plaid.Environment.Sandbox),
            api_key={
                "clientId": settings.plaid_client_id,
                "secret": settings.plaid_secret,
            },
        )
        api_client = plaid.ApiClient(configuration)
        client = plaid_api.PlaidApi(api_client)

        request = LinkTokenCreateRequest(
            products=[Products("transactions")],
            client_name="KinValet",
            country_codes=[CountryCode("US")],
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id=str(current_member.id)),
        )
        response = client.link_token_create(request)
        return {"link_token": response["link_token"]}
    except Exception as e:
        logger.error("plaid_link_token_failed", error=str(e))
        raise HTTPException(status_code=502, detail="Failed to create Plaid link token")
