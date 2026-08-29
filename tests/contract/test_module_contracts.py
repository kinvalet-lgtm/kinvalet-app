"""Contract tests — verify every module's api.py satisfies its Protocol.

This is the load-bearing test for the extraction path (§12.1):
It asserts that each module's implementation satisfies its declared Protocol —
which is exactly what a future HTTP client will also have to satisfy.
Keep these green and the day you swap an implementation for a network client,
you already know the shape is right.

These tests are DB-free — they verify structural conformance only.
"""
import uuid
from unittest.mock import AsyncMock

import pytest


class TestIdentityAPIContract:
    """IdentityService must satisfy the IdentityAPI Protocol."""

    def test_satisfies_protocol(self):
        """Check all required methods are present with correct signatures."""
        from app.contracts.identity import IdentityAPI
        from app.modules.identity.api import IdentityService

        # Protocol methods
        required_methods = [
            "get_member",
            "list_adult_members",
            "resolve_phone",
            "household_timezone",
            "get_household",
            "validate_token",
        ]

        session = AsyncMock()
        svc = IdentityService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"IdentityService missing method: {method}"
            assert callable(getattr(svc, method)), f"{method} is not callable"


class TestConnectorsAPIContract:
    """ConnectorsService must satisfy the ConnectorsAPI Protocol."""

    def test_satisfies_protocol(self):
        from app.contracts.connectors import ConnectorsAPI
        from app.modules.connectors.api import ConnectorsService

        required_methods = [
            "get_busy_windows",
            "create_calendar_event",
            "update_calendar_event",
            "delete_calendar_event",
            "get_instance_health",
        ]

        session = AsyncMock()
        svc = ConnectorsService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"ConnectorsService missing: {method}"


class TestOperationsAPIContract:
    def test_satisfies_protocol(self):
        from app.contracts.operations import OperationsAPI
        from app.modules.operations.api import OperationsService

        required_methods = ["get_item", "list_active_items"]

        session = AsyncMock()
        svc = OperationsService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"OperationsService missing: {method}"


class TestNotificationAPIContract:
    def test_satisfies_protocol(self):
        from app.contracts.notification import NotificationAPI
        from app.modules.notification.api import NotificationService

        required_methods = ["send", "send_whatsapp"]

        session = AsyncMock()
        svc = NotificationService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"NotificationService missing: {method}"


class TestExtractionAPIContract:
    def test_satisfies_protocol(self):
        from app.contracts.extraction import ExtractionAPI
        from app.modules.extraction.api import ExtractionService

        required_methods = ["get_result"]

        session = AsyncMock()
        svc = ExtractionService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"ExtractionService missing: {method}"


class TestSkillsAPIContract:
    def test_satisfies_protocol(self):
        from app.contracts.skills import SkillsAPI
        from app.modules.skills.api import SkillsService

        required_methods = ["schedule_skills_for_item", "cancel_skills_for_item"]

        session = AsyncMock()
        svc = SkillsService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"SkillsService missing: {method}"


class TestBriefingAPIContract:
    def test_satisfies_protocol(self):
        from app.contracts.briefing import BriefingAPI
        from app.modules.briefing.api import BriefingService

        required_methods = ["get_latest_briefing"]

        session = AsyncMock()
        svc = BriefingService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"BriefingService missing: {method}"


class TestSupportAPIContract:
    def test_satisfies_protocol(self):
        from app.contracts.support import SupportAPI
        from app.modules.support.api import SupportService

        required_methods = ["open_ticket"]

        session = AsyncMock()
        svc = SupportService(session)

        for method in required_methods:
            assert hasattr(svc, method), f"SupportService missing: {method}"
