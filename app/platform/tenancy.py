"""Tenancy guard — enforces household scoping at the repository layer.

Every repository method must call `assert_household_scope` before executing
any query. This catches missing scope at development time, not in production.
"""
from uuid import UUID

from app.platform.context import get_request_context
from app.platform.errors import HouseholdScopeError


def assert_household_scope(household_id: UUID | str) -> str:
    """Verify the provided household_id matches the request context.

    Raises HouseholdScopeError if:
    - No context is set (raw query without setting context)
    - household_id doesn't match the context (cross-tenant data access attempt)

    Returns the validated household_id as a string.
    """
    ctx = get_request_context()
    if ctx is None or ctx.household_id is None:
        raise HouseholdScopeError(
            "Repository called without a household context. "
            "Set household_id in request context before calling repository methods."
        )

    str_id = str(household_id)
    if ctx.household_id != str_id:
        raise HouseholdScopeError(
            f"Cross-household access attempt: context has {ctx.household_id!r}, "
            f"query requested {str_id!r}."
        )
    return str_id
