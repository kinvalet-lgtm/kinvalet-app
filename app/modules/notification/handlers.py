"""Notification event handlers — subscribe to domain events and send notifications.

This module is a pure sink for events. It subscribes to nearly everything,
publishes almost none. That asymmetry is healthy and makes it the
second-easiest module to extract (§12.3 of architecture doc).
"""
from app.contracts.events import (
    ApprovalRequested,
    BriefingReady,
    ConflictDetected,
    DelegationAccepted,
    DelegationDeclined,
    DelegationProposed,
    SkillFired,
)
from app.platform.events.registry import subscribe
from app.platform.observability import get_logger

logger = get_logger(__name__)


@subscribe(DelegationProposed)
async def on_delegation_proposed(payload: dict) -> None:
    """Send delegation request WhatsApp to the proposed delegate."""
    logger.info("notification_delegation_proposed",
                item_id=payload.get("operational_item_id"),
                to=payload.get("delegated_to_member_id"))
    # TODO: resolve delegate's phone → NotificationService.send_from_template(...)


@subscribe(DelegationAccepted)
async def on_delegation_accepted(payload: dict) -> None:
    """Notify the delegator that their task was accepted."""
    logger.info("notification_delegation_accepted",
                delegation_id=payload.get("delegation_id"))


@subscribe(DelegationDeclined)
async def on_delegation_declined(payload: dict) -> None:
    """Notify the delegator that the delegate can't do it."""
    logger.info("notification_delegation_declined",
                delegation_id=payload.get("delegation_id"))


@subscribe(ApprovalRequested)
async def on_approval_requested(payload: dict) -> None:
    """Send approval request WhatsApp with cost context."""
    logger.info("notification_approval_requested",
                item_id=payload.get("operational_item_id"),
                amount=payload.get("amount_cents"))


@subscribe(ConflictDetected)
async def on_conflict_detected(payload: dict) -> None:
    """Notify the busy member of the conflict and proposed resolution."""
    logger.info("notification_conflict_detected",
                item_id=payload.get("operational_item_id"),
                resolution=payload.get("resolution"))


@subscribe(BriefingReady)
async def on_briefing_ready(payload: dict) -> None:
    """Send the daily briefing via WhatsApp."""
    logger.info("notification_briefing_ready",
                household_id=payload.get("household_id"),
                date=payload.get("briefing_date"))


@subscribe(SkillFired)
async def on_skill_fired(payload: dict) -> None:
    """Send reminder or ETA notification when a skill fires."""
    logger.info("notification_skill_fired",
                skill_type=payload.get("skill_type_id"),
                item_id=payload.get("operational_item_id"))
