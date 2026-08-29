"""Integration: household registration flow (§10.4).

Tests the atomic registration: household + member + phone_route all committed
or none are. DB-state verified after each step.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_register_primary_creates_all_records(db_session, unique_phone, unique_email):
    """§10.4: Atomic creation of household, member, and phone route."""
    from app.platform.context import set_request_context
    from app.modules.identity.services.registration import RegistrationService
    from app.modules.identity.repository import PhoneRouteRepository
    from app.modules.identity.models import Household, HouseholdMember, PhoneChannelRoute

    set_request_context(household_id=None, actor_type="system_agent")

    svc = RegistrationService(db_session)
    household, member = await svc.register_primary(
        household_name="Test Family",
        timezone="America/Chicago",
        display_name="Alice",
        phone_e164=unique_phone,
        email=unique_email,
    )
    await db_session.commit()

    # Household created with correct fields
    await db_session.refresh(household)
    hh = household
    assert hh is not None
    assert hh.name == "Test Family"
    assert hh.timezone == "America/Chicago"
    assert hh.status == "active"
    assert hh.plan_tier == "pilot"
    assert hh.primary_admin_id == member.id

    # Member created as primary_admin and active
    mem = await db_session.get(HouseholdMember, member.id)
    assert mem is not None
    assert mem.role == "primary_admin"
    assert mem.status == "active"
    assert mem.channel_identity_status == "active"
    assert mem.phone_e164 == unique_phone
    assert mem.household_id == hh.id

    # Phone route created and active
    routes = PhoneRouteRepository(db_session)
    route = await routes.resolve(unique_phone)
    assert route is not None
    assert route.is_active is True
    assert route.household_member_id == member.id
    assert route.household_id == hh.id


@pytest.mark.asyncio
async def test_duplicate_phone_blocked(db_session, unique_phone, unique_email):
    """A phone already active on one household cannot register on another."""
    from app.platform.context import set_request_context
    from app.modules.identity.services.registration import RegistrationService
    from app.platform.errors import DuplicateError

    set_request_context(household_id=None, actor_type="system_agent")
    svc = RegistrationService(db_session)

    # First registration
    await svc.register_primary(
        household_name="Family A",
        timezone="America/New_York",
        display_name="Alice",
        phone_e164=unique_phone,
        email=unique_email,
    )
    await db_session.commit()

    # Second registration with same phone — must fail
    with pytest.raises(DuplicateError):
        svc2 = RegistrationService(db_session)
        await svc2.register_primary(
            household_name="Family B",
            timezone="America/Los_Angeles",
            display_name="Bob",
            phone_e164=unique_phone,
            email=f"other-{unique_email}",
        )


@pytest.mark.asyncio
async def test_register_api_endpoint(app_client, unique_phone, unique_email):
    """POST /api/v1/identity/register returns 201 with household + member."""
    resp = await app_client.post("/api/v1/identity/register", json={
        "household_name": "API Test Family",
        "timezone": "America/Denver",
        "display_name": "Carol",
        "phone_e164": unique_phone,
        "email": unique_email,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["household"]["name"] == "API Test Family"
    assert data["household"]["status"] == "active"
    assert data["member"]["role"] == "primary_admin"
    assert data["member"]["status"] == "active"
    assert data["household"]["id"] == data["member"]["household_id"]
