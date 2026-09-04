"""Inbound email processing — tenant-aware, per-member, queue-based.

Address format: {family_slug}+{member_number}@mail.kinvalet.com
  e.g. shahdadpuri+1@mail.kinvalet.com  (Sarah, member 1)
       shahdadpuri+2@mail.kinvalet.com  (Mark, member 2)

Flow:
  Gmail filter → Mailgun/SendGrid/Postmark → webhook → resolve tenant+member
  → idempotency check → queue procrastinate job → worker extracts → briefing/actions
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, func, String, DateTime, Text, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import text as sa_text

from app.modules.connectors.calendar_config import ConnectorsConfigBase
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

INBOUND_DOMAIN = "kinvalet.com"


def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '', s)
    return s or "household"


class MemberInboundAddress(ConnectorsConfigBase):
    __tablename__ = "member_inbound_address"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)
    household_slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    member_number: Mapped[int] = mapped_column(Integer, nullable=False)
    display_label: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    emails_received: Mapped[int] = mapped_column(Integer, default=0)
    last_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa_text("now()"))


class InboundEmailLog(ConnectorsConfigBase):
    __tablename__ = "inbound_email_log"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    message_id: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    from_email: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    to_address: Mapped[str] = mapped_column(String(300), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="queued")
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa_text("now()"))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    extraction_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa_text("now()"))


# Gmail forwarding confirmation detection
# Gmail uses various formats:
#   "Confirmation code: 123456789"
#   "confirm this request... code is 123456789"
#   "Your confirmation code is 123456789"
#   Or a clickable link with the code embedded in the URL
GMAIL_CONFIRM_SUBJECT = re.compile(r"forwarding confirmation|mail forwarding", re.IGNORECASE)
GMAIL_CONFIRM_PATTERNS = [
    re.compile(r"(?:confirmation|verify|verification)\s*(?:code|number)[:\s]*(\d{5,12})", re.IGNORECASE),
    re.compile(r"code\s*(?:is|:)\s*(\d{5,12})", re.IGNORECASE),
    re.compile(r"(\d{7,12})", re.IGNORECASE),  # fallback: any 7-12 digit number in a confirmation email
]


def detect_gmail_confirmation(subject: str, body: str) -> Optional[str]:
    """Detect Gmail forwarding confirmation code from subject + body.

    Returns the code if found, None otherwise.
    Also searches the subject itself (Gmail sometimes puts the code there).
    """
    is_confirmation = GMAIL_CONFIRM_SUBJECT.search(subject or "")
    if not is_confirmation:
        return None

    # Search body and subject for the code
    search_text = f"{subject} {body}"
    for pattern in GMAIL_CONFIRM_PATTERNS:
        match = pattern.search(search_text)
        if match:
            code = match.group(1)
            # Filter out obvious non-codes (IP addresses, port numbers, etc.)
            if len(code) >= 7:
                return code

    return None


async def generate_member_address(
    session: AsyncSession,
    household_id: uuid.UUID,
    household_member_id: uuid.UUID,
    household_name: str,
    member_display_name: str,
) -> MemberInboundAddress:
    existing = await session.execute(
        select(MemberInboundAddress)
        .where(MemberInboundAddress.household_member_id == household_member_id)
        .where(MemberInboundAddress.is_active.is_(True))
    )
    if addr := existing.scalar_one_or_none():
        return addr

    slug = slugify(household_name)
    count_result = await session.execute(
        select(func.count()).select_from(MemberInboundAddress)
        .where(MemberInboundAddress.household_id == household_id)
    )
    member_number = (count_result.scalar() or 0) + 1
    address = f"process+{slug}{member_number}@{INBOUND_DOMAIN}"

    record = MemberInboundAddress(
        household_id=household_id,
        household_member_id=household_member_id,
        address=address,
        household_slug=slug,
        member_number=member_number,
        display_label=f"process+{slug}{member_number} ({member_display_name})",
        is_active=True,
    )
    session.add(record)
    await session.flush()
    return record


async def get_all_member_addresses(session, household_id):
    result = await session.execute(
        select(MemberInboundAddress)
        .where(MemberInboundAddress.household_id == household_id)
        .where(MemberInboundAddress.is_active.is_(True))
        .order_by(MemberInboundAddress.member_number)
    )
    return list(result.scalars().all())


async def resolve_address(session, to_email: str):
    """Resolve inbound address to (household_id, member_id).

    Supports:
      process+shahdadpuri1@kinvalet.com  → exact match (member 1)
      process+shahdadpuri@kinvalet.com   → fallback to primary admin (no member number)
    """
    normalized = to_email.strip().lower()

    # Try exact match first
    result = await session.execute(
        select(MemberInboundAddress)
        .where(MemberInboundAddress.address == normalized)
        .where(MemberInboundAddress.is_active.is_(True))
    )
    record = result.scalar_one_or_none()
    if record:
        return record.household_id, record.household_member_id

    # Fallback: extract slug from process+{slug}@domain (no member number)
    # and route to member 1 (primary admin)
    local_part = normalized.split("@")[0] if "@" in normalized else normalized
    if local_part.startswith("process+"):
        slug = local_part.replace("process+", "").rstrip("0123456789")
        if slug:
            result2 = await session.execute(
                select(MemberInboundAddress)
                .where(MemberInboundAddress.household_slug == slug)
                .where(MemberInboundAddress.member_number == 1)
                .where(MemberInboundAddress.is_active.is_(True))
            )
            record2 = result2.scalar_one_or_none()
            if record2:
                return record2.household_id, record2.household_member_id

    return None


def parse_mailgun_webhook(payload: dict) -> dict:
    return {
        "message_id": payload.get("Message-Id", payload.get("message-id", str(uuid.uuid4()))),
        "from": payload.get("from", payload.get("sender", "")),
        "to": payload.get("recipient", payload.get("To", "")),
        "subject": payload.get("subject", ""),
        "body_plain": payload.get("body-plain", payload.get("stripped-text", "")),
        "attachments": int(payload.get("attachment-count", 0)) > 0,
    }


def parse_sendgrid_webhook(payload: dict) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "from": payload.get("from", ""),
        "to": payload.get("to", ""),
        "subject": payload.get("subject", ""),
        "body_plain": payload.get("text", ""),
        "attachments": int(payload.get("attachments", 0)) > 0,
    }


def parse_postmark_webhook(payload: dict) -> dict:
    to_list = payload.get("ToFull", [{}])
    return {
        "message_id": payload.get("MessageID", str(uuid.uuid4())),
        "from": payload.get("FromFull", {}).get("Email", payload.get("From", "")),
        "to": to_list[0].get("Email", "") if to_list else payload.get("To", ""),
        "subject": payload.get("Subject", ""),
        "body_plain": payload.get("TextBody", ""),
        "attachments": len(payload.get("Attachments", [])) > 0,
    }


async def receive_and_queue(session: AsyncSession, parsed: dict, provider: str = "mailgun") -> dict:
    to_email = parsed.get("to", "")
    message_id = parsed.get("message_id", "")
    from_email = parsed.get("from", "")
    subject = parsed.get("subject", "")
    body = parsed.get("body_plain", "") or ""

    resolved = await resolve_address(session, to_email)
    if resolved is None:
        logger.info("email_dropped", to=to_email)
        return {"status": "dropped", "reason": "unrecognized_address"}

    household_id, member_id = resolved

    existing = await session.execute(
        select(InboundEmailLog).where(InboundEmailLog.message_id == message_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate"}

    # Gmail forwarding confirmation code
    code = detect_gmail_confirmation(subject, body)
    if code:
        logger.info("gmail_confirmation_code", code=code, household_id=str(household_id))
        # Store the code in the inbound address record so dashboard + assistant can read it
        addr_result = await session.execute(
            select(MemberInboundAddress)
            .where(MemberInboundAddress.household_id == household_id)
            .where(MemberInboundAddress.household_member_id == member_id)
        )
        addr = addr_result.scalar_one_or_none()
        if addr:
            # Store code in the display_label field temporarily (visible in UI)
            addr.display_label = f"VERIFICATION CODE: {code}"

        log = InboundEmailLog(
            household_id=household_id, household_member_id=member_id,
            message_id=message_id, from_email=from_email, to_address=to_email,
            subject=subject, body_preview=f"Gmail verification code: {code}", status="processed",
        )
        session.add(log)
        await session.commit()
        return {"status": "confirmation_code", "code": code}

    log = InboundEmailLog(
        household_id=household_id, household_member_id=member_id,
        message_id=message_id, from_email=from_email, to_address=to_email,
        subject=subject, body_preview=body[:500] if body else None,
        has_attachments=parsed.get("attachments", False), status="queued",
    )
    session.add(log)

    await session.execute(
        update(MemberInboundAddress)
        .where(MemberInboundAddress.household_id == household_id)
        .where(MemberInboundAddress.household_member_id == member_id)
        .values(emails_received=MemberInboundAddress.emails_received + 1,
                last_received_at=datetime.now(timezone.utc))
    )
    await session.flush()

    # Queue for async extraction
    try:
        from app.modules.connectors.email_tasks import process_email_task
        await process_email_task.defer_async(
            email_log_id=str(log.id),
            household_id=str(household_id),
            member_id=str(member_id),
            subject=subject,
            body=body[:5000],
            from_email=from_email,
        )
        log.status = "processing"
    except Exception as e:
        logger.error("email_queue_failed", error=str(e))

    await session.commit()
    logger.info("email_queued", household_id=str(household_id), subject=subject[:60], provider=provider)
    return {"status": "queued", "household_id": str(household_id), "email_log_id": str(log.id)}
