"""Identity module FastAPI routes.

Public routes: /api/v1/identity/...
Auth: most routes require a valid JWT; registration routes are unauthenticated.
"""
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import get_db_session
from app.platform.errors import DuplicateError, ValidationError, AuthorizationError
from app.platform.observability import get_logger
from app.modules.identity.api import IdentityService
from app.modules.identity.repository import (
    AuthSessionRepository,
    HouseholdRepository,
    InviteRepository,
    MemberRepository,
    PhoneRouteRepository,
)
from app.modules.identity.services.registration import RegistrationService

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])
logger = get_logger(__name__)


# ── Request / Response schemas ──────────────────────────────────────────────────

class RegisterPrimaryRequest(BaseModel):
    household_name: str
    timezone: str = "America/New_York"
    display_name: str
    phone_e164: str
    email: EmailStr
    supabase_access_token: Optional[str] = None  # JWT from Supabase auth


class MemberResponse(BaseModel):
    id: uuid.UUID
    household_id: uuid.UUID
    display_name: str
    role: str
    status: str


class HouseholdResponse(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    status: str
    forwarding_email: Optional[str] = None


class RegisterPrimaryResponse(BaseModel):
    household: HouseholdResponse
    member: MemberResponse


class InviteMemberRequest(BaseModel):
    display_name: str
    intended_role: str  # co_parent | secondary_readonly
    phone_e164: Optional[str] = None
    email: Optional[EmailStr] = None

    @field_validator("intended_role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"co_parent", "secondary_readonly"}
        if v not in allowed:
            raise ValueError(f"intended_role must be one of {allowed}")
        return v


class AcceptInviteRequest(BaseModel):
    token: str
    display_name: str
    phone_e164: str
    supabase_access_token: Optional[str] = None


class PhoneChangeRequest(BaseModel):
    new_phone_e164: str
    otp_code: str  # Verified OTP for the new number


class TokenValidationResponse(BaseModel):
    valid: bool
    member_id: Optional[uuid.UUID] = None
    household_id: Optional[uuid.UUID] = None
    role: Optional[str] = None


# ── Dependency: current member from JWT ────────────────────────────────────────

async def get_current_member(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Authenticate the request — supports JWT (production) and header-based (MVP).

    Priority:
    1. Authorization: Bearer <JWT> — Supabase JWT, validated against JWT secret
    2. x-household-id + x-member-id headers — MVP localStorage-based session

    Production: remove the header-based fallback and require JWT only.
    """
    identity_svc = IdentityService(session)

    # Try JWT first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ")
        member = await identity_svc.validate_token(token)
        if member is not None:
            return member

    # MVP fallback: x-household-id header (from localStorage session)
    household_id = request.headers.get("x-household-id", "")
    member_id = request.headers.get("x-member-id", "")

    if household_id and member_id:
        member = await identity_svc.get_member(uuid.UUID(member_id))
        if member is not None and str(member.household_id) == household_id:
            from app.platform.context import set_request_context
            set_request_context(
                household_id=household_id,
                member_id=member_id,
                actor_type="household_member",
            )
            return member

    # Also try household_id alone (find primary admin)
    if household_id and not member_id:
        from sqlalchemy import select as sa_select
        from app.modules.identity.models import HouseholdMember
        result = await session.execute(
            sa_select(HouseholdMember)
            .where(HouseholdMember.household_id == uuid.UUID(household_id))
            .where(HouseholdMember.role == "primary_admin")
            .where(HouseholdMember.status == "active")
        )
        db_member = result.scalar_one_or_none()
        if db_member is not None:
            from app.platform.context import set_request_context
            set_request_context(
                household_id=household_id,
                member_id=str(db_member.id),
                actor_type="household_member",
            )
            return IdentityService._to_member_dto(db_member)

    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/register", response_model=RegisterPrimaryResponse, status_code=201)
async def register_primary_user(
    body: RegisterPrimaryRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """Register a new household and primary admin (§10.4)."""
    svc = RegistrationService(session)
    try:
        household, member = await svc.register_primary(
            household_name=body.household_name,
            timezone=body.timezone,
            display_name=body.display_name,
            phone_e164=body.phone_e164,
            email=body.email,
        )

        # Auto-generate the family's unique forwarding email
        from app.modules.connectors.email_inbound import generate_member_address
        try:
            addr = await generate_member_address(
                session, household.id, member.id,
                body.household_name, body.display_name,
            )
            forwarding_email = addr.address
        except Exception:
            forwarding_email = None

        await session.commit()
        return RegisterPrimaryResponse(
            household=HouseholdResponse(
                id=household.id,
                name=household.name,
                timezone=household.timezone,
                status=household.status,
                forwarding_email=forwarding_email,
            ),
            member=MemberResponse(
                id=member.id,
                household_id=member.household_id,
                display_name=member.display_name,
                role=member.role,
                status=member.status,
            ),
        )
    except DuplicateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/invite", status_code=201)
async def invite_member(
    body: InviteMemberRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Primary admin invites a new household member (§10.5)."""
    if current_member.role != "primary_admin":
        raise HTTPException(status_code=403, detail="Only the primary admin can invite members")

    if not body.phone_e164 and not body.email:
        raise HTTPException(
            status_code=422,
            detail="At least one of phone_e164 or email is required"
        )

    # Prevent inviting dependents (AC 3.1)
    if body.intended_role in ("dependent_minor", "dependent_care_recipient"):
        raise HTTPException(
            status_code=422,
            detail="Dependents cannot be invited — they are represented as member records only."
        )

    token = secrets.token_urlsafe(32)
    invites = InviteRepository(session)
    invite = await invites.create(
        household_id=current_member.household_id,
        invited_by_member_id=current_member.id,
        intended_role=body.intended_role,
        token=token,
        invited_phone=body.phone_e164,
        invited_email=str(body.email) if body.email else None,
    )
    await session.commit()

    # In production: send the invite link via SMS / email
    # invite_url = f"{settings.twilio_webhook_base_url}/invite/{token}"
    return {"invite_id": str(invite.id), "expires_at": invite.expires_at.isoformat()}


@router.post("/invite/accept", response_model=MemberResponse, status_code=201)
async def accept_invite(
    body: AcceptInviteRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """Invitee accepts a household invitation (§10.5)."""
    svc = RegistrationService(session)
    try:
        member = await svc.accept_invite(
            invite_token=body.token,
            display_name=body.display_name,
            phone_e164=body.phone_e164,
        )
        await session.commit()
        return MemberResponse(
            id=member.id,
            household_id=member.household_id,
            display_name=member.display_name,
            role=member.role,
            status=member.status,
        )
    except (ValidationError, DuplicateError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/me", response_model=MemberResponse)
async def get_current_member_info(current_member=Depends(get_current_member)):
    """Return the authenticated member's profile."""
    return MemberResponse(
        id=current_member.id,
        household_id=current_member.household_id,
        display_name=current_member.display_name,
        role=current_member.role,
        status=current_member.status,
    )


@router.get("/household/{household_id}/members")
async def list_household_members(
    household_id: uuid.UUID,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List all active members in the household."""
    if current_member.household_id != household_id:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.platform.context import set_request_context
    set_request_context(
        household_id=str(household_id),
        member_id=str(current_member.id),
        actor_type="household_member",
    )

    members = MemberRepository(session)
    all_members = await members.get_by_household(household_id)
    return [
        {
            "id": str(m.id),
            "display_name": m.display_name,
            "role": m.role,
            "status": m.status,
            "channel_identity_status": m.channel_identity_status,
        }
        for m in all_members
    ]


class LookupRequest(BaseModel):
    phone_e164: str


class LookupResponse(BaseModel):
    found: bool
    household_id: Optional[uuid.UUID] = None
    household_name: Optional[str] = None
    member_id: Optional[uuid.UUID] = None
    display_name: Optional[str] = None
    role: Optional[str] = None


@router.post("/lookup", response_model=LookupResponse)
async def lookup_by_phone(
    body: LookupRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """Look up a member by phone number — used for session recovery.

    MVP: returns the member's household info so the frontend can restore
    the session. Production: this would require OTP verification first.
    """
    identity_svc = IdentityService(session)
    member = await identity_svc.resolve_phone(body.phone_e164)
    if member is None:
        return LookupResponse(found=False)

    household = await identity_svc.get_household(member.household_id)
    return LookupResponse(
        found=True,
        household_id=member.household_id,
        household_name=household.name if household else None,
        member_id=member.id,
        display_name=member.display_name,
        role=member.role,
    )


@router.post("/validate-token", response_model=TokenValidationResponse)
async def validate_token(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Validate a JWT token — used by internal services."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return TokenValidationResponse(valid=False)

    token = auth_header.removeprefix("Bearer ")
    identity_svc = IdentityService(session)
    member = await identity_svc.validate_token(token)
    if member is None:
        return TokenValidationResponse(valid=False)

    return TokenValidationResponse(
        valid=True,
        member_id=member.id,
        household_id=member.household_id,
        role=member.role,
    )
