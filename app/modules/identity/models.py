"""Identity module SQLAlchemy models.

Schema: "identity" — all tables live here and ONLY here.
No other module may import these models (import-linter enforces this).
"""
import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# Every model in this module uses schema="identity" automatically
class IdentityBase(DeclarativeBase):
    metadata = MetaData(schema="identity")


class HouseholdStatus(str, PyEnum):
    active = "active"
    paused = "paused"
    offboarding = "offboarding"
    deleted = "deleted"


class PlanTier(str, PyEnum):
    pilot = "pilot"
    paid = "paid"


class Household(IdentityBase):
    __tablename__ = "household"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)  # IANA zone
    # primary_admin_id: stored as plain UUID — no FK across module schemas
    primary_admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    home_address_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    plan_tier: Mapped[str] = mapped_column(String(50), nullable=False, default="pilot")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )

    # No SQLAlchemy relationship() — no FK constraints across module boundary (ADR-003)
    # Repositories do explicit select() queries instead.


class MemberRole(str, PyEnum):
    primary_admin = "primary_admin"
    co_parent = "co_parent"
    secondary_readonly = "secondary_readonly"
    dependent_minor = "dependent_minor"
    dependent_care_recipient = "dependent_care_recipient"


ADULT_ROLES = {MemberRole.primary_admin, MemberRole.co_parent, MemberRole.secondary_readonly}
DEPENDENT_ROLES = {MemberRole.dependent_minor, MemberRole.dependent_care_recipient}


class MemberStatus(str, PyEnum):
    invited = "invited"
    pending_verification = "pending_verification"
    active = "active"
    suspended = "suspended"
    removed = "removed"


class ChannelIdentityStatus(str, PyEnum):
    active = "active"
    pending = "pending"
    unavailable_no_sms = "unavailable_no_sms"


class HouseholdMember(IdentityBase):
    __tablename__ = "household_member"
    __table_args__ = (
        # Enforce: auth_user_id must be NULL for dependent roles (AC 3.1)
        # This is a check constraint — enforced at DB level, not application
        {"schema": "identity"},
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # Null for dependents; unique across active members platform-wide (enforced via phone_channel_route)
    phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # Must be NULL for dependent roles — enforced by DB constraint
    auth_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="invited")
    channel_identity_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    phone_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )

    @property
    def is_adult(self) -> bool:
        return self.role in {r.value for r in ADULT_ROLES}

    @property
    def is_dependent(self) -> bool:
        return self.role in {r.value for r in DEPENDENT_ROLES}


class PhoneChannelRoute(IdentityBase):
    """Denormalized lookup for the webhook hot path.

    One row per phone number; is_active=False on suspend/remove (preserves audit trail).
    Constraint: at most one row with is_active=True per phone_e164 platform-wide.
    This single constraint is what makes the shared-number model safe.
    """
    __tablename__ = "phone_channel_route"
    __table_args__ = (
        UniqueConstraint(
            "phone_e164",
            "is_active",
            name="uq_phone_active",
            # Partial unique on is_active=True via DB-level index (set up in migration)
        ),
    )

    phone_e164: Mapped[str] = mapped_column(String(20), primary_key=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class InviteStatus(str, PyEnum):
    pending = "pending"
    accepted = "accepted"
    expired = "expired"
    revoked = "revoked"


class MemberInvite(IdentityBase):
    __tablename__ = "member_invite"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    invited_by_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    intended_role: Mapped[str] = mapped_column(String(50), nullable=False)
    invited_phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    invited_email: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AuthSession(IdentityBase):
    """Queryable projection for the Active Sessions UI (§10.6)."""
    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    device_label: Mapped[str] = mapped_column(String(200), nullable=False)
    ip_created_from: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
