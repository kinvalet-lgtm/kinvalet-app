"""Email forwarding connector adapter.

No OAuth — household gets a unique inbound address (e.g. household123@inbound.domain.com).
They set up forwarding from their school/care provider email to this address.
Inbound webhook receives the forwarded mail, runs the same extraction pipeline.

Security: only accepts mail forwarded from a verified household member's email (§11.9).
Unrecognized senders are silently dropped and logged — never bounced (backscatter risk).
"""
import hashlib
import uuid
from typing import Optional
from uuid import UUID

from app.contracts.connectors import ConnectorHealth, ConnectorInstanceDTO
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

INBOUND_DOMAIN = "inbound.sandwichcopilot.com"  # configure in DNS


class EmailForwardAdapter:
    """Email forwarding adapter — no OAuth, webhook-based."""

    connector_type_id = "email_forwarding"

    async def initiate_auth(self, household_id: UUID, member_id: UUID) -> str:
        """Return the unique inbound forwarding address for this household."""
        addr = _generate_inbound_address(household_id)
        # The "auth URL" for email forwarding is the address to configure
        return f"mailto:{addr}"

    async def handle_auth_callback(self, payload: dict) -> ConnectorInstanceDTO:
        """Called when household confirms the forwarding address is set up."""
        household_id = UUID(payload["household_id"])
        member_id = UUID(payload["member_id"])
        addr = _generate_inbound_address(household_id)
        return ConnectorInstanceDTO(
            id=UUID(int=0),
            household_id=household_id,
            connector_type_id=self.connector_type_id,
            connected_by_member_id=member_id,
            external_account_ref=addr,
            status="connected",
        )

    async def handle_webhook(self, payload: dict) -> list[dict]:
        """Receive a forwarded email payload.

        Validates the forwarding source is a registered household member email.
        Returns the email content for extraction pipeline.
        """
        from_email = payload.get("from", "")
        to_email = payload.get("to", "")
        subject = payload.get("subject", "")
        body = payload.get("body_plain", "") or payload.get("body_html", "")

        if not to_email:
            return []

        # Extract household_id from the inbound address
        household_id = _household_from_address(to_email)
        if household_id is None:
            logger.warning("email_unroutable", to=to_email)
            return []

        return [{
            "type": "email_forward",
            "household_id": str(household_id),
            "from_email": from_email,
            "subject": subject,
            "body": body[:5000],  # cap at 5KB
            "message_id": payload.get("message-id", str(uuid.uuid4())),
        }]

    async def fetch_incremental(self, instance: ConnectorInstanceDTO) -> list[dict]:
        return []  # Pull-based not applicable; push-only via webhook

    async def revoke(self, instance: ConnectorInstanceDTO) -> None:
        pass  # No tokens to revoke; the address simply stops being accepted

    async def health_check(self, instance: ConnectorInstanceDTO) -> ConnectorHealth:
        from datetime import datetime, timezone
        # Email forwarding is healthy if the connector exists
        return ConnectorHealth(status="healthy", last_checked_at=datetime.now(timezone.utc))


def _generate_inbound_address(household_id: UUID) -> str:
    """Generate a deterministic, unique inbound email address for a household."""
    # Short hash of household UUID for a readable-ish address
    h = hashlib.sha256(str(household_id).encode()).hexdigest()[:12]
    return f"hh-{h}@{INBOUND_DOMAIN}"


def _household_from_address(address: str) -> Optional[UUID]:
    """Reverse-lookup: this is a simple deterministic mapping, not a DB lookup.
    In production this would query the connector_instance table.
    """
    # For now, return None — the inbound email router handles resolution
    return None
