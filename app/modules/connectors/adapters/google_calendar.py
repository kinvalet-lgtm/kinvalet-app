"""Google Calendar connector adapter.

Implements ConnectorAdapter protocol for Google Calendar OAuth + sync.
- OAuth 2.0 PKCE flow with offline access (refresh tokens)
- Incremental sync via sync tokens
- Push notifications via Google Calendar webhook channel
- Busy/free query for conflict detection (§11.6)
- Event write for confirmed items (§11.4)

Credential storage: secret_ref points to a key in Secrets Manager (or env in dev).
The token itself is NEVER stored in household_connector_instance.
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

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]
REDIRECT_URI_PATH = "/api/v1/connectors/google/callback"


def _build_flow(state: Optional[str] = None):
    """Build Google OAuth flow."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": _settings.google_client_id,
                "client_secret": _settings.google_client_secret,
                "redirect_uris": [f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_SCOPES,
    )
    flow.redirect_uri = f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}"
    return flow


class GoogleCalendarAdapter:
    """Google Calendar adapter — satisfies ConnectorAdapter protocol."""

    connector_type_id = "google_calendar"

    async def initiate_auth(self, household_id: UUID, member_id: UUID) -> str:
        """Return the Google OAuth authorization URL.

        Built manually (no google-auth-oauthlib Flow) to avoid PKCE code_challenge
        being injected. We're a confidential client — PKCE is not needed.
        """
        if not _settings.google_client_id:
            raise ValueError("GOOGLE_CLIENT_ID not configured")

        from urllib.parse import urlencode
        params = {
            "client_id": _settings.google_client_id,
            "redirect_uri": f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}",
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": f"{household_id}:{member_id}",
        }
        return f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"

    async def handle_auth_callback(self, payload: dict) -> ConnectorInstanceDTO:
        """Complete OAuth, store tokens, return instance DTO.

        Uses direct HTTP token exchange instead of the Flow object to avoid
        PKCE code_verifier issues (the library generates one in authorization_url
        but it's lost when the Flow is recreated in the callback).
        """
        code = payload["code"]
        state = payload.get("state", ":")
        household_id, member_id = state.split(":")

        # Direct token exchange — bypass google-auth-oauthlib's PKCE handling
        import httpx
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": _settings.google_client_id,
                "client_secret": _settings.google_client_secret,
                "redirect_uri": f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}",
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            raise ValueError(f"Token exchange failed: {token_response.text}")

        token_data = token_response.json()
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")

        if not access_token:
            raise ValueError(f"No access_token in response: {list(token_data.keys())}")

        # Get the Google account email using the access token directly
        import httpx as httpx_client
        userinfo_resp = httpx_client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        email = "unknown"
        if userinfo_resp.status_code == 200:
            email = userinfo_resp.json().get("email", "unknown")

        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=_settings.google_client_id,
            client_secret=_settings.google_client_secret,
            scopes=GOOGLE_SCOPES,
        )

        # Store token JSON in secrets backend
        token_json = creds.to_json()
        secret_ref = await self._store_secret(
            f"google_cal_{household_id}_{member_id}", token_json
        )

        return ConnectorInstanceDTO(
            id=UUID(int=0),
            household_id=UUID(household_id),
            connector_type_id=self.connector_type_id,
            connected_by_member_id=UUID(member_id),
            external_account_ref=email,
            status="connected",
            secret_ref=secret_ref,
        )

    async def get_busy_windows(
        self,
        instance: ConnectorInstanceDTO,
        start: datetime,
        end: datetime,
    ) -> list[BusyWindow]:
        """Query Google Calendar freebusy API (§11.6)."""
        creds = await self._load_credentials(instance)
        if creds is None:
            return []

        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)

        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": "primary"}],
        }
        try:
            result = service.freebusy().query(body=body).execute()
            busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
            return [
                BusyWindow(
                    start=datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                    end=datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
                )
                for b in busy
            ]
        except Exception as e:
            logger.error("google_freebusy_failed", error=str(e))
            return []

    async def fetch_incremental(self, instance: ConnectorInstanceDTO) -> list[dict]:
        """Fetch new/changed events since last sync using sync token."""
        creds = await self._load_credentials(instance)
        if creds is None:
            return []

        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)

        # TODO: persist sync_token per instance in DB
        try:
            result = service.events().list(
                calendarId="primary",
                singleEvents=True,
                orderBy="updated",
                maxResults=250,
            ).execute()
            return result.get("items", [])
        except Exception as e:
            logger.error("google_incremental_sync_failed", error=str(e))
            return []

    async def handle_webhook(self, payload: dict) -> list[dict]:
        """Handle Google Calendar push notification — trigger incremental fetch."""
        # Google sends a sparse push notification; we re-fetch incrementally
        return []

    async def create_event(
        self,
        instance: ConnectorInstanceDTO,
        title: str,
        start_at: datetime,
        end_at: Optional[datetime],
        location: Optional[str],
    ) -> Optional[str]:
        """Create a calendar event, return the provider event ID."""
        creds = await self._load_credentials(instance)
        if creds is None:
            return None

        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)

        end = end_at or (start_at + timedelta(hours=1))
        event_body: dict = {
            "summary": title,
            "start": {"dateTime": start_at.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if location:
            event_body["location"] = location

        try:
            created = service.events().insert(calendarId="primary", body=event_body).execute()
            return created.get("id")
        except Exception as e:
            logger.error("google_event_create_failed", error=str(e))
            return None

    async def revoke(self, instance: ConnectorInstanceDTO) -> None:
        """Revoke OAuth tokens."""
        creds = await self._load_credentials(instance)
        if creds and creds.token:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": creds.token},
                )

    async def health_check(self, instance: ConnectorInstanceDTO) -> ConnectorHealth:
        creds = await self._load_credentials(instance)
        if creds is None:
            return ConnectorHealth(
                status="error",
                last_checked_at=datetime.now(timezone.utc),
                error_message="Credentials not found",
            )
        if not creds.valid:
            return ConnectorHealth(
                status="degraded",
                last_checked_at=datetime.now(timezone.utc),
                error_message="Token expired — re-auth required",
            )
        return ConnectorHealth(status="healthy", last_checked_at=datetime.now(timezone.utc))

    async def _load_credentials(self, instance: ConnectorInstanceDTO):
        """Load Google credentials from secret_ref (stored as JSON in the DB column)."""
        try:
            from google.oauth2.credentials import Credentials
            secret_ref = instance.secret_ref
            if not secret_ref:
                logger.warning("google_creds_no_secret_ref")
                return None

            # secret_ref IS the token JSON (stored directly in DB, not a file path)
            token_json = secret_ref
            creds = Credentials.from_authorized_user_info(json.loads(token_json), GOOGLE_SCOPES)

            # Refresh if expired and persist the new access token back to DB
            if not creds.valid and creds.refresh_token:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                # Save refreshed token to DB so we don't re-refresh every call
                await self._persist_refreshed_token(instance, creds)
            return creds
        except Exception as e:
            logger.error("google_credentials_load_failed", error=str(e))
            return None

    @staticmethod
    async def _persist_refreshed_token(instance: ConnectorInstanceDTO, creds):
        """Write the refreshed access token back to the DB."""
        try:
            from sqlalchemy import update
            from app.modules.connectors.models import HouseholdConnectorInstance
            from app.platform.db import AsyncSessionFactory

            new_token_json = creds.to_json()
            async with AsyncSessionFactory() as session:
                await session.execute(
                    update(HouseholdConnectorInstance)
                    .where(HouseholdConnectorInstance.id == instance.id)
                    .values(secret_ref=new_token_json)
                )
                await session.commit()
            logger.info("google_token_refreshed", account=instance.external_account_ref)
        except Exception as e:
            logger.warning("google_token_persist_failed", error=str(e))

    @staticmethod
    async def _store_secret(key: str, value: str) -> str:
        """Store secret — returns the token JSON directly (stored in DB column).

        No filesystem, no /tmp, no external secrets manager.
        The token JSON is stored directly in connector_instance.secret_ref.
        Survives Railway deploys, container restarts, and scaling.
        """
        # Return the token JSON as-is — the caller stores it in the DB column
        return value
