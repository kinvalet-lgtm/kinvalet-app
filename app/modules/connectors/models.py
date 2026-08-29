"""Connectors module SQLAlchemy models. Schema: connectors."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, MetaData, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ConnectorsBase(DeclarativeBase):
    metadata = MetaData(schema="connectors")


class ConnectorType(ConnectorsBase):
    """Platform-managed registry of connector types (§9.6)."""
    __tablename__ = "connector_type"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    # google_calendar | microsoft_calendar | plaid_financial | email_forwarding
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # calendar | financial | email_ingestion
    auth_method: Mapped[str] = mapped_column(String(50), nullable=False)
    # oauth2 | webhook_forward_address | api_key
    fixed_permission_profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Required transparency fields (AC 11.2.1) — enforced non-empty at registration
    copy_what_we_read: Mapped[str] = mapped_column(Text, nullable=False)
    copy_what_we_write: Mapped[str] = mapped_column(Text, nullable=False, default="nothing")
    copy_used_for: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    # active | deprecated | beta
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class HouseholdConnectorEntitlement(ConnectorsBase):
    """Controls which connector types are VISIBLE to a household."""
    __tablename__ = "household_connector_entitlement"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    connector_type_id: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled_by_internal_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class HouseholdConnectorInstance(ConnectorsBase):
    """The actual connection: which member, tokens, sync state.

    Unique constraint: one instance per household per type per member.
    This is what makes per-member calendar conflict detection work (§9.6).
    """
    __tablename__ = "household_connector_instance"
    __table_args__ = (
        UniqueConstraint(
            "household_id", "connector_type_id", "connected_by_member_id",
            name="uq_connector_instance"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    connector_type_id: Mapped[str] = mapped_column(String(100), nullable=False)
    connected_by_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")  # OAuth token JSON stored directly
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="connected")
    # not_connected | connecting | connected | error_reauth_required | disconnected_by_user | revoked_by_admin
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
