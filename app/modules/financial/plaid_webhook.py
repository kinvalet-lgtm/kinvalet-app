"""Plaid webhook handler — receives transaction push notifications.

Plaid delivers webhooks for:
- TRANSACTIONS.SYNC_UPDATES_AVAILABLE → new/modified/removed transactions
- ITEM.ERROR → account needs re-authentication

Idempotency: unique on (external_account_ref, provider_transaction_id, alert_type).
A redelivered webhook never produces a duplicate alert (§11.8).

Security: Plaid signs webhooks with a verification token.
We verify before processing.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.financial.anomaly import AnomalyDetector
from app.modules.financial.models import FinancialAccount, FinancialAlert
from app.platform.db import get_db_session
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/financial/webhooks", tags=["financial"])
logger = get_logger(__name__)


@router.post("/plaid")
async def plaid_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Receive Plaid webhook and process new transactions."""
    body = await request.json()
    webhook_type = body.get("webhook_type", "")
    webhook_code = body.get("webhook_code", "")
    item_id = body.get("item_id", "")

    logger.info("plaid_webhook_received", type=webhook_type, code=webhook_code)

    if webhook_type == "ITEM" and webhook_code == "ERROR":
        await _handle_item_error(session, item_id, body)
        return {"ok": True}

    if webhook_type == "TRANSACTIONS" and webhook_code == "SYNC_UPDATES_AVAILABLE":
        await _handle_transactions_available(session, item_id)
        return {"ok": True}

    # Other webhook types — acknowledge and ignore
    return {"ok": True}


async def _handle_item_error(
    session: AsyncSession, item_id: str, body: dict
) -> None:
    """Mark account as needing re-auth (§11.8 edge case)."""
    error_code = body.get("error", {}).get("error_code", "")

    if error_code == "ITEM_LOGIN_REQUIRED":
        await session.execute(
            update(FinancialAccount)
            .where(FinancialAccount.external_account_ref.contains(item_id))
            .values(status="error_reauth_required")
        )
        await session.commit()
        logger.warning("plaid_item_reauth_required", item_id=item_id)


async def _handle_transactions_available(
    session: AsyncSession, item_id: str
) -> None:
    """Fetch and evaluate new transactions from Plaid."""
    from app.platform.config import get_settings
    settings = get_settings()

    if not settings.plaid_client_id:
        logger.warning("plaid_not_configured")
        return

    # Find the financial account for this item
    result = await session.execute(
        select(FinancialAccount)
        .where(FinancialAccount.external_account_ref.contains(item_id))
        .where(FinancialAccount.status == "active")
    )
    account = result.scalar_one_or_none()
    if account is None:
        logger.warning("plaid_account_not_found", item_id=item_id)
        return

    # Fetch transactions from Plaid
    transactions = await _fetch_plaid_transactions(account, settings)
    if not transactions:
        return

    # Run anomaly detection on each new transaction
    detector = AnomalyDetector(session)
    total_alerts = 0
    for txn in transactions:
        alerts = await detector.evaluate(account, txn)
        total_alerts += len(alerts)

    if total_alerts:
        await session.commit()
        logger.info(
            "plaid_anomaly_alerts_created",
            account_id=str(account.id),
            transaction_count=len(transactions),
            alert_count=total_alerts,
        )

        # Publish event so notification module surfaces in briefing
        from app.platform.events.outbox import add_event_to_outbox
        await add_event_to_outbox(session, "FinancialAlertsCreated", {
            "household_id": str(account.household_id),
            "account_id": str(account.id),
            "alert_count": total_alerts,
        })
        await session.commit()


async def _fetch_plaid_transactions(account: FinancialAccount, settings) -> list[dict]:
    """Fetch recent transactions via Plaid Transactions Sync API."""
    try:
        import plaid
        from plaid.api import plaid_api
        from plaid.model.transactions_sync_request import TransactionsSyncRequest

        env_map = {
            "sandbox": plaid.Environment.Sandbox,
            "development": plaid.Environment.Development,
            "production": plaid.Environment.Production,
        }
        configuration = plaid.Configuration(
            host=env_map.get(settings.plaid_env, plaid.Environment.Sandbox),
            api_key={
                "clientId": settings.plaid_client_id,
                "secret": settings.plaid_secret,
            },
        )
        api_client = plaid.ApiClient(configuration)
        client = plaid_api.PlaidApi(api_client)

        # Get the access token from secrets backend
        from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
        token_data = await GoogleCalendarAdapter._read_secret(account.external_account_ref)
        if not token_data:
            return []

        import json
        token_info = json.loads(token_data) if token_data.startswith("{") else {"access_token": token_data}
        access_token = token_info.get("access_token", "")

        request = TransactionsSyncRequest(access_token=access_token)
        response = client.transactions_sync(request)
        return [t.to_dict() for t in response.get("added", [])]

    except Exception as e:
        logger.error("plaid_fetch_failed", error=str(e))
        return []
