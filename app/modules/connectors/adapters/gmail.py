"""Gmail connector adapter.

Uses Gmail API (not email forwarding) with gmail.readonly scope.
After OAuth, the user selects which Gmail labels/folders to watch.
We poll for new messages matching those labels and run extraction.

Why Gmail API over forwarding:
- No forwarding rule setup needed — pure in-UI consent flow
- User selects exactly which labels to watch (school, healthcare, etc.)
- We never see email unrelated to selected labels
- Per-member: Sarah watches her school emails, Mark watches his

Scopes: gmail.readonly — read-only, cannot send, delete, or modify.
This is the minimum necessary; stated in consent UI (AC 11.2.1).
"""
import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.contracts.connectors import BusyWindow, ConnectorHealth, ConnectorInstanceDTO
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
REDIRECT_URI_PATH = "/api/v1/connectors/gmail/callback"

# System labels that are always available in every Gmail account
SYSTEM_LABELS = [
    {"id": "INBOX",     "name": "Inbox",      "type": "system"},
    {"id": "SENT",      "name": "Sent",       "type": "system"},
    {"id": "IMPORTANT", "name": "Important",  "type": "system"},
    {"id": "STARRED",   "name": "Starred",    "type": "system"},
    {"id": "UNREAD",    "name": "Unread",     "type": "system"},
]


class GmailAdapter:
    """Gmail connector — read selected labels, run extraction on matching emails."""

    connector_type_id = "gmail"

    async def initiate_auth(self, household_id: UUID, member_id: UUID) -> str:
        if not _settings.google_client_id:
            raise ValueError("GOOGLE_CLIENT_ID not configured")

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
            scopes=GMAIL_SCOPES,
        )
        flow.redirect_uri = f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}"
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            state=f"{household_id}:{member_id}",
            prompt="consent",
        )
        return auth_url

    async def handle_auth_callback(self, payload: dict) -> ConnectorInstanceDTO:
        code = payload["code"]
        state = payload.get("state", ":")
        household_id, member_id = state.split(":")

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
            scopes=GMAIL_SCOPES,
        )
        flow.redirect_uri = f"{_settings.twilio_webhook_base_url}{REDIRECT_URI_PATH}"
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Get Gmail account email
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "unknown")

        from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
        secret_ref = await GoogleCalendarAdapter._store_secret(
            f"gmail_{household_id}_{member_id}", creds.to_json()
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

    async def list_labels(self, instance: ConnectorInstanceDTO) -> list[dict]:
        """Return all Gmail labels for this account — shown in the UI label picker."""
        creds = await self._load_credentials(instance)
        if creds is None:
            return SYSTEM_LABELS

        try:
            from googleapiclient.discovery import build
            service = build("gmail", "v1", credentials=creds)
            result = service.users().labels().list(userId="me").execute()
            all_labels = result.get("labels", [])

            # Separate system vs user-created labels
            return [
                {
                    "id": lbl["id"],
                    "name": lbl["name"],
                    "type": lbl.get("type", "user"),
                }
                for lbl in all_labels
                if not lbl["name"].startswith("CATEGORY_")  # skip Gmail auto-categories
            ]
        except Exception as e:
            logger.error("gmail_list_labels_failed", error=str(e))
            return SYSTEM_LABELS

    async def fetch_messages_for_labels(
        self,
        instance: ConnectorInstanceDTO,
        label_ids: list[str],
        max_results: int = 10,
        page_token: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """Fetch emails matching the selected labels. Returns (messages, next_page_token)."""
        creds = await self._load_credentials(instance)
        if creds is None:
            return [], None

        try:
            from googleapiclient.discovery import build
            service = build("gmail", "v1", credentials=creds)

            kwargs: dict = {
                "userId": "me",
                "labelIds": label_ids,
                "maxResults": max_results,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result = service.users().messages().list(**kwargs).execute()
            message_ids = result.get("messages", [])
            next_token = result.get("nextPageToken")

            messages = []
            for msg_ref in message_ids:
                msg = service.users().messages().get(
                    userId="me",
                    id=msg_ref["id"],
                    format="full",
                ).execute()
                parsed = self._parse_message(msg)
                if parsed:
                    messages.append(parsed)

            return messages, next_token

        except Exception as e:
            logger.error("gmail_fetch_failed", error=str(e))
            return [], None

    def _parse_message(self, msg: dict) -> Optional[dict]:
        """Extract fields needed for extraction pipeline."""
        try:
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = self._extract_body(msg.get("payload", {}))
            return {
                "message_id": msg["id"],
                "thread_id": msg.get("threadId"),
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "body": body[:5000],  # cap at 5KB per §11.9
                "label_ids": msg.get("labelIds", []),
            }
        except Exception:
            return None

    def _extract_body(self, payload: dict, depth: int = 0) -> str:
        """Recursively extract plain text body from MIME parts."""
        if depth > 5:
            return ""
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/plain":
            import base64
            data = payload.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        for part in payload.get("parts", []):
            result = self._extract_body(part, depth + 1)
            if result:
                return result
        return payload.get("snippet", "")

    async def fetch_incremental(self, instance: ConnectorInstanceDTO) -> list[dict]:
        """Fetch recent inbox emails — called by sync worker."""
        return []  # label_ids stored in instance extra config

    async def handle_webhook(self, payload: dict) -> list[dict]:
        return []

    async def revoke(self, instance: ConnectorInstanceDTO) -> None:
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
        if not creds or not creds.valid:
            return ConnectorHealth(
                status="error",
                last_checked_at=datetime.now(timezone.utc),
                error_message="Token expired or missing",
            )
        return ConnectorHealth(status="healthy", last_checked_at=datetime.now(timezone.utc))

    async def _load_credentials(self, instance: ConnectorInstanceDTO):
        try:
            from google.oauth2.credentials import Credentials
            from app.modules.connectors.adapters.google_calendar import GoogleCalendarAdapter
            token_json = await GoogleCalendarAdapter._read_secret(instance.secret_ref)
            if not token_json:
                return None
            creds = Credentials.from_authorized_user_info(json.loads(token_json), GMAIL_SCOPES)
            if not creds.valid and creds.refresh_token:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
            return creds
        except Exception as e:
            logger.error("gmail_credentials_failed", error=str(e))
            return None
