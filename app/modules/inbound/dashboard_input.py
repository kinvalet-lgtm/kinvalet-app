"""Dashboard input channel — same AI extraction pipeline as WhatsApp and email.

Three input channels, one brain:
  1. WhatsApp (Meta Cloud API) → inbound webhook → extraction
  2. Email (Cloudflare forwarding) → inbound webhook → extraction
  3. Dashboard (this) → POST /api/v1/assistant/message → extraction

The user types natural language in the dashboard — same as they would in WhatsApp.
The AI extracts tasks, FYIs, delegations, and responds conversationally.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.router import get_current_member
from app.modules.inbound.models import InboundMessage, InboundMessageStatus
from app.platform.db import get_db_session, AsyncSessionFactory
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
logger = get_logger(__name__)


class MessageRequest(BaseModel):
    text: str
    # Optional: pre-tag the intent so the UI can show appropriate response
    intent: Optional[str] = None  # task | fyi | delegate | question


class MessageResponse(BaseModel):
    message_id: str
    status: str  # processing | extracted | error
    reply: Optional[str] = None


class DelegateRequest(BaseModel):
    item_id: str
    to_member_id: str
    note: Optional[str] = None


@router.post("/message", response_model=MessageResponse)
async def send_message(
    body: MessageRequest,
    background_tasks: BackgroundTasks,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Send a message to KinValet from the dashboard — same AI pipeline as WhatsApp.

    User types: "Leo has soccer practice Tuesday at 6pm at Field B"
    AI extracts: creates operational_item, responds with confirmation.
    """
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    # Create inbound message (same table as WhatsApp/email)
    msg = InboundMessage(
        household_id=current_member.household_id,
        household_member_id=current_member.id,
        source="dashboard",
        provider_message_id=f"dashboard:{uuid.uuid4()}",
        media_type="text",
        raw_text=body.text.strip(),
        status=InboundMessageStatus.PROCESSING,
    )
    session.add(msg)
    await session.flush()
    message_id = str(msg.id)
    await session.commit()

    # Enqueue extraction (same pipeline as WhatsApp)
    background_tasks.add_task(
        _extract_and_respond,
        message_id=message_id,
        household_id=str(current_member.household_id),
        member_id=str(current_member.id),
        text=body.text.strip(),
    )

    logger.info(
        "dashboard_message_received",
        household_id=str(current_member.household_id),
        text_preview=body.text[:60],
    )

    return MessageResponse(
        message_id=message_id,
        status="processing",
        reply="Got it — processing your message...",
    )


@router.get("/message/{message_id}/result")
async def get_message_result(
    message_id: str,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Poll for the extraction result of a dashboard message."""
    from app.modules.extraction.models import ExtractionResult

    # Check if extraction is done
    result = await session.execute(
        select(ExtractionResult)
        .where(ExtractionResult.inbound_message_id == uuid.UUID(message_id))
    )
    extraction = result.scalar_one_or_none()

    if extraction is None:
        return {"status": "processing", "reply": "Still thinking..."}

    # Build a human-readable response from the extraction
    tool_calls = extraction.extracted_json.get("tool_calls", [])
    text_response = extraction.extracted_json.get("text_response", "")

    items_created = []
    for tc in tool_calls:
        if tc.get("name") == "create_draft_item":
            args = tc.get("arguments", {})
            items_created.append({
                "title": args.get("title"),
                "category": args.get("category"),
                "start_at": args.get("start_at"),
                "cost_cents": args.get("cost_cents"),
                "confidence": args.get("confidence"),
            })

    if items_created:
        reply_parts = []
        for item in items_created:
            parts = [f"**{item['title']}**"]
            if item.get("start_at"):
                parts.append(f"at {item['start_at']}")
            if item.get("cost_cents"):
                parts.append(f"${item['cost_cents']/100:.2f}")
            reply_parts.append(" — ".join(parts))
        reply = "Got it! Created:\n" + "\n".join(f"• {r}" for r in reply_parts)
    elif text_response:
        reply = text_response
    else:
        reply = "Processed — no actionable items found."

    return {
        "status": "extracted",
        "reply": reply,
        "items_created": items_created,
        "confidence": float(extraction.confidence_score),
        "requires_review": extraction.requires_human_review,
    }


@router.get("/history")
async def get_chat_history(
    limit: int = 20,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Get recent dashboard messages and their extraction results."""
    result = await session.execute(
        select(InboundMessage)
        .where(InboundMessage.household_id == current_member.household_id)
        .where(InboundMessage.source == "dashboard")
        .order_by(InboundMessage.received_at.desc())
        .limit(limit)
    )
    messages = result.scalars().all()

    history = []
    for msg in messages:
        # Check for extraction result
        from app.modules.extraction.models import ExtractionResult
        ext_result = await session.execute(
            select(ExtractionResult)
            .where(ExtractionResult.inbound_message_id == msg.id)
        )
        extraction = ext_result.scalar_one_or_none()

        history.append({
            "id": str(msg.id),
            "text": msg.raw_text,
            "status": msg.status,
            "sent_at": msg.received_at.isoformat(),
            "has_result": extraction is not None,
            "confidence": float(extraction.confidence_score) if extraction else None,
        })

    return history


@router.post("/delegate")
async def quick_delegate(
    body: DelegateRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Quick delegation from the dashboard — assign an item to another family member."""
    from app.modules.operations.models import OperationalItem, TaskDelegation
    from app.platform.events.outbox import add_event_to_outbox

    item = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.id == uuid.UUID(body.item_id))
        .where(OperationalItem.household_id == current_member.household_id)
    )
    item_row = item.scalar_one_or_none()
    if item_row is None:
        raise HTTPException(status_code=404, detail="Item not found")

    delegation = TaskDelegation(
        id=uuid.uuid4(),
        operational_item_id=item_row.id,
        household_id=current_member.household_id,
        delegated_by_member_id=current_member.id,
        delegated_to_member_id=uuid.UUID(body.to_member_id),
        trigger_reason="manual",
        status="proposed",
    )
    session.add(delegation)

    await add_event_to_outbox(session, "DelegationProposed", {
        "delegation_id": str(delegation.id),
        "operational_item_id": str(item_row.id),
        "household_id": str(current_member.household_id),
        "delegated_by_member_id": str(current_member.id),
        "delegated_to_member_id": body.to_member_id,
        "item_title": item_row.title,
        "trigger_reason": "manual",
    })

    await session.commit()
    return {"delegated": True, "delegation_id": str(delegation.id)}


async def _extract_and_respond(
    message_id: str,
    household_id: str,
    member_id: str,
    text: str,
) -> None:
    """Run extraction in background — same pipeline as WhatsApp/email."""
    async with AsyncSessionFactory() as session:
        try:
            from app.modules.extraction.orchestrator import ExtractionOrchestrator
            from app.modules.identity.api import IdentityService
            from app.platform.context import set_request_context

            set_request_context(household_id=household_id, member_id=member_id, actor_type="household_member")

            identity_svc = IdentityService(session)
            members = await identity_svc.list_adult_members(uuid.UUID(household_id))
            member_context = [{"display_name": m.display_name, "role": m.role, "id": str(m.id)} for m in members]

            # Build additional context: recent emails, verification codes, tasks
            extra_context = await _build_household_context(session, uuid.UUID(household_id))
            enriched_text = text
            if extra_context:
                enriched_text = f"{text}\n\n[CONTEXT FOR ANSWERING QUESTIONS]\n{extra_context}"

            orchestrator = ExtractionOrchestrator(session)
            extraction = await orchestrator.extract(
                message_id=uuid.UUID(message_id),
                household_id=uuid.UUID(household_id),
                member_id=uuid.UUID(member_id),
                raw_text=enriched_text,
                media_type="text",
                household_members=member_context,
            )

            # Update message status
            from sqlalchemy import update
            from app.modules.inbound.models import InboundMessage
            await session.execute(
                update(InboundMessage)
                .where(InboundMessage.id == uuid.UUID(message_id))
                .values(status="extracted")
            )

            # If high confidence, create items
            if not extraction.requires_human_review:
                from app.modules.extraction.tasks import _create_item_from_tool_call
                for tc in extraction.extracted_json.get("tool_calls", []):
                    if tc.get("name") == "create_draft_item":
                        await _create_item_from_tool_call(
                            session=session,
                            args=tc["arguments"],
                            extraction_id=extraction.id,
                            household_id=uuid.UUID(household_id),
                            inbound_message_id=uuid.UUID(message_id),
                        )

            await session.commit()
            logger.info("dashboard_extraction_complete", message_id=message_id, confidence=float(extraction.confidence_score))

        except Exception as e:
            logger.error("dashboard_extraction_failed", message_id=message_id, error=str(e))


async def _build_household_context(session: AsyncSession, household_id: uuid.UUID) -> str:
    """Build context about the household's recent data for the LLM to answer questions.

    Includes: recent emails, verification codes, active tasks, calendar events.
    This lets the Assistant answer questions like:
    - "what's my verification code?"
    - "what emails did I receive?"
    - "what's on my plate?"
    """
    parts = []

    # Recent emails received
    try:
        from app.modules.connectors.email_inbound import InboundEmailLog
        email_result = await session.execute(
            select(InboundEmailLog)
            .where(InboundEmailLog.household_id == household_id)
            .order_by(InboundEmailLog.received_at.desc())
            .limit(10)
        )
        emails = email_result.scalars().all()
        if emails:
            email_lines = []
            for e in emails:
                email_lines.append(f"- From: {e.from_email}, Subject: {e.subject}, Status: {e.status}, Preview: {e.body_preview[:100] if e.body_preview else 'N/A'}")
            parts.append("Recent emails received:\n" + "\n".join(email_lines))
    except Exception:
        pass

    # Gmail verification code
    try:
        from app.modules.connectors.email_inbound import MemberInboundAddress
        addr_result = await session.execute(
            select(MemberInboundAddress)
            .where(MemberInboundAddress.household_id == household_id)
            .where(MemberInboundAddress.display_label.startswith("VERIFICATION CODE:"))
        )
        addr = addr_result.scalar_one_or_none()
        if addr:
            code = addr.display_label.replace("VERIFICATION CODE: ", "")
            parts.append(f"Pending Gmail forwarding verification code: {code}")
    except Exception:
        pass

    # Active tasks
    try:
        from app.modules.operations.models import OperationalItem
        items_result = await session.execute(
            select(OperationalItem)
            .where(OperationalItem.household_id == household_id)
            .where(OperationalItem.is_archived == False)
            .where(OperationalItem.status.notin_(["cancelled", "declined"]))
            .order_by(OperationalItem.created_at.desc())
            .limit(10)
        )
        items = items_result.scalars().all()
        if items:
            item_lines = []
            for i in items:
                time_str = f" at {i.start_at.strftime('%b %d %I:%M %p')}" if i.start_at else ""
                item_lines.append(f"- {i.title} ({i.category}, {i.status}){time_str}")
            parts.append("Active tasks:\n" + "\n".join(item_lines))
    except Exception:
        pass

    # Forwarding email address
    try:
        from app.modules.connectors.email_inbound import MemberInboundAddress as MIA
        addr_result2 = await session.execute(
            select(MIA)
            .where(MIA.household_id == household_id)
            .where(MIA.is_active == True)
        )
        addrs = addr_result2.scalars().all()
        if addrs:
            parts.append(f"Family forwarding email address: {addrs[0].address}")
    except Exception:
        pass

    # Today's calendar events
    try:
        from app.modules.connectors.config_router import _fetch_events_for_range
        from datetime import timedelta
        import pytz
        from app.modules.identity.api import IdentityService
        identity_svc = IdentityService(session)
        tz_name = await identity_svc.household_timezone(household_id)
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        events = await _fetch_events_for_range(
            session, household_id,
            start.astimezone(timezone.utc), end.astimezone(timezone.utc),
        )
        if events:
            event_lines = []
            for ev in events:
                time_str = ""
                if ev.get("start_at"):
                    try:
                        from datetime import datetime as dt
                        t = dt.fromisoformat(ev["start_at"].replace("Z", "+00:00")).astimezone(tz)
                        time_str = t.strftime("%I:%M %p")
                    except Exception:
                        time_str = ev["start_at"]
                elif ev.get("all_day"):
                    time_str = "All day"
                loc = f" at {ev['location']}" if ev.get("location") else ""
                cal = f" ({ev.get('calendar_name', '')})" if ev.get("calendar_name") else ""
                event_lines.append(f"- {time_str}: {ev['title']}{loc}{cal}")
            parts.append(f"Today's calendar events ({now.strftime('%A, %B %d')}):\n" + "\n".join(event_lines))
        else:
            parts.append(f"Today's calendar ({now.strftime('%A, %B %d')}): No events scheduled.")
    except Exception:
        pass

    return "\n\n".join(parts)
