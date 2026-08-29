"""Notification module public API — outbound WhatsApp messaging.

Implements contracts.notification.NotificationAPI.
This module is a pure sink — subscribes to events, publishes almost none.
That asymmetry is healthy and makes it the second-easiest module to extract.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client as TwilioClient

from app.contracts.notification import NotificationAPI, NotificationRequest
from app.modules.notification.models import NotificationLog, NotificationPreference
from app.platform.config import get_settings
from app.platform.observability import get_logger, log_cost_event

logger = get_logger(__name__)
_settings = get_settings()

# Template catalogue — keys map to WhatsApp approved templates (§17)
TEMPLATES = {
    "item_confirmation": "Got it — {title} on {date}. ✅ Looks good | ✏️ Edit",
    "approval_request": "Approval needed: {title} — ${amount}. ✅ Approve | ❌ Decline",
    "delegation_request": "{delegated_by} is asking you to cover: {title}. ✅ I've got it | ❌ Can't do it",
    "delegation_accepted": "{name} accepted — they've got {title}.",
    "delegation_declined": "{name} can't cover {title}. It's back with you.",
    "reminder_leave_by": "Traffic looks like {eta_minutes} min to {destination} — leave by {leave_by} for {title}.",
    "reminder_task": "Reminder: {title} at {time}. ✅ Done",
    "conflict_detected": "Heads up — you have a conflict with {title} (overlaps {event}). Asking {candidate} to cover.",
    "daily_briefing": "{content}",
    "onboarding_invite": "You've been invited to join {household_name} on KinValet. Click: {url}",
    "quiet_day": "All quiet today ✅ — nothing needs your attention.",
}


class NotificationService:
    """Satisfies contracts.notification.NotificationAPI."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._twilio: Optional[TwilioClient] = None

    def _get_twilio(self) -> TwilioClient:
        if self._twilio is None:
            if _settings.twilio_account_sid and _settings.twilio_auth_token:
                self._twilio = TwilioClient(
                    _settings.twilio_account_sid,
                    _settings.twilio_auth_token,
                )
        return self._twilio

    async def send(self, request: NotificationRequest) -> str:
        """Send a notification via the appropriate channel."""
        # TODO: check quiet hours, daily ceiling, opt-out before sending
        # For MVP, we send directly
        log = NotificationLog(
            household_member_id=request.member_id,
            household_id=request.household_id,
            template_key=request.template_key,
            priority=request.priority,
            sent_at=datetime.now(timezone.utc),
        )
        self._session.add(log)
        await self._session.flush()
        return str(log.id)

    async def send_whatsapp(
        self,
        phone_e164: str,
        message: str,
        priority: str = "normal",
    ) -> bool:
        """Send a WhatsApp message via Twilio."""
        client = self._get_twilio()
        if client is None:
            logger.warning("twilio_not_configured", message_preview=message[:50])
            return False

        try:
            msg = client.messages.create(
                from_=_settings.twilio_whatsapp_number,
                to=f"whatsapp:{phone_e164}",
                body=message,
            )
            logger.info(
                "whatsapp_sent",
                to=phone_e164[-4:],
                sid=msg.sid,
                priority=priority,
            )
            # Log cost
            log_cost_event(
                household_id="unknown",  # TODO: pass household_id
                event_type="whatsapp_conversation",
                amount_cents=2,  # ~$0.02 per conversation window
            )
            return True
        except Exception as e:
            logger.error("twilio_send_failed", error=str(e), to=phone_e164[-4:])
            return False

    async def send_from_template(
        self,
        phone_e164: str,
        template_key: str,
        variables: dict,
        priority: str = "normal",
    ) -> bool:
        """Send a templated WhatsApp message."""
        template = TEMPLATES.get(template_key, "")
        if not template:
            logger.error("unknown_template", key=template_key)
            return False

        try:
            message = template.format(**variables)
        except KeyError as e:
            logger.error("template_format_error", key=template_key, missing=str(e))
            return False

        return await self.send_whatsapp(phone_e164, message, priority)
