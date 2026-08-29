"""Inbound module — Twilio WhatsApp webhook handler.

Critical performance constraints (per PRD §11.3):
- Webhook handler must complete in <500ms to satisfy Twilio's timeout
- Extraction is async via the job queue
- Unregistered number reply must happen within 2 seconds
"""
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.request_validator import RequestValidator

from app.contracts.identity import IdentityAPI
from app.platform.config import get_settings
from app.platform.db import get_db_session
from app.platform.errors import WebhookIdempotencyHit, WebhookSignatureError
from app.platform.observability import get_logger
from app.modules.inbound.models import InboundMessage, InboundMessageStatus

router = APIRouter(prefix="/api/v1/webhooks", tags=["inbound"])
logger = get_logger(__name__)
_settings = get_settings()


async def verify_twilio_signature(request: Request) -> None:
    """Verify the X-Twilio-Signature header. Reject with 403 on failure.

    This is a security-critical gate — an invalid signature means the request
    did not originate from Twilio.
    """
    if _settings.is_development and not _settings.twilio_auth_token:
        logger.warning("twilio_signature_verification_skipped", reason="no auth token in dev")
        return

    validator = RequestValidator(_settings.twilio_auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)

    form = await request.form()
    params = dict(form)

    if not validator.validate(url, params, signature):
        raise WebhookSignatureError("Invalid Twilio webhook signature")


def _determine_media_type(
    num_media: int,
    media_content_type: Optional[str],
) -> str:
    if num_media == 0:
        return "text"
    if num_media > 1:
        return "multi"
    if media_content_type:
        if "audio" in media_content_type:
            return "voice"
        if "image" in media_content_type:
            return "image"
        if "pdf" in media_content_type:
            return "pdf"
    return "image"  # default for unknown media


@router.post("/twilio/whatsapp")
async def receive_whatsapp(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    # Standard Twilio webhook fields
    MessageSid: str = Form(...),
    From: str = Form(...),
    Body: str = Form(default=""),
    NumMedia: int = Form(default=0),
    MediaContentType0: Optional[str] = Form(default=None),
    MediaUrl0: Optional[str] = Form(default=None),
):
    """Receive a WhatsApp message from Twilio.

    Must return 200 in <500ms. Extraction is enqueued as a background job.
    """
    try:
        await verify_twilio_signature(request)
    except WebhookSignatureError:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    # Strip "whatsapp:" prefix from Twilio's From field
    sender_phone = From.replace("whatsapp:", "")
    provider_message_id = MessageSid

    # Idempotency check
    from sqlalchemy import select
    existing = await session.execute(
        select(InboundMessage)
        .where(InboundMessage.source == "whatsapp")
        .where(InboundMessage.provider_message_id == provider_message_id)
    )
    if existing.scalar_one_or_none():
        logger.info("webhook_already_processed", message_sid=MessageSid)
        return _twiml_response("")  # 200, no action

    # Resolve sender
    from app.modules.identity.api import IdentityService
    identity_svc = IdentityService(session)
    member = await identity_svc.resolve_phone(sender_phone)

    media_type = _determine_media_type(NumMedia, MediaContentType0)

    if member is None:
        # Unregistered sender — reply with onboarding invitation
        msg = InboundMessage(
            source="whatsapp",
            provider_message_id=provider_message_id,
            sender_phone_e164=sender_phone,
            media_type=media_type,
            raw_text=Body or None,
            status=InboundMessageStatus.UNROUTED,
        )
        session.add(msg)
        await session.commit()
        logger.info("unregistered_sender", phone=sender_phone[-4:])  # log only last 4 digits

        # Reply with onboarding link (within 2 seconds per AC 11.3.1)
        onboarding_msg = (
            "Welcome to KinValet! It looks like your number isn't registered yet. "
            "Please ask your household admin to invite you, or sign up at our website."
        )
        return _twiml_response(onboarding_msg)

    # Check if member is suspended/removed
    if member.status in ("suspended", "removed"):
        msg = InboundMessage(
            source="whatsapp",
            provider_message_id=provider_message_id,
            sender_phone_e164=sender_phone,
            household_id=member.household_id,
            household_member_id=member.id,
            media_type=media_type,
            raw_text=Body or None,
            status=InboundMessageStatus.UNROUTED,
        )
        session.add(msg)
        await session.commit()
        return _twiml_response(
            "This number is no longer connected to a KinValet account. "
            "Please contact support if you need help."
        )

    # Rate limiting: track message count (simplified — full impl uses Redis/DB counter)
    # TODO: implement per-member rate limiting (>30/hr → polite throttle, >200/day → flag to ops)

    # Create inbound message record
    msg = InboundMessage(
        source="whatsapp",
        provider_message_id=provider_message_id,
        sender_phone_e164=sender_phone,
        household_id=member.household_id,
        household_member_id=member.id,
        media_type=media_type,
        raw_text=Body or None,
        raw_media_url=MediaUrl0,
        status=InboundMessageStatus.PROCESSING,
    )
    session.add(msg)
    await session.flush()

    message_id = msg.id
    household_id = member.household_id

    # Commit the inbound message, then enqueue extraction
    # The queue enqueue is part of the same transaction (procrastinate + postgres)
    await session.commit()

    # Enqueue extraction job
    background_tasks.add_task(
        _enqueue_extraction,
        message_id=str(message_id),
        household_id=str(household_id),
        member_id=str(member.id),
    )

    logger.info(
        "message_received",
        message_id=str(message_id),
        media_type=media_type,
        household_id=str(household_id),
    )

    # Ack to Twilio — no content (extraction reply comes async)
    return _twiml_response("")


async def _enqueue_extraction(
    message_id: str,
    household_id: str,
    member_id: str,
) -> None:
    """Enqueue extraction job via procrastinate.

    Opens the queue app if not already open (lazy init), defers the task,
    then closes the app. This avoids depending on FastAPI lifespan for
    the queue connection, which HTTPX ASGITransport doesn't trigger.
    """
    from app.modules.extraction.tasks import extract_message_task
    from app.platform.queue import async_queue_app

    try:
        if async_queue_app.connector._pool is None:
            await async_queue_app.open_async()

        await extract_message_task.defer_async(
            message_id=message_id,
            household_id=household_id,
            member_id=member_id,
        )
        logger.info("extraction_enqueued", message_id=message_id)
    except Exception as e:
        logger.error("extraction_enqueue_failed", message_id=message_id, error=str(e))


def _twiml_response(message: str) -> dict:
    """Return a TwiML-formatted response.

    Using dict here; in production use twilio.twiml.messaging_response.
    """
    if not message:
        # Empty TwiML — no reply sent
        return {"Content-Type": "application/xml", "body": "<?xml version='1.0' encoding='UTF-8'?><Response></Response>"}

    twiml = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<Response>"
        f"<Message>{message}</Message>"
        "</Response>"
    )
    from fastapi.responses import Response
    return Response(content=twiml, media_type="application/xml")
