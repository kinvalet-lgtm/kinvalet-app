"""Connector adapter registry.

Adding a connector type = one new file in adapters/ + registry entry here.
No other module changes. No changes to the extraction pipeline.
(AC 7.4 — the extensibility guarantee.)
"""
from typing import Any, Optional


def _build_registry() -> dict[str, Any]:
    from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
    from app.modules.connectors.adapters.microsoft_calendar import MicrosoftCalendarAdapter
    from app.modules.connectors.adapters.email_forward import EmailForwardAdapter
    from app.modules.connectors.adapters.gmail import GmailAdapter
    return {
        "google_calendar": GoogleCalendarAdapter(),
        "microsoft_calendar": MicrosoftCalendarAdapter(),
        "email_forwarding": EmailForwardAdapter(),
        "gmail": GmailAdapter(),
    }


_registry: Optional[dict] = None


def get_adapter(connector_type_id: str) -> Optional[Any]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry.get(connector_type_id)


def get_all_adapters() -> dict[str, Any]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


# Seed data for connector_type table — run once at startup
CONNECTOR_TYPE_SEEDS = [
    {
        "id": "google_calendar",
        "display_name": "Google Calendar",
        "category": "calendar",
        "auth_method": "oauth2",
        "fixed_permission_profile": {"scopes": ["calendar.events", "calendar.readonly"]},
        "copy_what_we_read": "your event times, titles, and locations",
        "copy_what_we_write": "events you confirm via WhatsApp or dashboard",
        "copy_used_for": "your daily briefing and scheduling conflict detection",
        "status": "active",
    },
    {
        "id": "microsoft_calendar",
        "display_name": "Outlook / Microsoft 365 Calendar",
        "category": "calendar",
        "auth_method": "oauth2",
        "fixed_permission_profile": {"scopes": ["Calendars.ReadWrite"]},
        "copy_what_we_read": "your event times, titles, and locations",
        "copy_what_we_write": "events you confirm via WhatsApp or dashboard",
        "copy_used_for": "your daily briefing and scheduling conflict detection",
        "status": "active",
    },
    {
        "id": "gmail",
        "display_name": "Gmail",
        "category": "email_ingestion",
        "auth_method": "oauth2",
        "fixed_permission_profile": {"scopes": ["gmail.readonly"]},
        "copy_what_we_read": "emails in the specific folders you select (subject, sender, body)",
        "copy_what_we_write": "nothing — read-only. We never send, delete, or modify your email.",
        "copy_used_for": "automatically extracting school notices, appointment reminders, and care updates from your selected Gmail folders",
        "status": "active",
    },
    {
        "id": "email_forwarding",
        "display_name": "Email Forwarding",
        "category": "email_ingestion",
        "auth_method": "webhook_forward_address",
        "fixed_permission_profile": {"scope": "read_forwarded_mail_only"},
        "copy_what_we_read": "only mail you forward to your unique inbound address",
        "copy_what_we_write": "nothing",
        "copy_used_for": "extracting school and senior-care updates from forwarded emails",
        "status": "active",
    },
]
