"""Unit tests for the tenancy guard.

The tenancy test is load-bearing: every repository method must reject
a missing household scope. This is the test that catches cross-tenant
data access before it reaches production.
"""
import uuid

import pytest

from app.platform.context import set_request_context
from app.platform.errors import HouseholdScopeError
from app.platform.tenancy import assert_household_scope


class TestAssertHouseholdScope:
    def test_raises_when_no_context(self):
        """No context set → must raise."""
        from app.platform.context import _ctx_var
        _ctx_var.set(None)

        with pytest.raises(HouseholdScopeError) as exc_info:
            assert_household_scope(uuid.uuid4())
        assert "household context" in str(exc_info.value).lower()

    def test_raises_when_context_has_no_household(self):
        """Context set but no household_id → must raise."""
        set_request_context(household_id=None, actor_type="system_agent")

        with pytest.raises(HouseholdScopeError):
            assert_household_scope(uuid.uuid4())

    def test_raises_on_household_mismatch(self):
        """household_id in context != query household_id → cross-tenant access attempt."""
        context_id = uuid.uuid4()
        query_id = uuid.uuid4()

        set_request_context(household_id=str(context_id))

        with pytest.raises(HouseholdScopeError) as exc_info:
            assert_household_scope(query_id)
        assert "cross-household" in str(exc_info.value).lower()

    def test_passes_on_matching_household(self):
        """Same household_id → no error, returns the household_id string."""
        hh_id = uuid.uuid4()
        set_request_context(household_id=str(hh_id))

        result = assert_household_scope(hh_id)
        assert result == str(hh_id)

    def test_accepts_string_or_uuid(self):
        """Both string and UUID forms of household_id should work."""
        hh_id = uuid.uuid4()
        set_request_context(household_id=str(hh_id))

        # UUID form
        assert_household_scope(hh_id)
        # String form
        assert_household_scope(str(hh_id))
