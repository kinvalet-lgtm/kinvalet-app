"""Operations module SQLAlchemy models. Schema: operations.

OperationalItem is the unified task/event record — one table, not two.
Tasks and calendar events are deliberately one table: most household items
need both calendar semantics (time, location) and task semantics (owner, approval, completion).
"""
import uuid
from datetime import datetime, date
from typing import Optional

from sqlalchemy import Boolean, DateTime, Date, Integer, MetaData, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OperationsBase(DeclarativeBase):
    metadata = MetaData(schema="operations")


class OperationalItem(OperationsBase):
    __tablename__ = "operational_item"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # kids_logistics | parent_care | household_admin | financial_action | vendor_booking
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_inbound_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    # No FK across module schemas — stored as plain UUIDs, validated through interface
    assigned_to_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True, index=True)
    about_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending_confirmation")
    # pending_confirmation | pending_approval | confirmed | declined | completed | expired | cancelled
    calendar_provider_event_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    calendar_sync_status: Mapped[str] = mapped_column(String(50), nullable=False, default="not_synced")
    # not_synced | synced | conflict | sync_failed
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    # critical | normal | low
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    # Recurrence
    recurrence_parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    occurrence_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )

    def effective_end_at(self) -> Optional[datetime]:
        """For overlap checking when end_at is null: use start_at + 30 min placeholder."""
        if self.end_at:
            return self.end_at
        if self.start_at:
            from datetime import timedelta
            return self.start_at + timedelta(minutes=30)
        return None


class ApprovalRequest(OperationsBase):
    __tablename__ = "approval_request"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    requested_of_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    # pending | approved | declined | expired
    expiry_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # whatsapp_button | dashboard
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class TaskDelegation(OperationsBase):
    __tablename__ = "task_delegation"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    delegated_by_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    delegated_to_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    trigger_reason: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    # manual | conflict_detected
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="proposed")
    # proposed | accepted | declined | expired | completed | cancelled
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    response_channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class CalendarConflict(OperationsBase):
    __tablename__ = "calendar_conflict"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    busy_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    conflicting_event_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)  # title + time snapshot only
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    resolution: Mapped[str] = mapped_column(String(50), nullable=False, default="unresolved")
    # auto_delegation_proposed | manual_pick_required | unresolved | resolved_manually
    resulting_delegation_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)


class RecurrenceRule(OperationsBase):
    __tablename__ = "recurrence_rule"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    template_title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_assignee_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    about_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    rrule: Mapped[str] = mapped_column(Text, nullable=False)  # RFC 5545 RRULE subset
    start_time: Mapped[str] = mapped_column(String(10), nullable=False)  # HH:MM
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    until_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    materialized_through: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    # active | ended | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ItemCorrection(OperationsBase):
    """Labelled dataset for extraction quality analysis (§11.17)."""
    __tablename__ = "item_correction"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    extraction_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    correction_channel: Mapped[str] = mapped_column(String(50), nullable=False)
    # whatsapp | dashboard
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
