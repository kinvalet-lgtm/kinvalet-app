"""Shared error taxonomy.

Each module defines its own domain errors inheriting from these base classes.
HTTP mapping lives in the FastAPI exception handlers in main.py.
"""
from typing import Any, Optional


class AppError(Exception):
    """Base for all application errors."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, detail: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


# ── 400 errors ────────────────────────────────────────────────────────────────

class ValidationError(AppError):
    status_code = 400
    error_code = "validation_error"


class DuplicateError(AppError):
    """The resource already exists (idempotency key collision)."""
    status_code = 409
    error_code = "duplicate"


class ConflictError(AppError):
    """State machine transition is not valid from the current state."""
    status_code = 409
    error_code = "state_conflict"


# ── 401 / 403 errors ──────────────────────────────────────────────────────────

class AuthenticationError(AppError):
    status_code = 401
    error_code = "authentication_required"


class AuthorizationError(AppError):
    status_code = 403
    error_code = "forbidden"


class HouseholdScopeError(AppError):
    """Cross-household data access attempt — tenancy guard fired."""
    status_code = 403
    error_code = "household_scope_violation"


# ── 404 errors ────────────────────────────────────────────────────────────────

class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"


# ── 422 errors ────────────────────────────────────────────────────────────────

class InvalidMemberError(AppError):
    """Member does not exist or does not belong to the expected household."""
    status_code = 422
    error_code = "invalid_member"


# ── 429 errors ────────────────────────────────────────────────────────────────

class RateLimitError(AppError):
    status_code = 429
    error_code = "rate_limited"


# ── Extraction errors ──────────────────────────────────────────────────────────

class ExtractionError(AppError):
    """LLM extraction failed in an unrecoverable way."""
    status_code = 500
    error_code = "extraction_failed"


class LowConfidenceError(AppError):
    """Extraction confidence below threshold — needs human review."""
    status_code = 200  # Not an error for the client; routed to ops queue
    error_code = "low_confidence"

    def __init__(self, message: str, confidence: float, detail: Optional[Any] = None) -> None:
        super().__init__(message, detail)
        self.confidence = confidence


# ── Connector errors ───────────────────────────────────────────────────────────

class ConnectorAuthError(AppError):
    """OAuth token expired or revoked; needs re-authentication."""
    status_code = 401
    error_code = "connector_auth_error"


class ConnectorSyncError(AppError):
    """External connector sync failed."""
    status_code = 502
    error_code = "connector_sync_error"


# ── Webhook errors ─────────────────────────────────────────────────────────────

class WebhookSignatureError(AppError):
    """Webhook signature verification failed."""
    status_code = 403
    error_code = "webhook_signature_invalid"


class WebhookIdempotencyHit(AppError):
    """This webhook has already been processed (idempotency key match)."""
    status_code = 200
    error_code = "already_processed"
