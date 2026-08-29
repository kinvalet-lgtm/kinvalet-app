"""Daily briefing generator — assembles content from across the household.

Runs per household at 8:00 AM household-local time, within ±3 minutes.
Content is assembled from operations items, pending approvals, conflicts,
leave-by times, financial alerts, and overdue items.

The briefing content_json is the single source of truth for both
WhatsApp delivery and the dashboard's Daily Briefing screen (§9.5).
"""
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.briefing.models import Briefing, BriefingDelivery
from app.platform.observability import get_logger

logger = get_logger(__name__)


class BriefingGenerator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def generate(
        self,
        household_id: uuid.UUID,
        briefing_date: date,
    ) -> Briefing:
        """Generate a daily briefing for a household."""
        content = await self._assemble_content(household_id, briefing_date)

        briefing = Briefing(
            household_id=household_id,
            briefing_date=briefing_date,
            content_json=content,
        )
        self._session.add(briefing)
        await self._session.flush()

        logger.info(
            "briefing_generated",
            household_id=str(household_id),
            date=briefing_date.isoformat(),
            sections=list(content.keys()),
        )

        return briefing

    async def _assemble_content(
        self,
        household_id: uuid.UUID,
        briefing_date: date,
    ) -> dict:
        """Assemble briefing sections from across the household data."""
        # Import operations models directly — allowed because briefing is in the same monolith
        # but we go through the operations API contract conceptually
        from app.modules.operations.models import OperationalItem, TaskDelegation, CalendarConflict, ApprovalRequest

        today_start = datetime.combine(briefing_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        today_end = datetime.combine(briefing_date, datetime.max.time()).replace(tzinfo=timezone.utc)

        # Today's items
        today_items_result = await self._session.execute(
            select(OperationalItem)
            .where(OperationalItem.household_id == household_id)
            .where(OperationalItem.start_at >= today_start)
            .where(OperationalItem.start_at <= today_end)
            .where(OperationalItem.is_archived == False)
            .where(OperationalItem.status.notin_(["cancelled", "declined"]))
            .order_by(OperationalItem.start_at)
        )
        today_items = today_items_result.scalars().all()

        # Pending approvals
        pending_approvals_result = await self._session.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.household_id == household_id)
            .where(ApprovalRequest.status == "pending")
        )
        pending_approvals = pending_approvals_result.scalars().all()

        # Unresolved conflicts
        conflicts_result = await self._session.execute(
            select(CalendarConflict)
            .where(CalendarConflict.household_id == household_id)
            .where(CalendarConflict.resolution == "unresolved")
        )
        unresolved_conflicts = conflicts_result.scalars().all()

        # Pending delegations
        delegations_result = await self._session.execute(
            select(TaskDelegation)
            .where(TaskDelegation.household_id == household_id)
            .where(TaskDelegation.status == "proposed")
        )
        pending_delegations = delegations_result.scalars().all()

        # Calendar events from connected Google Calendar (not in our DB)
        calendar_events = []
        try:
            from app.modules.connectors.calendar_sync import fetch_calendar_events_for_date
            calendar_events = await fetch_calendar_events_for_date(
                self._session, household_id, today_start
            )
        except Exception as e:
            logger.warning("briefing_calendar_fetch_failed", error=str(e))

        # Merge KinValet items + external calendar events, sorted by time
        kinvalet_items = [
            {
                "id": str(item.id),
                "title": item.title,
                "category": item.category,
                "start_at": item.start_at.isoformat() if item.start_at else None,
                "location": item.location,
                "priority": item.priority,
                "source": "kinvalet",
            }
            for item in today_items
        ]
        external_events = [
            {
                "id": evt.get("event_id", ""),
                "title": evt["title"],
                "category": "calendar_event",
                "start_at": evt.get("start_at"),
                "end_at": evt.get("end_at"),
                "location": evt.get("location"),
                "priority": "normal",
                "source": "google_calendar",
                "calendar_name": evt.get("calendar_name"),
                "member_id": evt.get("member_id"),
            }
            for evt in calendar_events
        ]
        all_today = sorted(
            kinvalet_items + external_events,
            key=lambda x: x.get("start_at") or "9999",
        )

        sections = {
            "today_items": all_today,
            "pending_approvals": [
                {
                    "id": str(ap.id),
                    "item_id": str(ap.operational_item_id),
                    "amount_cents": ap.amount_cents,
                }
                for ap in pending_approvals
            ],
            "unresolved_conflicts": [
                {
                    "id": str(c.id),
                    "item_id": str(c.operational_item_id),
                }
                for c in unresolved_conflicts
            ],
            "pending_delegations": [
                {
                    "id": str(d.id),
                    "item_id": str(d.operational_item_id),
                    "to_member_id": str(d.delegated_to_member_id),
                }
                for d in pending_delegations
            ],
            "is_quiet_day": (
                not all_today  # includes both KinValet items AND calendar events
                and not pending_approvals
                and not unresolved_conflicts
                and not pending_delegations
            ),
        }

        return sections

    async def format_whatsapp_message(self, briefing: Briefing) -> str:
        """Format the briefing content for WhatsApp delivery."""
        content = briefing.content_json

        if content.get("is_quiet_day"):
            return "All quiet today ✅ — nothing needs your attention."

        lines = ["*Your daily briefing* 📋\n"]

        today_items = content.get("today_items", [])
        if today_items:
            lines.append("*Today's schedule:*")
            for item in today_items:
                time_str = ""
                if item.get("start_at"):
                    dt = datetime.fromisoformat(item["start_at"])
                    time_str = f" at {dt.strftime('%-I:%M %p')}"
                location_str = f" @ {item['location']}" if item.get("location") else ""
                lines.append(f"• {item['title']}{time_str}{location_str}")

        approvals = content.get("pending_approvals", [])
        if approvals:
            lines.append(f"\n*Needs approval:* {len(approvals)} item(s)")

        conflicts = content.get("unresolved_conflicts", [])
        if conflicts:
            lines.append(f"\n⚠️ *Unresolved conflicts:* {len(conflicts)}")

        delegations = content.get("pending_delegations", [])
        if delegations:
            lines.append(f"\n*Awaiting your response:* {len(delegations)} delegation(s)")

        return "\n".join(lines)
