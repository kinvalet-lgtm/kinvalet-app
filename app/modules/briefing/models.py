"""Briefing module SQLAlchemy models. Schema: briefing."""
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import DateTime, Date, MetaData, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BriefingBase(DeclarativeBase):
    metadata = MetaData(schema="briefing")


class Briefing(BriefingBase):
    __tablename__ = "briefing"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    briefing_date: Mapped[date] = mapped_column(Date, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # One source of truth for both WhatsApp and dashboard (§9.5)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class BriefingDelivery(BriefingBase):
    __tablename__ = "briefing_delivery"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    briefing_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False, default="whatsapp")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    # queued | sent | delivered | read | failed
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # read_at is the direct instrumentation for OKR O3
