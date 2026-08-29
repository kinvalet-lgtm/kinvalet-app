"""Identity contracts — what other modules are allowed to know about members.

Design principle: DTOs expose what the consumer needs, not the ORM's fields.
phone_e164 and auth_user_id are deliberately omitted — other modules have
no business knowing those.
"""
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class MemberDTO(BaseModel):
    """What other modules are allowed to know about a member."""
    id: UUID
    household_id: UUID
    display_name: str
    role: str  # primary_admin | co_parent | secondary_readonly | dependent_minor | dependent_care_recipient
    is_adult: bool  # role not in (dependent_minor, dependent_care_recipient)
    timezone: str   # inherited from household.timezone
    status: str     # active | suspended | removed


class HouseholdDTO(BaseModel):
    id: UUID
    name: str
    timezone: str
    status: str  # active | paused | offboarding | deleted
    home_address_text: Optional[str] = None


class IdentityAPI(Protocol):
    async def get_member(self, member_id: UUID) -> Optional[MemberDTO]: ...
    async def list_adult_members(self, household_id: UUID) -> list[MemberDTO]: ...
    async def resolve_phone(self, phone_e164: str) -> Optional[MemberDTO]: ...
    async def household_timezone(self, household_id: UUID) -> str: ...
    async def get_household(self, household_id: UUID) -> Optional[HouseholdDTO]: ...
    async def validate_token(self, token: str) -> Optional[MemberDTO]: ...
