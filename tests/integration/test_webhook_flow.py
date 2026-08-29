"""Integration: WhatsApp webhook flow (§11.3).

Tests the full inbound processing pipeline:
- Twilio webhook → inbound_message row created
- Idempotency: same MessageSid → no second row
- Unregistered sender → onboarding TwiML reply
- Registered sender → message stored, extraction enqueued
"""
import uuid
import pytest

pytestmark = pytest.mark.integration


async def _register_household(app_client, phone: str, email: str) -> dict:
    """Helper: register a household and return the response data."""
    resp = await app_client.post("/api/v1/identity/register", json={
        "household_name": "Webhook Test Family",
        "timezone": "America/New_York",
        "display_name": "Test User",
        "phone_e164": phone,
        "email": email,
    })
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_unregistered_sender_gets_onboarding_reply(app_client):
    """AC 11.3.1: Unregistered sender gets an onboarding reply in <2s."""
    sid = f"SM_TEST_{uuid.uuid4().hex[:8]}"
    resp = await app_client.post("/api/v1/webhooks/twilio/whatsapp", data={
        "MessageSid": sid,
        "From": "whatsapp:+19995550000",
        "Body": "Hello there",
        "NumMedia": "0",
    })
    assert resp.status_code == 200
    assert "application/xml" in resp.headers.get("content-type", "")
    body = resp.text
    # Should contain a reply (Message tag in TwiML)
    assert "<Message>" in body
    assert "registered" in body.lower() or "invite" in body.lower() or "sign up" in body.lower()


@pytest.mark.asyncio
async def test_registered_sender_message_stored(app_client, unique_phone, unique_email):
    """Registered sender's message is stored with processing status."""
    await _register_household(app_client, unique_phone, unique_email)

    sid = f"SM_TEST_{uuid.uuid4().hex[:8]}"
    resp = await app_client.post("/api/v1/webhooks/twilio/whatsapp", data={
        "MessageSid": sid,
        "From": f"whatsapp:{unique_phone}",
        "Body": "Leo has soccer practice Tuesday at 6pm",
        "NumMedia": "0",
    })
    assert resp.status_code == 200

    # Verify the message was stored in the DB
    from sqlalchemy import select
    from app.modules.inbound.models import InboundMessage
    from app.platform.db import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(InboundMessage).where(InboundMessage.provider_message_id == sid)
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "processing"
        assert msg.raw_text == "Leo has soccer practice Tuesday at 6pm"
        assert msg.source == "whatsapp"
        assert msg.household_id is not None


@pytest.mark.asyncio
async def test_idempotency_same_sid_not_reprocessed(app_client, unique_phone, unique_email):
    """Same MessageSid replayed → stored once, returns 200 both times."""
    await _register_household(app_client, unique_phone, unique_email)

    sid = f"SM_IDEM_{uuid.uuid4().hex[:8]}"
    payload = {
        "MessageSid": sid,
        "From": f"whatsapp:{unique_phone}",
        "Body": "Leo practice",
        "NumMedia": "0",
    }

    r1 = await app_client.post("/api/v1/webhooks/twilio/whatsapp", data=payload)
    r2 = await app_client.post("/api/v1/webhooks/twilio/whatsapp", data=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200

    # Only one row in the DB
    from sqlalchemy import select, func
    from app.modules.inbound.models import InboundMessage
    from app.platform.db import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(InboundMessage)
            .where(InboundMessage.provider_message_id == sid)
        )
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_health_endpoint(app_client):
    """Health check returns 200."""
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
