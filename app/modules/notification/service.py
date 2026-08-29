"""Notification enforcement service.

Implements all rules from PRD §11.19:
- Quiet hours: hold non-urgent messages, deliver at window end
- Daily ceiling (default 12): batch excess into digest
- Priority breakthrough: critical always fires regardless of quiet hours
- STOP/START: Meta-required opt-out (halts all sends immediately)
- Delegation requests cannot be disabled (hardcoded exception)

Every quiet-hours breakthrough is logged (notification_log.broke_through_quiet_hours)
so a too-loose urgency definition is visible rather than invisible.
"""
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notification.models import NotificationLog, NotificationPreference
from app.platform.config import get_settings
from app.platform.observability import get_logger, log_cost_event

logger = get_logger(__name__)
_settings = get_settings()

# Notification categories that cannot be disabled (PRD §11.19)
ALWAYS_ON_CATEGORIES = {"delegation_request"}

# Categories where delivery is always immediate regardless of quiet hours
ALWAYS_URGENT_CATEGORIES = {"leave_by"}  # time-critical logistics


class NotificationEnforcer:
    """Checks all rules before sending. Returns (should_send, reason)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(
        self,
        member_id: UUID,
        household_id: UUID,
        category: str,
        priority: str,
        member_timezone: str = "America/New_York",
    ) -> tuple[bool, str]:
        """Return (should_send_now, reason).

        Possible reasons: 'send', 'opted_out', 'held_quiet_hours', 'held_ceiling_digest'
        """
        prefs = await self._get_prefs(member_id, category)

        # 1. Hard opt-out (STOP) — always blocks
        if prefs and prefs.whatsapp_opted_out:
            return False, "opted_out"

        # 2. Delegation requests cannot be disabled (PRD §11.19)
        if category in ALWAYS_ON_CATEGORIES:
            return True, "send"

        # 3. Category disabled
        if prefs and not prefs.enabled:
            return False, "category_disabled"

        # 4. Quiet hours check
        now_local = self._now_local(member_timezone)
        in_quiet = self._in_quiet_hours(
            now_local.time(),
            prefs.quiet_hours_start if prefs else time(21, 0),
            prefs.quiet_hours_end if prefs else time(7, 0),
        )

        if in_quiet and category not in ALWAYS_URGENT_CATEGORIES:
            if priority == "critical":
                # Critical always breaks through quiet hours (PRD §11.19)
                logger.info("quiet_hours_breakthrough", member_id=str(member_id), category=category)
                return True, "breakthrough"
            return False, "held_quiet_hours"

        # 5. Daily ceiling check
        ceiling = prefs.daily_notification_ceiling if prefs else _settings.notification_daily_ceiling
        today_count = await self._count_today(member_id, member_timezone)
        if today_count >= ceiling and priority != "critical":
            return False, "held_ceiling_digest"

        return True, "send"

    async def _get_prefs(self, member_id: UUID, category: str) -> Optional[NotificationPreference]:
        result = await self._session.execute(
            select(NotificationPreference)
            .where(NotificationPreference.household_member_id == member_id)
            .where(NotificationPreference.category == category)
        )
        return result.scalar_one_or_none()

    async def _count_today(self, member_id: UUID, timezone_name: str) -> int:
        now_local = self._now_local(timezone_name)
        today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert to UTC for DB comparison
        import pytz
        tz = pytz.timezone(timezone_name)
        today_start_utc = tz.localize(today_start_local.replace(tzinfo=None)).astimezone(pytz.utc)

        result = await self._session.execute(
            select(func.count())
            .select_from(NotificationLog)
            .where(NotificationLog.household_member_id == member_id)
            .where(NotificationLog.sent_at >= today_start_utc)
            .where(NotificationLog.batched_into_digest.is_(False))
        )
        return result.scalar() or 0

    @staticmethod
    def _now_local(timezone_name: str) -> datetime:
        import pytz
        tz = pytz.timezone(timezone_name)
        return datetime.now(tz).replace(tzinfo=None)

    @staticmethod
    def _in_quiet_hours(current: time, start: time, end: time) -> bool:
        """Check if current time falls in quiet window, handling midnight-spanning windows."""
        if start <= end:
            return start <= current < end
        # Window spans midnight (e.g. 21:00 – 07:00)
        return current >= start or current < end


async def log_notification(
    session: AsyncSession,
    member_id: UUID,
    household_id: UUID,
    template_key: str,
    priority: str,
    held_quiet: bool = False,
    breakthrough: bool = False,
    batched: bool = False,
    twilio_sid: Optional[str] = None,
) -> None:
    """Write a notification_log row. Always called whether sent or held."""
    log = NotificationLog(
        household_member_id=member_id,
        household_id=household_id,
        template_key=template_key,
        priority=priority,
        sent_at=datetime.now(timezone.utc),
        held_for_quiet_hours=held_quiet,
        broke_through_quiet_hours=breakthrough,
        batched_into_digest=batched,
        twilio_message_sid=twilio_sid,
        delivery_status="sent" if not held_quiet else "queued",
    )
    session.add(log)
