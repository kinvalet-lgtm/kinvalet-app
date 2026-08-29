"""Identity repository — all DB access, household-scoped.

Every public method that queries household-specific data calls assert_household_scope().
Methods that query global data (phone lookup, token validation) are explicitly exempt
and documented as such.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.tenancy import assert_household_scope
from app.platform.errors import NotFoundError, DuplicateError
from app.modules.identity.models import (
    AuthSession,
    Household,
    HouseholdMember,
    MemberInvite,
    PhoneChannelRoute,
)


class HouseholdRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        name: str,
        timezone: str,
        plan_tier: str = "pilot",
        home_address_text: Optional[str] = None,
    ) -> Household:
        household = Household(
            name=name,
            timezone=timezone,
            plan_tier=plan_tier,
            home_address_text=home_address_text,
            status="active",
        )
        self._session.add(household)
        await self._session.flush()
        return household

    async def get(self, household_id: uuid.UUID) -> Optional[Household]:
        result = await self._session.execute(
            select(Household).where(Household.id == household_id)
        )
        return result.scalar_one_or_none()

    async def update_status(self, household_id: uuid.UUID, status: str) -> None:
        assert_household_scope(household_id)
        await self._session.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )

    async def set_primary_admin(self, household_id: uuid.UUID, member_id: uuid.UUID) -> None:
        assert_household_scope(household_id)
        await self._session.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(primary_admin_id=member_id)
        )


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        household_id: uuid.UUID,
        display_name: str,
        role: str,
        phone_e164: Optional[str] = None,
        email: Optional[str] = None,
        invited_by: Optional[uuid.UUID] = None,
    ) -> HouseholdMember:
        member = HouseholdMember(
            household_id=household_id,
            display_name=display_name,
            role=role,
            phone_e164=phone_e164,
            email=email,
            invited_by=invited_by,
            status="pending_verification" if phone_e164 else "invited",
            channel_identity_status="pending" if phone_e164 else "unavailable_no_sms",
        )
        self._session.add(member)
        await self._session.flush()
        return member

    async def get(self, member_id: uuid.UUID) -> Optional[HouseholdMember]:
        result = await self._session.execute(
            select(HouseholdMember).where(HouseholdMember.id == member_id)
        )
        return result.scalar_one_or_none()

    async def get_by_household(
        self, household_id: uuid.UUID, include_removed: bool = False
    ) -> list[HouseholdMember]:
        assert_household_scope(household_id)
        stmt = select(HouseholdMember).where(HouseholdMember.household_id == household_id)
        if not include_removed:
            stmt = stmt.where(HouseholdMember.status != "removed")
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_adults(self, household_id: uuid.UUID) -> list[HouseholdMember]:
        assert_household_scope(household_id)
        adult_roles = ["primary_admin", "co_parent", "secondary_readonly"]
        result = await self._session.execute(
            select(HouseholdMember)
            .where(HouseholdMember.household_id == household_id)
            .where(HouseholdMember.role.in_(adult_roles))
            .where(HouseholdMember.status == "active")
        )
        return list(result.scalars().all())

    async def activate(
        self,
        member_id: uuid.UUID,
        auth_user_id: Optional[uuid.UUID] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        values: dict = {
            "status": "active",
            "verified_at": now,
            "phone_verified_at": now,
            "channel_identity_status": "active",
            "updated_at": now,
        }
        if auth_user_id:
            values["auth_user_id"] = auth_user_id
        await self._session.execute(
            update(HouseholdMember)
            .where(HouseholdMember.id == member_id)
            .values(**values)
        )

    async def suspend(self, member_id: uuid.UUID) -> None:
        await self._session.execute(
            update(HouseholdMember)
            .where(HouseholdMember.id == member_id)
            .values(status="suspended", updated_at=datetime.now(timezone.utc))
        )

    async def remove(self, member_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self._session.execute(
            update(HouseholdMember)
            .where(HouseholdMember.id == member_id)
            .values(status="removed", removed_at=now, updated_at=now)
        )

    async def update_phone(self, member_id: uuid.UUID, new_phone: str) -> None:
        await self._session.execute(
            update(HouseholdMember)
            .where(HouseholdMember.id == member_id)
            .values(
                phone_e164=new_phone,
                phone_verified_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )


class PhoneRouteRepository:
    """Manages the phone→household routing table.

    Critical invariant: at most one is_active=True row per phone_e164.
    Phone changes are atomic swaps — no window with two active or zero.
    """
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, phone_e164: str) -> Optional[PhoneChannelRoute]:
        """Lookup for the webhook hot path — not household-scoped (global by design)."""
        result = await self._session.execute(
            select(PhoneChannelRoute)
            .where(PhoneChannelRoute.phone_e164 == phone_e164)
            .where(PhoneChannelRoute.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        phone_e164: str,
        household_id: uuid.UUID,
        member_id: uuid.UUID,
    ) -> PhoneChannelRoute:
        # Check for existing active route
        existing = await self.resolve(phone_e164)
        if existing:
            raise DuplicateError(
                f"Phone {phone_e164} is already active on household {existing.household_id}. "
                "A phone number may only be active on one household at a time."
            )
        route = PhoneChannelRoute(
            phone_e164=phone_e164,
            household_id=household_id,
            household_member_id=member_id,
            is_active=True,
        )
        self._session.add(route)
        await self._session.flush()
        return route

    async def deactivate(self, phone_e164: str) -> None:
        """Deactivate a phone route (on member remove/suspend or phone change)."""
        await self._session.execute(
            update(PhoneChannelRoute)
            .where(PhoneChannelRoute.phone_e164 == phone_e164)
            .where(PhoneChannelRoute.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )

    async def atomic_swap(
        self,
        old_phone: str,
        new_phone: str,
        household_id: uuid.UUID,
        member_id: uuid.UUID,
    ) -> PhoneChannelRoute:
        """Atomic phone number change — deactivate old, create new in same transaction.

        There is never a window with two active numbers or zero active numbers.
        """
        await self.deactivate(old_phone)
        return await self.create(new_phone, household_id, member_id)


class InviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        household_id: uuid.UUID,
        invited_by_member_id: uuid.UUID,
        intended_role: str,
        token: str,
        invited_phone: Optional[str] = None,
        invited_email: Optional[str] = None,
    ) -> MemberInvite:
        if not invited_phone and not invited_email:
            raise ValueError("At least one of invited_phone or invited_email is required.")

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        invite = MemberInvite(
            household_id=household_id,
            invited_by_member_id=invited_by_member_id,
            intended_role=intended_role,
            invited_phone_e164=invited_phone,
            invited_email=invited_email,
            token_hash=token_hash,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        self._session.add(invite)
        await self._session.flush()
        return invite

    async def get_by_token(self, token: str) -> Optional[MemberInvite]:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        result = await self._session.execute(
            select(MemberInvite).where(MemberInvite.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def mark_accepted(self, invite_id: uuid.UUID) -> None:
        await self._session.execute(
            update(MemberInvite)
            .where(MemberInvite.id == invite_id)
            .values(status="accepted")
        )

    async def mark_expired(self, invite_id: uuid.UUID) -> None:
        await self._session.execute(
            update(MemberInvite)
            .where(MemberInvite.id == invite_id)
            .values(status="expired")
        )


class AuthSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        member_id: uuid.UUID,
        device_label: str,
        ip_address: Optional[str] = None,
    ) -> AuthSession:
        session = AuthSession(
            household_member_id=member_id,
            device_label=device_label,
            ip_created_from=ip_address,
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def list_active(self, member_id: uuid.UUID) -> list[AuthSession]:
        result = await self._session.execute(
            select(AuthSession)
            .where(AuthSession.household_member_id == member_id)
            .where(AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.last_active_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(self, session_id: uuid.UUID) -> None:
        await self._session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(revoked_at=datetime.now(timezone.utc))
        )

    async def revoke_all(self, member_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self._session.execute(
            update(AuthSession)
            .where(AuthSession.household_member_id == member_id)
            .where(AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
