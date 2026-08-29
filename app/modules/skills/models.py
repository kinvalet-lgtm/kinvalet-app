"""Skills module SQLAlchemy models. Schema: skills."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, MetaData, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SkillsBase(DeclarativeBase):
    metadata = MetaData(schema="skills")


class SkillExecution(SkillsBase):
    __tablename__ = "skill_execution"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    skill_type_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # reminder_notification | logistics_eta
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="scheduled")
    # scheduled | executed | failed | cancelled | superseded
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    output_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # e.g. {eta_minutes, leave_by_at, traffic_condition, origin_used}
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class HouseholdMemberAddress(SkillsBase):
    """Origin input for the Logistics Skill (§8.4). No live location tracking in MVP."""
    __tablename__ = "household_member_address"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False, default="home")
    # home | work | other
    address_text: Mapped[str] = mapped_column(Text, nullable=False)
    geocode_lat: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    geocode_lng: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
