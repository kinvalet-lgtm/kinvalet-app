"""Registration service — household creation and member onboarding.

All mutations in one transaction per §10.4 step 4:
'Atomically, in one transaction: create the auth identity, household,
household_member(role=primary_admin, status=active), and phone_channel_route(is_active=true).
A partial identity must never exist.'
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.context import set_request_context
from app.platform.errors import DuplicateError, ValidationError
from app.platform.observability import get_logger, log_audit_event
from app.modules.identity.repository import (
    HouseholdRepository,
    MemberRepository,
    PhoneRouteRepository,
    InviteRepository,
)
from app.modules.identity.models import HouseholdMember, Household

logger = get_logger(__name__)


class RegistrationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._households = HouseholdRepository(session)
        self._members = MemberRepository(session)
        self._phone_routes = PhoneRouteRepository(session)
        self._invites = InviteRepository(session)

    async def register_primary(
        self,
        household_name: str,
        timezone: str,
        display_name: str,
        phone_e164: str,
        email: str,
        auth_user_id: Optional[uuid.UUID] = None,
    ) -> tuple[Household, HouseholdMember]:
        """Register a new household and primary admin.

        Atomic: all-or-nothing. Raises DuplicateError if phone already active.
        """
        # Check phone not already in use (checked here before household creation
        # so no orphaned partial records are produced)
        existing_route = await self._phone_routes.resolve(phone_e164)
        if existing_route:
            raise DuplicateError(
                f"Phone {phone_e164} is already registered to another household. "
                "Contact support if you believe this is an error."
            )

        # Create household
        household = await self._households.create(
            name=household_name,
            timezone=timezone,
        )

        # Set context immediately after household creation (required by tenancy guard)
        set_request_context(
            household_id=str(household.id),
            actor_type="system_agent",  # system action, no member yet
        )

        # Create primary admin member
        member = await self._members.create(
            household_id=household.id,
            display_name=display_name,
            role="primary_admin",
            phone_e164=phone_e164,
            email=email,
        )

        # Activate member immediately (phone already verified by OTP)
        await self._members.activate(member_id=member.id, auth_user_id=auth_user_id)

        # Set primary admin on household
        await self._households.set_primary_admin(household.id, member.id)

        # Create phone route — must be last so member exists
        await self._phone_routes.create(
            phone_e164=phone_e164,
            household_id=household.id,
            member_id=member.id,
        )

        # All three writes succeed together or none do (SQLAlchemy session = transaction)
        await self._session.flush()

        # Update context with the member ID now that we have it
        set_request_context(
            household_id=str(household.id),
            member_id=str(member.id),
            actor_type="household_member",
        )

        log_audit_event(
            action="household.created",
            actor_type="household_member",
            actor_id=str(member.id),
            household_id=str(household.id),
            target_type="household",
            target_id=str(household.id),
        )

        logger.info(
            "primary_registered",
            household_id=str(household.id),
            member_id=str(member.id),
        )
        return household, member

    async def accept_invite(
        self,
        invite_token: str,
        display_name: str,
        phone_e164: str,
        auth_user_id: Optional[uuid.UUID] = None,
    ) -> HouseholdMember:
        """Secondary user accepts an invite and completes registration.

        Per §10.5: the role is fixed at invite time; the invitee cannot change it.
        """
        invite = await self._invites.get_by_token(invite_token)
        if invite is None:
            raise ValidationError("Invitation not found or already used.")

        if invite.status != "pending":
            raise ValidationError(
                f"This invitation has already been {invite.status}. "
                "Ask the household admin to send a new one."
            )

        if invite.expires_at < datetime.now(timezone.utc):
            await self._invites.mark_expired(invite.id)
            raise ValidationError(
                "This invitation has expired — ask the household admin to send a new one."
            )

        # Verify phone matches if the invite was SMS-based
        if invite.invited_phone_e164 and invite.invited_phone_e164 != phone_e164:
            raise ValidationError(
                "The verified phone number does not match the invite. "
                "Please use the phone number the invite was sent to."
            )

        # Check phone not already in use
        existing_route = await self._phone_routes.resolve(phone_e164)
        if existing_route:
            raise DuplicateError(
                f"Phone {phone_e164} is already registered to another household."
            )

        member = await self._members.create(
            household_id=invite.household_id,
            display_name=display_name,
            role=invite.intended_role,
            phone_e164=phone_e164,
            invited_by=invite.invited_by_member_id,
        )
        await self._members.activate(member.id, auth_user_id)

        await self._phone_routes.create(
            phone_e164=phone_e164,
            household_id=invite.household_id,
            member_id=member.id,
        )

        await self._invites.mark_accepted(invite.id)
        await self._session.flush()

        log_audit_event(
            action="member.joined",
            actor_type="household_member",
            actor_id=str(member.id),
            household_id=str(invite.household_id),
            target_type="household_member",
            target_id=str(member.id),
            metadata={"role": invite.intended_role},
        )

        return member
