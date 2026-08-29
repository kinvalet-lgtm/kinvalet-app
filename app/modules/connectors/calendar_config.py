"""Calendar and Gmail per-member configuration storage.

After OAuth, we fetch the user's available calendars and labels.
They pick which ones to sync. Those selections are stored here
alongside the connector instance so each family member has
their own independent configuration.

Calendar write access:
- We write ONLY to the calendar the member explicitly selects as writable.
- Read-only calendars (e.g. "Holidays in US", shared school calendars)
  are used for conflict detection ONLY — we never write to them.
- The member explicitly marks one calendar as "primary for new events".
  If none is marked, we prompt before writing.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, MetaData, String, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete


class ConnectorsConfigBase(DeclarativeBase):
    metadata = MetaData(schema="connectors")


class MemberCalendarConfig(ConnectorsConfigBase):
    """Which calendars a member has selected for sync, and which one we write to.

    Per-member: Sarah's config is completely independent of Mark's.
    A member can sync multiple calendars for conflict detection
    but designate exactly ONE as writable (for new event creation).
    """
    __tablename__ = "member_calendar_config"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    connector_instance_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    # Calendar selections (stored as comma-separated provider IDs)
    # e.g. "primary,family@group.calendar.google.com,school@calendar.google.com"
    sync_calendar_ids: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    # The ONE calendar we are allowed to write new events to
    # Must be in sync_calendar_ids. User explicitly consents to this in UI.
    write_calendar_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    write_calendar_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Display names for the sync list (comma-separated, parallel to sync_calendar_ids)
    sync_calendar_names: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )

    @property
    def sync_calendar_list(self) -> list[str]:
        if not self.sync_calendar_ids:
            return ["primary"]
        return [c.strip() for c in self.sync_calendar_ids.split(",") if c.strip()]

    @property
    def sync_calendar_name_list(self) -> list[str]:
        if not self.sync_calendar_names:
            return []
        return [n.strip() for n in self.sync_calendar_names.split(",") if n.strip()]


class MemberGmailConfig(ConnectorsConfigBase):
    """Which Gmail labels a member has selected to watch for extraction.

    Per-member: Sarah watches 'School' + 'Healthcare'; Mark watches 'Work'.
    Only emails matching selected labels are extracted.
    Read-only — we never send, modify, or delete any Gmail messages.
    """
    __tablename__ = "member_gmail_config"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    connector_instance_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    # Selected Gmail label IDs (comma-separated)
    # e.g. "INBOX,Label_123,Label_456"
    selected_label_ids: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    # Display names (comma-separated, parallel to selected_label_ids)
    selected_label_names: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    # History ID for incremental sync (avoids re-processing old emails)
    last_history_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    @property
    def label_list(self) -> list[str]:
        if not self.selected_label_ids:
            return ["INBOX"]
        return [l.strip() for l in self.selected_label_ids.split(",") if l.strip()]

    @property
    def label_name_list(self) -> list[str]:
        if not self.selected_label_names:
            return []
        return [n.strip() for n in self.selected_label_names.split(",") if n.strip()]
