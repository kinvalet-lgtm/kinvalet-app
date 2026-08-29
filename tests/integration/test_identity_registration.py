"""Integration tests for the identity registration flow (§10.4).

These tests use a real Postgres database.
Requires: TEST_DATABASE_URL or the local docker-compose stack running.
"""
import uuid

import pytest
import pytest_asyncio

from app.platform.context import set_request_context
from app.platform.errors import DuplicateError


@pytest.mark.asyncio
@pytest.mark.integration
class TestPrimaryRegistration:
    async def test_register_primary_creates_household_member_and_route(self, db_session):
        """§10.4: atomic creation of household, member, and phone route."""
        from app.modules.identity.services.registration import RegistrationService
        from app.modules.identity.repository import PhoneRouteRepository

        phone = f"+1555{uuid.uuid4().hex[:7]}"

        svc = RegistrationService(db_session)
        set_request_context(household_id=None, actor_type="system_agent")

        household, member = await svc.register_primary(
            household_name="The Miller Family",
            timezone="America/New_York",
            display_name="Sarah",
            phone_e164=phone,
            email="sarah@example.com",
        )

        # Household created
        assert household.id is not None
        assert household.name == "The Miller Family"
        assert household.timezone == "America/New_York"

        # Member created as primary_admin with active status
        assert member.id is not None
        assert member.role == "primary_admin"
        assert member.status == "active"
        assert member.household_id == household.id

        # Phone route created and active
        phone_repo = PhoneRouteRepository(db_session)
        route = await phone_repo.resolve(phone)
        assert route is not None
        assert route.is_active is True
        assert route.household_member_id == member.id

    async def test_duplicate_phone_raises_error(self, db_session):
        """A phone number can only be active on one household at a time."""
        from app.modules.identity.services.registration import RegistrationService
        phone = f"+1555{uuid.uuid4().hex[:7]}"

        svc = RegistrationService(db_session)
        set_request_context(household_id=None, actor_type="system_agent")

        # First registration succeeds
        await svc.register_primary(
            household_name="Family A",
            timezone="America/Chicago",
            display_name="Alice",
            phone_e164=phone,
            email="alice@example.com",
        )

        # Second registration with same phone raises DuplicateError
        with pytest.raises(DuplicateError):
            await svc.register_primary(
                household_name="Family B",
                timezone="America/Los_Angeles",
                display_name="Bob",
                phone_e164=phone,
                email="bob@example.com",
            )
