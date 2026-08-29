"""Audit log — append-only DB writes for all significant actions.

Never updated or deleted. Every action attributable to an actor.
Used by: internal ops (break-glass), compliance, dispute resolution.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, MetaData, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession


class PlatformAuditBase(DeclarativeBase):
    metadata = MetaData(schema="platform")


class AuditLog(PlatformAuditBase):
    """Append-only audit log. Never update or delete rows."""
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # household_member | internal_user | system_agent
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # e.g. household.created, member.invited, phone.changed, admin.break_glass
    target_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )


async def write_audit_log(
    session: AsyncSession,
    action: str,
    actor_type: str,
    actor_id: Optional[uuid.UUID] = None,
    household_id: Optional[uuid.UUID] = None,
    target_type: Optional[str] = None,
    target_id: Optional[uuid.UUID] = None,
    metadata: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Write an audit log entry. Call inside the same transaction as the domain write."""
    entry = AuditLog(
        household_id=household_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=metadata,
        ip_address=ip_address,
    )
    session.add(entry)
    # No commit — caller commits with their domain transaction
