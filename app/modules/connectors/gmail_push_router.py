"""Gmail Pub/Sub push webhook + watch management API.

Endpoints:
  POST /api/v1/connectors/gmail/push     — Google Pub/Sub push delivery (no auth)
  POST /api/v1/connectors/gmail/watch    — Register watch for a member's Gmail
  DELETE /api/v1/connectors/gmail/watch  — Stop watch
  GET /api/v1/connectors/gmail/sync-status — View sync state per member

The push endpoint receives notifications from Google Cloud Pub/Sub.
It must respond with 200 quickly (within 10 seconds) or Pub/Sub will retry.
Actual processing happens in the background.

Setup required in Google Cloud Console:
  1. Create a Pub/Sub topic: kinvalet-gmail-push
  2. Grant gmail-api-push@system.gserviceaccount.com Publisher role on the topic
  3. Create a push subscription pointing to:
     https://api-production-57040.up.railway.app/api/v1/connectors/gmail/push
  4. The subscription must have no auth (or use a shared verification token)
"""
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.gmail_sync import GmailHistorySync, GmailSyncState, GmailWatchManager
from app.modules.connectors.models import HouseholdConnectorInstance
from app.modules.connectors.calendar_config import MemberGmailConfig
from app.modules.identity.router import get_current_member
from app.platform.db import get_db_session, AsyncSessionFactory
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/connectors/gmail", tags=["gmail"])
logger = get_logger(__name__)


# ── Pub/Sub push webhook (unauthenticated — Google sends this) ─────────────

@router.post("/push")
async def gmail_push_notification(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Receive a Gmail push notification via Google Cloud Pub/Sub.

    Must return 200 quickly. Processing happens in the background.
    Pub/Sub will retry on non-2xx responses.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}  # Malformed — ack to prevent retries

    # Validate this is from Google (basic check — production should verify JWT)
    if "message" not in body:
        return {"ok": True}

    # Process in background so we return 200 immediately
    background_tasks.add_task(_process_push, body)
    return {"ok": True}


async def _process_push(pubsub_message: dict) -> None:
    """Background: process the Gmail push notification."""
    async with AsyncSessionFactory() as session:
        try:
            sync = GmailHistorySync(session)
            count = await sync.process_push_notification(pubsub_message)
            if count:
                logger.info("gmail_push_processed", messages=count)
        except Exception as e:
            logger.error("gmail_push_processing_failed", error=str(e))


# ── Watch management API (authenticated) ───────────────────────────────────

class WatchRequest(BaseModel):
    member_id: Optional[uuid.UUID] = None


@router.post("/watch")
async def setup_gmail_watch(
    body: WatchRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Register a Gmail watch for push notifications.

    Called automatically after label selection, or manually to re-register.
    The watch lasts 7 days — the worker renews it automatically every 6 days.
    """
    target_member_id = body.member_id or current_member.id

    # Get the Gmail connector instance
    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "gmail")
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
        .where(HouseholdConnectorInstance.status == "connected")
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=404, detail="Gmail not connected for this member")

    # Get selected labels
    config_result = await session.execute(
        select(MemberGmailConfig)
        .where(MemberGmailConfig.connector_instance_id == instance.id)
    )
    config = config_result.scalar_one_or_none()
    label_ids = config.label_list if config else ["INBOX"]

    if not label_ids or label_ids == [""]:
        raise HTTPException(
            status_code=422,
            detail="Select at least one Gmail label before setting up a watch"
        )

    manager = GmailWatchManager(session)
    try:
        result = await manager.setup_watch(instance, label_ids)
        await session.commit()
        return {
            "watching": True,
            "labels": label_ids,
            "historyId": result["historyId"],
            "expiration": result["expiration"],
            "note": "Push notifications active. Watch renews automatically every 6 days.",
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to set up Gmail watch: {e}")


@router.delete("/watch")
async def stop_gmail_watch(
    member_id: Optional[uuid.UUID] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Stop Gmail push notifications for a member."""
    target_member_id = member_id or current_member.id

    result = await session.execute(
        select(HouseholdConnectorInstance)
        .where(HouseholdConnectorInstance.household_id == current_member.household_id)
        .where(HouseholdConnectorInstance.connector_type_id == "gmail")
        .where(HouseholdConnectorInstance.connected_by_member_id == target_member_id)
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        return {"stopped": True, "note": "No Gmail connection found"}

    manager = GmailWatchManager(session)
    await manager.stop_watch(instance)
    await session.commit()
    return {"stopped": True}


@router.get("/sync-status")
async def gmail_sync_status(
    member_id: Optional[uuid.UUID] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """View Gmail sync state for a member — useful for debugging."""
    target_member_id = member_id or current_member.id

    result = await session.execute(
        select(GmailSyncState)
        .where(GmailSyncState.household_member_id == target_member_id)
    )
    state = result.scalar_one_or_none()

    if state is None:
        return {"syncing": False, "note": "No Gmail sync configured"}

    # Count processed messages
    from sqlalchemy import func
    from app.modules.connectors.gmail_sync import GmailProcessedMessage
    count_result = await session.execute(
        select(func.count())
        .select_from(GmailProcessedMessage)
        .where(GmailProcessedMessage.connector_instance_id == state.connector_instance_id)
    )
    total_processed = count_result.scalar() or 0

    return {
        "syncing": True,
        "email": state.gmail_email,
        "history_id": state.history_id,
        "watch_expiration": state.watch_expiration.isoformat() if state.watch_expiration else None,
        "watch_active": state.watch_expiration is not None and state.watch_expiration > __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        "last_synced_at": state.last_synced_at.isoformat() if state.last_synced_at else None,
        "total_messages_processed": total_processed,
        "sync_errors": state.sync_errors,
        "last_error": state.last_error,
    }
