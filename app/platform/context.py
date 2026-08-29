"""Request context: household_id, member_id, request_id.

Propagated via contextvars so any code in the call stack can read them
without threading state through every function signature.
"""
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    household_id: Optional[str] = None
    member_id: Optional[str] = None
    actor_type: str = "system_agent"  # household_member | internal_user | system_agent


_ctx_var: ContextVar[Optional[RequestContext]] = ContextVar("request_context", default=None)


def set_request_context(
    household_id: Optional[str] = None,
    member_id: Optional[str] = None,
    actor_type: str = "system_agent",
    request_id: Optional[str] = None,
) -> RequestContext:
    ctx = RequestContext(
        request_id=request_id or str(uuid.uuid4()),
        household_id=household_id,
        member_id=member_id,
        actor_type=actor_type,
    )
    _ctx_var.set(ctx)
    return ctx


def get_request_context() -> Optional[RequestContext]:
    return _ctx_var.get()


def require_household_context() -> str:
    """Return the current household_id or raise if not set.

    All repository methods call this to enforce the tenancy invariant:
    a query cannot be constructed without a household scope.
    """
    ctx = _ctx_var.get()
    if ctx is None or ctx.household_id is None:
        raise RuntimeError(
            "A household_id is required in the request context for this operation. "
            "Set it via set_request_context() before calling repository methods."
        )
    return ctx.household_id
