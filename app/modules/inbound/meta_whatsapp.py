"""Meta WhatsApp Cloud API — direct integration (no Twilio).

Replaces Twilio for WhatsApp. Uses Meta's Graph API directly:
- Inbound: webhook receives messages from Meta
- Outbound: send via Graph API (/v21.0/{phone_number_id}/messages)

Setup in Meta Business Suite:
1. Create a Meta App at developers.facebook.com
2. Add WhatsApp product
3. Get: Phone Number ID, Access Token, App Secret, Verify Token
4. Set webhook URL: https://api.kinvalet.com/api/v1/webhooks/whatsapp
5. Subscribe to: messages, message_status

Webhook format (Meta sends JSON, not form data like Twilio):
{
  "object": "whatsapp_business_account",
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "16506001905",
          "id": "wamid.xxx",
          "type": "text",
          "text": {"body": "Leo has soccer..."}
        }]
      }
    }]
  }]
}
"""
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.api import IdentityService
from app.modules.inbound.models import InboundMessage, InboundMessageStatus
from app.platform.config import get_settings
from app.platform.db import get_db_session, AsyncSessionFactory
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/webhooks", tags=["whatsapp"])
logger = get_logger(__name__)
_settings = get_settings()


# ── Webhook verification (Meta sends GET to verify) ──────────────────────────

@router.get("/whatsapp")
async def verify_webhook(request: Request):
    """Meta webhook verification — responds to the challenge.

    Meta sends: GET /whatsapp?hub.mode=subscribe&hub.verify_token=xxx&hub.challenge=yyy
    We verify the token matches ours and return the challenge.
    """
    params = dict(request.query_params)
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    verify_token = _settings.meta_verify_token if hasattr(_settings, 'meta_verify_token') else _settings.secret_key

    if mode == "subscribe" and token == verify_token:
        logger.info("whatsapp_webhook_verified")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


# ── Inbound messages (Meta sends POST with JSON) ─────────────────────────────

@router.post("/whatsapp")
async def receive_whatsapp(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
):
    """Receive WhatsApp messages from Meta Cloud API.

    Must return 200 quickly. Meta retries on non-2xx.
    Signature verified via X-Hub-Signature-256 header.
    """
    # Verify signature
    body_bytes = await request.body()
    if not _verify_signature(request, body_bytes):
        logger.warning("whatsapp_signature_invalid")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    # Meta can send status updates, not just messages
    if payload.get("object") != "whatsapp_business_account":
        return {"ok": True}

    # Process each entry/change
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Handle message status updates (delivered, read)
            if "statuses" in value:
                for status in value["statuses"]:
                    await _handle_status_update(status)
                continue

            # Handle incoming messages
            messages = value.get("messages", [])
            metadata = value.get("metadata", {})
            contacts = value.get("contacts", [])

            for msg in messages:
                await _process_message(
                    session=session,
                    msg=msg,
                    metadata=metadata,
                    contacts=contacts,
                    background_tasks=background_tasks,
                )

    return {"ok": True}


async def _process_message(
    session: AsyncSession,
    msg: dict,
    metadata: dict,
    contacts: list,
    background_tasks: BackgroundTasks,
) -> None:
    """Process a single inbound WhatsApp message."""
    sender_phone = msg.get("from", "")  # e.g. "16506001905" (no +)
    message_id = msg.get("id", "")      # wamid.xxx
    msg_type = msg.get("type", "text")  # text, image, document, audio, etc.
    timestamp = msg.get("timestamp", "")

    # Normalize phone to E.164
    if not sender_phone.startswith("+"):
        sender_phone = f"+{sender_phone}"

    # Extract text body based on message type
    body = ""
    media_url = None
    media_type = "text"

    if msg_type == "text":
        body = msg.get("text", {}).get("body", "")
    elif msg_type == "image":
        media_type = "image"
        media_url = msg.get("image", {}).get("id")  # Media ID, needs separate fetch
        body = msg.get("image", {}).get("caption", "")
    elif msg_type == "document":
        media_type = "pdf"
        media_url = msg.get("document", {}).get("id")
        body = msg.get("document", {}).get("caption", "")
    elif msg_type == "audio":
        media_type = "voice"
        media_url = msg.get("audio", {}).get("id")
    elif msg_type == "interactive":
        # Button replies (delegation accept/decline)
        interactive = msg.get("interactive", {})
        if interactive.get("type") == "button_reply":
            body = interactive.get("button_reply", {}).get("id", "")
        elif interactive.get("type") == "list_reply":
            body = interactive.get("list_reply", {}).get("id", "")

    # Idempotency check
    existing = await session.execute(
        select(InboundMessage)
        .where(InboundMessage.source == "whatsapp")
        .where(InboundMessage.provider_message_id == message_id)
    )
    if existing.scalar_one_or_none():
        logger.info("whatsapp_already_processed", message_id=message_id)
        return

    # Resolve sender
    identity_svc = IdentityService(session)
    member = await identity_svc.resolve_phone(sender_phone)

    if member is None:
        # Unregistered sender — send onboarding message
        msg_record = InboundMessage(
            source="whatsapp",
            provider_message_id=message_id,
            sender_phone_e164=sender_phone,
            media_type=media_type,
            raw_text=body or None,
            status=InboundMessageStatus.UNROUTED,
        )
        session.add(msg_record)
        await session.commit()
        logger.info("unregistered_sender", phone=sender_phone[-4:])

        # Reply with onboarding message
        await send_whatsapp_message(
            sender_phone,
            "Welcome to KinValet! Your number isn't registered yet. "
            "Please ask your household admin to invite you, or sign up at https://app.kinvalet.com"
        )
        return

    if member.status in ("suspended", "removed"):
        msg_record = InboundMessage(
            source="whatsapp",
            provider_message_id=message_id,
            sender_phone_e164=sender_phone,
            household_id=member.household_id,
            household_member_id=member.id,
            media_type=media_type,
            raw_text=body or None,
            status=InboundMessageStatus.UNROUTED,
        )
        session.add(msg_record)
        await session.commit()
        await send_whatsapp_message(
            sender_phone,
            "This number is no longer connected to a KinValet account. Contact support for help."
        )
        return

    # Store the message
    msg_record = InboundMessage(
        source="whatsapp",
        provider_message_id=message_id,
        sender_phone_e164=sender_phone,
        household_id=member.household_id,
        household_member_id=member.id,
        media_type=media_type,
        raw_text=body or None,
        raw_media_url=media_url,
        status=InboundMessageStatus.PROCESSING,
    )
    session.add(msg_record)
    await session.flush()

    stored_id = msg_record.id
    household_id = member.household_id

    await session.commit()

    # Enqueue extraction
    background_tasks.add_task(
        _enqueue_extraction,
        message_id=str(stored_id),
        household_id=str(household_id),
        member_id=str(member.id),
    )

    logger.info(
        "whatsapp_message_received",
        message_id=str(stored_id),
        media_type=media_type,
        household_id=str(household_id),
    )


async def _enqueue_extraction(message_id: str, household_id: str, member_id: str) -> None:
    """Enqueue extraction job."""
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


# ── Outbound messaging (Graph API) ───────────────────────────────────────────

async def send_whatsapp_message(
    to_phone: str,
    message: str,
    reply_to_message_id: Optional[str] = None,
) -> Optional[str]:
    """Send a WhatsApp message via Meta Graph API.

    Returns the message ID (wamid) on success, None on failure.
    """
    phone_number_id = getattr(_settings, 'meta_phone_number_id', '') or ''
    access_token = getattr(_settings, 'meta_access_token', '') or ''

    if not phone_number_id or not access_token:
        logger.warning("meta_whatsapp_not_configured")
        return None

    # Strip + from phone number (Meta wants just digits)
    to_digits = to_phone.lstrip("+")

    payload: dict = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "text",
        "text": {"body": message},
    }

    if reply_to_message_id:
        payload["context"] = {"message_id": reply_to_message_id}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://graph.facebook.com/v21.0/{phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                wamid = data.get("messages", [{}])[0].get("id")
                logger.info("whatsapp_sent", to=to_digits[-4:], wamid=wamid)
                return wamid
            else:
                logger.error("whatsapp_send_failed", status=resp.status_code, body=resp.text[:200])
                return None
    except Exception as e:
        logger.error("whatsapp_send_error", error=str(e))
        return None


async def send_whatsapp_template(
    to_phone: str,
    template_name: str,
    language_code: str = "en_US",
    components: Optional[list] = None,
) -> Optional[str]:
    """Send a pre-approved WhatsApp template message.

    Required for proactive messages (outside 24h session window):
    briefings, reminders, delegation requests.
    """
    phone_number_id = getattr(_settings, 'meta_phone_number_id', '') or ''
    access_token = getattr(_settings, 'meta_access_token', '') or ''

    if not phone_number_id or not access_token:
        return None

    to_digits = to_phone.lstrip("+")

    payload: dict = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }

    if components:
        payload["template"]["components"] = components

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://graph.facebook.com/v21.0/{phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("messages", [{}])[0].get("id")
            else:
                logger.error("whatsapp_template_failed", status=resp.status_code, body=resp.text[:200])
                return None
    except Exception as e:
        logger.error("whatsapp_template_error", error=str(e))
        return None


async def send_whatsapp_interactive(
    to_phone: str,
    body_text: str,
    buttons: list[dict],
    header: Optional[str] = None,
    footer: Optional[str] = None,
) -> Optional[str]:
    """Send an interactive button message (for confirmations, delegation, approvals).

    buttons format: [{"id": "approve", "title": "✅ Approve"}, {"id": "decline", "title": "❌ Decline"}]
    Max 3 buttons per message (WhatsApp limit).
    """
    phone_number_id = getattr(_settings, 'meta_phone_number_id', '') or ''
    access_token = getattr(_settings, 'meta_access_token', '') or ''

    if not phone_number_id or not access_token:
        return None

    to_digits = to_phone.lstrip("+")

    interactive: dict = {
        "type": "button",
        "body": {"text": body_text},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}}
                for btn in buttons[:3]  # Max 3 buttons
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    payload = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "interactive",
        "interactive": interactive,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://graph.facebook.com/v21.0/{phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("messages", [{}])[0].get("id")
            else:
                logger.error("whatsapp_interactive_failed", body=resp.text[:200])
                return None
    except Exception as e:
        logger.error("whatsapp_interactive_error", error=str(e))
        return None


# ── Status updates ────────────────────────────────────────────────────────────

async def _handle_status_update(status: dict) -> None:
    """Handle message delivery/read status updates from Meta.

    Used for OKR O3 (briefing read rate) and delivery tracking.
    """
    wamid = status.get("id", "")
    recipient = status.get("recipient_id", "")
    status_type = status.get("status", "")  # sent, delivered, read, failed

    logger.info("whatsapp_status", wamid=wamid, status=status_type, to=recipient[-4:] if recipient else "")

    # Update briefing delivery read_at for OKR O3
    if status_type == "read":
        async with AsyncSessionFactory() as session:
            from sqlalchemy import update, text
            from app.modules.briefing.models import BriefingDelivery
            await session.execute(
                update(BriefingDelivery)
                .where(BriefingDelivery.status.in_(["sent", "delivered"]))
                .values(status="read", read_at=datetime.now(timezone.utc))
            )
            await session.commit()


# ── Signature verification ────────────────────────────────────────────────────

def _verify_signature(request: Request, body: bytes) -> bool:
    """Verify X-Hub-Signature-256 header from Meta.

    Meta signs webhooks with HMAC-SHA256 using the App Secret.
    """
    app_secret = getattr(_settings, 'meta_app_secret', '') or _settings.secret_key

    if not app_secret:
        return True  # Skip in dev if not configured

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header.startswith("sha256="):
        # In development without Meta configured, allow unsigned requests
        if not getattr(_settings, 'meta_app_secret', ''):
            return True
        return False

    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
