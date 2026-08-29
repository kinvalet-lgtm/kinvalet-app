"""Microsoft Graph / Outlook Calendar connector adapter.

OAuth 2.0 via MSAL with refresh tokens.
Uses Microsoft Graph API for calendar events and freebusy queries.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from app.contracts.connectors import BusyWindow, ConnectorHealth, ConnectorInstanceDTO
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

GRAPH_SCOPES = ["Calendars.ReadWrite", "User.Read", "offline_access"]
REDIRECT_URI_PATH = "/api/v1/connectors/microsoft/callback"
AUTHORITY = "https://login.microsoftonline.com/common"


class MicrosoftCalendarAdapter:
    """Microsoft Outlook Calendar adapter — satisfies ConnectorAdapter protocol."""

    connector_type_id = "microsoft_calendar"

    def _get_msal_app(self):
        import msal
        return msal.ConfidentialClientApplication(
            _settings.microsoft_client_id,
            authority=AUTHORITY,
            client_credential=_settings.microsoft_client_secret,
        )

    async def initiate_auth(self, household_id: UUID, member_id: UUID) -> str:
        app = self._get_msal_app()
        redirect_uri = f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}"
        auth_url = app.get_authorization_request_url(
            scopes=GRAPH_SCOPES,
            state=f"{household_id}:{member_id}",
            redirect_uri=redirect_uri,
        )
        return auth_url

    async def handle_auth_callback(self, payload: dict) -> ConnectorInstanceDTO:
        code = payload["code"]
        state = payload.get("state", ":")
        household_id, member_id = state.split(":")

        app = self._get_msal_app()
        redirect_uri = f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}"
        result = app.acquire_token_by_authorization_code(
            code=code,
            scopes=GRAPH_SCOPES,
            redirect_uri=redirect_uri,
        )
        if "error" in result:
            raise ValueError(f"Microsoft OAuth error: {result['error_description']}")

        # Get user email via Graph
        import httpx
        access_token = result["access_token"]
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            email = resp.json().get("mail") or resp.json().get("userPrincipalName", "unknown")

        secret_ref = await GoogleCalendarAdapter._store_secret(
            f"ms_cal_{household_id}_{member_id}", json.dumps(result)
        )

        return ConnectorInstanceDTO(
            id=UUID(int=0),
            household_id=UUID(household_id),
            connector_type_id=self.connector_type_id,
            connected_by_member_id=UUID(member_id),
            external_account_ref=email,
            status="connected",
        )

    async def get_busy_windows(
        self,
        instance: ConnectorInstanceDTO,
        start: datetime,
        end: datetime,
    ) -> list[BusyWindow]:
        """Query Microsoft Graph calendar view for busy times."""
        access_token = await self._get_access_token(instance)
        if not access_token:
            return []

        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://graph.microsoft.com/v1.0/me/calendar/getSchedule",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "schedules": ["me"],
                        "startTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
                        "endTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
                        "availabilityViewInterval": 15,
                    },
                    timeout=10,
                )
                data = resp.json()
                schedules = data.get("value", [{}])[0]
                busy_items = [
                    item for item in schedules.get("scheduleItems", [])
                    if item.get("status") == "busy"
                ]
                return [
                    BusyWindow(
                        start=datetime.fromisoformat(b["start"]["dateTime"].replace("Z", "+00:00")),
                        end=datetime.fromisoformat(b["end"]["dateTime"].replace("Z", "+00:00")),
                        event_title=b.get("subject"),
                    )
                    for b in busy_items
                ]
        except Exception as e:
            logger.error("microsoft_freebusy_failed", error=str(e))
            return []

    async def create_event(
        self,
        instance: ConnectorInstanceDTO,
        title: str,
        start_at: datetime,
        end_at: Optional[datetime],
        location: Optional[str],
    ) -> Optional[str]:
        access_token = await self._get_access_token(instance)
        if not access_token:
            return None

        end = end_at or (start_at + timedelta(hours=1))
        body: dict = {
            "subject": title,
            "start": {"dateTime": start_at.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }
        if location:
            body["location"] = {"displayName": location}

        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://graph.microsoft.com/v1.0/me/events",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=10,
                )
                return resp.json().get("id")
        except Exception as e:
            logger.error("microsoft_event_create_failed", error=str(e))
            return None

    async def fetch_incremental(self, instance: ConnectorInstanceDTO) -> list[dict]:
        return []  # TODO: delta query via Graph

    async def handle_webhook(self, payload: dict) -> list[dict]:
        return []

    async def revoke(self, instance: ConnectorInstanceDTO) -> None:
        pass  # Microsoft tokens expire; no active revocation endpoint

    async def health_check(self, instance: ConnectorInstanceDTO) -> ConnectorHealth:
        token = await self._get_access_token(instance)
        if not token:
            return ConnectorHealth(
                status="error",
                last_checked_at=datetime.now(timezone.utc),
                error_message="Token refresh failed",
            )
        return ConnectorHealth(status="healthy", last_checked_at=datetime.now(timezone.utc))

    async def _get_access_token(self, instance: ConnectorInstanceDTO) -> Optional[str]:
        token_json = await GoogleCalendarAdapter._read_secret(instance.secret_ref)
        if not token_json:
            return None
        token_data = json.loads(token_json)
        # Try to refresh using MSAL
        app = self._get_msal_app()
        result = app.acquire_token_by_refresh_token(
            token_data.get("refresh_token", ""),
            scopes=GRAPH_SCOPES,
        )
        if "error" in result:
            logger.error("microsoft_token_refresh_failed", error=result.get("error_description"))
            return None
        return result.get("access_token")


# Import here to avoid circular — adapter shares secret helpers
from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
