"""Household cost ledger — per-household unit economics (§26.5 / §9.8).

ops_minutes is the most easily overlooked cost line. Human review time
determines whether this business has a gross margin — it only falls if
extraction accuracy improves. Tracking from day one makes the confidence-
threshold decision (§23.2) an economic decision rather than a guess.
"""
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Integer, MetaData, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update


class PlatformLedgerBase(DeclarativeBase):
    metadata = MetaData(schema="platform")


class HouseholdCostLedger(PlatformLedgerBase):
    __tablename__ = "household_cost_ledger"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    period_month: Mapped[date] = mapped_column(nullable=False)  # first day of month
    whatsapp_conversation_cents: Mapped[int] = mapped_column(Integer, default=0)
    llm_token_cents: Mapped[int] = mapped_column(Integer, default=0)
    stt_minutes_cents: Mapped[int] = mapped_column(Integer, default=0)
    maps_api_cents: Mapped[int] = mapped_column(Integer, default=0)
    ops_minutes: Mapped[int] = mapped_column(Integer, default=0)  # human review time
    total_cents: Mapped[int] = mapped_column(Integer, default=0)


async def record_cost(
    session: AsyncSession,
    household_id: uuid.UUID,
    event_type: str,  # llm_tokens | whatsapp_conversation | maps_api | stt_minutes | ops_minutes
    amount: int,      # cents (or minutes for ops_minutes)
) -> None:
    """Upsert a cost event into the monthly ledger row for this household."""
    from datetime import date, datetime
    period = date.today().replace(day=1)

    result = await session.execute(
        select(HouseholdCostLedger)
        .where(HouseholdCostLedger.household_id == household_id)
        .where(HouseholdCostLedger.period_month == period)
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = HouseholdCostLedger(
            household_id=household_id,
            period_month=period,
        )
        session.add(row)

    column_map = {
        "llm_tokens": "llm_token_cents",
        "whatsapp_conversation": "whatsapp_conversation_cents",
        "maps_api": "maps_api_cents",
        "stt_minutes": "stt_minutes_cents",
        "ops_minutes": "ops_minutes",
    }
    col = column_map.get(event_type)
    if col:
        current = getattr(row, col, 0) or 0
        setattr(row, col, current + amount)

    # Recompute total
    row.total_cents = (
        (row.whatsapp_conversation_cents or 0)
        + (row.llm_token_cents or 0)
        + (row.stt_minutes_cents or 0)
        + (row.maps_api_cents or 0)
    )
