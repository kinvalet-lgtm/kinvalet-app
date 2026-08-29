"""Notification module SQLAlchemy models. Schema: notification."""
import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, MetaData, String, Time, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class NotificationBase(DeclarativeBase):
    metadata = MetaData(schema="notification")


class NotificationPreference(NotificationBase):
    """Per-member, per-category notification preferences (§11.19)."""
    __tablename__ = "notification_preference"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # daily_briefing | leave_by | task_reminder | conflict_alert | financial_alert | feedback_prompt
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    delivery_mode: Mapped[str] = mapped_column(String(50), nullable=False, default="immediate")
    # immediate | in_briefing
    lead_time_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quiet_hours_start: Mapped[time] = mapped_column(Time, nullable=False, default=time(21, 0))
    quiet_hours_end: Mapped[time] = mapped_column(Time, nullable=False, default=time(7, 0))
    whatsapp_opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_notification_ceiling: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class NotificationLog(NotificationBase):
    """Log of all sent notifications — needed to enforce ceiling and measure over-messaging."""
    __tablename__ = "notification_log"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    template_key: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    held_for_quiet_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    broke_through_quiet_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    batched_into_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    twilio_message_sid: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(50), nullable=False, default="sent")
    # sent | delivered | read | failed
