"""Event payload schemas — the shared vocabulary for the event bus.

These are the only event types that cross module boundaries.
Modules that publish events import from here; modules that subscribe also import from here.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BaseEvent(BaseModel):
    event_id: uuid.UUID = uuid.uuid4()
    occurred_at: datetime = datetime.utcnow()


# ── Identity events ────────────────────────────────────────────────────────────

class MemberJoined(BaseEvent):
    household_id: uuid.UUID
    member_id: uuid.UUID
    role: str

class MemberRemoved(BaseEvent):
    household_id: uuid.UUID
    member_id: uuid.UUID

class PhoneChanged(BaseEvent):
    household_id: uuid.UUID
    member_id: uuid.UUID
    old_phone: str
    new_phone: str


# ── Inbound events ─────────────────────────────────────────────────────────────

class MessageReceived(BaseEvent):
    inbound_message_id: uuid.UUID
    household_id: uuid.UUID
    member_id: uuid.UUID
    source: str  # whatsapp | email
    media_type: str  # text | voice | image | pdf | multi


# ── Extraction events ──────────────────────────────────────────────────────────

class ExtractionCompleted(BaseEvent):
    extraction_result_id: uuid.UUID
    inbound_message_id: uuid.UUID
    household_id: uuid.UUID
    confidence_score: float
    requires_human_review: bool

class ClarificationNeeded(BaseEvent):
    inbound_message_id: uuid.UUID
    household_id: uuid.UUID
    member_id: uuid.UUID
    question_text: str


# ── Operations events ──────────────────────────────────────────────────────────

class ItemCreated(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    category: str
    assigned_to_member_id: Optional[uuid.UUID] = None
    start_at: Optional[datetime] = None
    location: Optional[str] = None

class ItemConfirmed(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID

class ItemUpdated(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    changed_fields: list[str]

class ItemCompleted(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID

class ItemCancelled(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID

class ApprovalRequested(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    requested_of_member_id: uuid.UUID
    amount_cents: Optional[int] = None

class DelegationProposed(BaseEvent):
    delegation_id: uuid.UUID
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    delegated_by_member_id: uuid.UUID
    delegated_to_member_id: uuid.UUID
    item_title: str
    trigger_reason: str  # manual | conflict_detected

class DelegationAccepted(BaseEvent):
    delegation_id: uuid.UUID
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    delegated_to_member_id: uuid.UUID

class DelegationDeclined(BaseEvent):
    delegation_id: uuid.UUID
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    delegated_to_member_id: uuid.UUID

class ConflictDetected(BaseEvent):
    calendar_conflict_id: uuid.UUID
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    busy_member_id: uuid.UUID
    resolution: str  # auto_delegation_proposed | manual_pick_required | unresolved

class ConflictUnresolved(BaseEvent):
    operational_item_id: uuid.UUID
    household_id: uuid.UUID


# ── Skills events ──────────────────────────────────────────────────────────────

class SkillScheduled(BaseEvent):
    skill_execution_id: uuid.UUID
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    skill_type_id: str
    scheduled_for: datetime

class SkillFired(BaseEvent):
    skill_execution_id: uuid.UUID
    operational_item_id: uuid.UUID
    household_id: uuid.UUID
    skill_type_id: str


# ── Briefing events ────────────────────────────────────────────────────────────

class BriefingReady(BaseEvent):
    briefing_id: uuid.UUID
    household_id: uuid.UUID
    briefing_date: str  # ISO date


# ── Connector events ───────────────────────────────────────────────────────────

class ConnectorConnected(BaseEvent):
    household_id: uuid.UUID
    connector_instance_id: uuid.UUID
    connector_type_id: str
    connected_by_member_id: uuid.UUID

class ConnectorRevoked(BaseEvent):
    household_id: uuid.UUID
    connector_instance_id: uuid.UUID
    connector_type_id: str
    reason: str  # disconnected_by_user | revoked_by_admin | error_reauth_required
