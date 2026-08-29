"""Inbound module SQLAlchemy models. Schema: inbound."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import MetaData


class InboundBase(DeclarativeBase):
    metadata = MetaData(schema="inbound")


class InboundMessageStatus:
    RECEIVED = "received"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    FAILED = "failed"
    UNROUTED = "unrouted"
    FAILED_UNINTELLIGIBLE = "failed_unintelligible"
    FAILED_UNPROCESSABLE = "failed_unprocessable"


class InboundMessage(InboundBase):
    __tablename__ = "inbound_message"
    __table_args__ = (
        # Idempotency: one row per (source, provider_message_id)
        UniqueConstraint("source", "provider_message_id", name="uq_inbound_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null until sender resolved
    household_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True, index=True)
    household_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # whatsapp | email
    # THE idempotency key — Twilio MessageSid or email Message-ID
    provider_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sender_email: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)  # text | voice | image | pdf | multi
    raw_media_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ephemeral; nulled at 72h purge
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
