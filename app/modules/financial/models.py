"""Financial module SQLAlchemy models. Schema: financial."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, MetaData, String, Text, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class FinancialBase(DeclarativeBase):
    metadata = MetaData(schema="financial")


class FinancialAccount(FinancialBase):
    """Produced by the Plaid connector's mapToDomainEvents() — not a Plaid-specific table."""
    __tablename__ = "financial_account"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    # FK to connectors instance — not Plaid-only, future-proofed (§9.4)
    household_connector_instance_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    about_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    external_account_ref: Mapped[str] = mapped_column(String(500), nullable=False)  # encrypted at rest
    institution_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    # active | error_reauth_required | disconnected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class FinancialAlert(FinancialBase):
    """Financial anomaly alert (§11.8)."""
    __tablename__ = "financial_alert"
    __table_args__ = (
        # Idempotency: never two alerts for the same transaction + alert_type (§11.8)
        UniqueConstraint(
            "external_account_ref", "provider_transaction_id", "alert_type",
            name="uq_financial_alert_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    financial_account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # possible_scam | duplicate_billing | unexpected_subscription | large_unusual_txn
    external_account_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(String(200), nullable=False)
    transaction_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)  # read-only snapshot
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="new")
    # new | confirmed_issue | false_positive | dismissed
    surfaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
