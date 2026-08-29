"""Support module SQLAlchemy models. Schema: support."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, MetaData, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SupportBase(DeclarativeBase):
    metadata = MetaData(schema="support")


class SupportTicket(SupportBase):
    __tablename__ = "support_ticket"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    raised_by_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    related_operational_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    # whatsapp | dashboard
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # how_do_i | something_wrong | extraction_error | billing_question | cancel_intent | other
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    # open | in_progress | resolved | escalated_to_eng
    assigned_internal_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class FeedbackResponse(SupportBase):
    __tablename__ = "feedback_response"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    prompt_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # moment_thumbs | day_7 | day_30 | nps_30 | nps_60
    sentiment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # positive | negative
    nps_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    free_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
