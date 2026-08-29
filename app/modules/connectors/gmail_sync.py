"""Production Gmail integration — Pub/Sub push notifications + incremental history sync.

Architecture:
  Gmail API → users.watch() registers push notifications
  Gmail → Google Cloud Pub/Sub topic (on new/modified messages)
  Pub/Sub → POST /api/v1/connectors/gmail/push (our webhook)
  Our webhook → uses historyId to fetch ONLY new messages via history.list()
  New messages → persisted idempotently → fed to extraction pipeline

Why push over polling:
  - Real-time: emails appear in KinValet within seconds, not minutes
  - Efficient: no wasted API calls checking empty inboxes
  - Scalable: works for 50 or 50,000 households without increasing API quota

Key design decisions:
  - historyId per account: tracks what we've already processed
  - Idempotent by Gmail message ID: redelivered Pub/Sub messages are safe
  - Watch renewal every 6 days (expires after 7): scheduled job in worker
  - Multi-tenant: each household member's Gmail is isolated by connector_instance_id
  - Label filtering: only messages matching selected labels are processed
"""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update, String, DateTime, Text, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID as PgUUID, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import text as sa_text, MetaData

from app.modules.connectors.calendar_config import ConnectorsConfigBase, MemberGmailConfig
from app.modules.connectors.models import HouseholdConnectorInstance
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# Google Cloud Pub/Sub topic for Gmail push notifications
# Format: projects/{project_id}/topics/{topic_name}
PUBSUB_TOPIC = f"projects/{_settings.google_client_id.split('-')[0] if _settings.google_client_id else 'project'}/topics/kinvalet-gmail-push"

# Watch renewal: renew 1 day before expiration (watches last 7 days)
WATCH_RENEWAL_DAYS = 6


# ── Database models ───────────────────────────────────────────────────────────

class GmailSyncState(ConnectorsConfigBase):
    """Per-member Gmail sync state — tracks historyId for incremental sync.

    Each household member who connects Gmail gets one row.
    historyId advances monotonically — we never re-process old history.
    """
    __tablename__ = "gmail_sync_state"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_member_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    connector_instance_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, unique=True)

    # Gmail API history tracking
    history_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gmail_email: Mapped[str] = mapped_column(String(300), nullable=False)

    # Watch state
    watch_expiration: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    watch_resource_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_errors: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa_text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa_text("now()"), onupdate=sa_text("now()")
    )


class GmailProcessedMessage(ConnectorsConfigBase):
    """Idempotency table — tracks which Gmail messages have been processed.

    Prevents duplicate extraction when Pub/Sub redelivers or history overlaps.
    Unique on (connector_instance_id, gmail_message_id).
    """
    __tablename__ = "gmail_processed_message"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_instance_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Extracted metadata
    from_email: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # comma-separated
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Processing state
    extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa_text("now()"))


# ── Gmail Watch Management ────────────────────────────────────────────────────

class GmailWatchManager:
    """Manages Gmail API watch() registrations for push notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def setup_watch(
        self,
        instance: HouseholdConnectorInstance,
        label_ids: list[str],
        topic_name: str = PUBSUB_TOPIC,
    ) -> dict:
        """Register a Gmail watch for push notifications.

        Called after initial OAuth and whenever labels change.
        Gmail will push to our Pub/Sub topic when matching messages arrive.
        """
        creds = await self._load_credentials(instance)
        if creds is None:
            raise ValueError("Cannot load Gmail credentials")

        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)

        # Register the watch
        watch_request = {
            "topicName": topic_name,
            "labelIds": label_ids if label_ids else ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        }

        try:
            result = service.users().watch(userId="me", body=watch_request).execute()

            history_id = result.get("historyId")
            expiration = result.get("expiration")  # ms since epoch

            # Update sync state
            sync_state = await self._get_or_create_sync_state(instance)
            if history_id and (not sync_state.history_id):
                sync_state.history_id = history_id
            if expiration:
                sync_state.watch_expiration = datetime.fromtimestamp(
                    int(expiration) / 1000, tz=timezone.utc
                )
            sync_state.sync_errors = 0
            sync_state.last_error = None
            await self._session.flush()

            logger.info(
                "gmail_watch_registered",
                email=instance.external_account_ref,
                history_id=history_id,
                expiration=sync_state.watch_expiration.isoformat() if sync_state.watch_expiration else None,
                labels=label_ids,
            )

            return {
                "historyId": history_id,
                "expiration": sync_state.watch_expiration.isoformat() if sync_state.watch_expiration else None,
            }

        except Exception as e:
            logger.error("gmail_watch_failed", email=instance.external_account_ref, error=str(e))
            sync_state = await self._get_or_create_sync_state(instance)
            sync_state.sync_errors += 1
            sync_state.last_error = str(e)
            await self._session.flush()
            raise

    async def stop_watch(self, instance: HouseholdConnectorInstance) -> None:
        """Stop receiving push notifications for this account."""
        creds = await self._load_credentials(instance)
        if creds is None:
            return

        try:
            from googleapiclient.discovery import build
            service = build("gmail", "v1", credentials=creds)
            service.users().stop(userId="me").execute()
            logger.info("gmail_watch_stopped", email=instance.external_account_ref)
        except Exception as e:
            logger.warning("gmail_watch_stop_failed", error=str(e))

        # Clear watch state
        sync_state = await self._get_sync_state(instance.id)
        if sync_state:
            sync_state.watch_expiration = None
            sync_state.watch_resource_id = None

    async def renew_expiring_watches(self) -> int:
        """Renew all watches expiring within 24 hours. Called by worker scheduler."""
        cutoff = datetime.now(timezone.utc) + timedelta(days=1)
        result = await self._session.execute(
            select(GmailSyncState)
            .where(GmailSyncState.watch_expiration <= cutoff)
            .where(GmailSyncState.watch_expiration.isnot(None))
        )
        expiring = result.scalars().all()

        renewed = 0
        for state in expiring:
            try:
                instance = await self._session.execute(
                    select(HouseholdConnectorInstance)
                    .where(HouseholdConnectorInstance.id == state.connector_instance_id)
                    .where(HouseholdConnectorInstance.status == "connected")
                )
                inst = instance.scalar_one_or_none()
                if inst is None:
                    continue

                # Get selected labels
                config = await self._session.execute(
                    select(MemberGmailConfig)
                    .where(MemberGmailConfig.connector_instance_id == state.connector_instance_id)
                )
                gmail_config = config.scalar_one_or_none()
                label_ids = gmail_config.label_list if gmail_config else ["INBOX"]

                if not label_ids or label_ids == [""]:
                    continue  # User stopped reading — don't renew

                await self.setup_watch(inst, label_ids)
                renewed += 1

            except Exception as e:
                logger.error(
                    "gmail_watch_renewal_failed",
                    connector_id=str(state.connector_instance_id),
                    error=str(e),
                )

        if renewed:
            logger.info("gmail_watches_renewed", count=renewed)
        return renewed

    async def _get_or_create_sync_state(
        self, instance: HouseholdConnectorInstance
    ) -> GmailSyncState:
        state = await self._get_sync_state(instance.id)
        if state is None:
            state = GmailSyncState(
                household_id=instance.household_id,
                household_member_id=instance.connected_by_member_id,
                connector_instance_id=instance.id,
                gmail_email=instance.external_account_ref,
            )
            self._session.add(state)
            await self._session.flush()
        return state

    async def _get_sync_state(self, connector_instance_id: uuid.UUID) -> Optional[GmailSyncState]:
        result = await self._session.execute(
            select(GmailSyncState)
            .where(GmailSyncState.connector_instance_id == connector_instance_id)
        )
        return result.scalar_one_or_none()

    async def _load_credentials(self, instance):
        from app.modules.connectors.adapters.gmail import GmailAdapter
        from app.contracts.connectors import ConnectorInstanceDTO
        adapter = GmailAdapter()
        return await adapter._load_credentials(ConnectorInstanceDTO(
            id=instance.id,
            household_id=instance.household_id,
            connector_type_id=instance.connector_type_id,
            connected_by_member_id=instance.connected_by_member_id,
            external_account_ref=instance.external_account_ref,
            status=instance.status,
        ))


# ── Incremental History Sync ──────────────────────────────────────────────────

class GmailHistorySync:
    """Process Gmail push notifications using incremental history sync.

    When Pub/Sub delivers a notification:
    1. Decode the email address from the notification
    2. Look up the sync state (historyId)
    3. Call history.list(startHistoryId=...) to get only NEW messages
    4. Filter by selected labels
    5. Fetch full message content for matching messages
    6. Persist idempotently (by gmail_message_id)
    7. Feed to extraction pipeline
    8. Advance historyId
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def process_push_notification(self, pubsub_message: dict) -> int:
        """Handle a Pub/Sub push notification from Gmail.

        Returns the number of new messages processed.
        """
        # Decode the Pub/Sub message
        data = pubsub_message.get("message", {}).get("data", "")
        if not data:
            return 0

        try:
            decoded = json.loads(base64.urlsafe_b64decode(data + "=="))
        except Exception:
            logger.warning("gmail_push_decode_failed", data=data[:100])
            return 0

        email_address = decoded.get("emailAddress", "")
        new_history_id = decoded.get("historyId", "")

        if not email_address:
            return 0

        logger.info("gmail_push_received", email=email_address, history_id=new_history_id)

        # Find the sync state for this email
        result = await self._session.execute(
            select(GmailSyncState)
            .where(GmailSyncState.gmail_email == email_address)
        )
        sync_state = result.scalar_one_or_none()
        if sync_state is None:
            logger.warning("gmail_push_no_sync_state", email=email_address)
            return 0

        # Get the connector instance
        inst_result = await self._session.execute(
            select(HouseholdConnectorInstance)
            .where(HouseholdConnectorInstance.id == sync_state.connector_instance_id)
            .where(HouseholdConnectorInstance.status == "connected")
        )
        instance = inst_result.scalar_one_or_none()
        if instance is None:
            return 0

        # Get selected labels (only process messages in these labels)
        config_result = await self._session.execute(
            select(MemberGmailConfig)
            .where(MemberGmailConfig.connector_instance_id == instance.id)
        )
        gmail_config = config_result.scalar_one_or_none()
        selected_labels = set(gmail_config.label_list if gmail_config else ["INBOX"])

        if not selected_labels or selected_labels == {""}:
            # User has stopped reading — skip processing
            return 0

        # Load credentials
        creds = await GmailWatchManager(self._session)._load_credentials(instance)
        if creds is None:
            return 0

        # Fetch history since last known historyId
        if not sync_state.history_id:
            sync_state.history_id = new_history_id
            await self._session.flush()
            return 0

        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)

        try:
            new_message_ids = await self._fetch_history(
                service, sync_state.history_id, selected_labels
            )
        except Exception as e:
            if "historyId is too old" in str(e) or "404" in str(e):
                # History expired — do a full label scan instead
                logger.warning("gmail_history_expired", email=email_address)
                new_message_ids = await self._full_label_scan(
                    service, selected_labels, max_results=20
                )
            else:
                logger.error("gmail_history_fetch_failed", error=str(e))
                sync_state.sync_errors += 1
                sync_state.last_error = str(e)
                await self._session.flush()
                return 0

        # Process each new message (idempotently)
        processed = 0
        for msg_id in new_message_ids:
            if await self._is_already_processed(instance.id, msg_id):
                continue

            try:
                msg_data = await self._fetch_message(service, msg_id)
                if msg_data:
                    await self._persist_and_extract(instance, sync_state, msg_data)
                    processed += 1
            except Exception as e:
                logger.error("gmail_message_process_failed", msg_id=msg_id, error=str(e))

        # Advance historyId
        if new_history_id:
            sync_state.history_id = new_history_id
        sync_state.last_synced_at = datetime.now(timezone.utc)
        sync_state.sync_errors = 0
        sync_state.last_error = None
        await self._session.commit()

        if processed:
            logger.info(
                "gmail_messages_processed",
                email=email_address,
                count=processed,
                household_id=str(sync_state.household_id),
            )

        return processed

    async def _fetch_history(
        self,
        service,
        start_history_id: str,
        selected_labels: set[str],
    ) -> list[str]:
        """Use history.list() for incremental sync — fetch only new message IDs."""
        message_ids = []
        page_token = None

        while True:
            kwargs = {
                "userId": "me",
                "startHistoryId": start_history_id,
                "historyTypes": ["messageAdded"],
                "maxResults": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result = service.users().history().list(**kwargs).execute()
            histories = result.get("history", [])

            for h in histories:
                for added in h.get("messagesAdded", []):
                    msg = added.get("message", {})
                    msg_labels = set(msg.get("labelIds", []))
                    # Only include messages that match selected labels
                    if msg_labels & selected_labels:
                        message_ids.append(msg["id"])

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return message_ids

    async def _full_label_scan(
        self, service, selected_labels: set[str], max_results: int = 20
    ) -> list[str]:
        """Fallback when history is too old — scan selected labels directly."""
        message_ids = []
        for label_id in selected_labels:
            try:
                result = service.users().messages().list(
                    userId="me",
                    labelIds=[label_id],
                    maxResults=max_results,
                ).execute()
                for msg in result.get("messages", []):
                    message_ids.append(msg["id"])
            except Exception:
                continue
        return list(set(message_ids))  # dedupe

    async def _fetch_message(self, service, msg_id: str) -> Optional[dict]:
        """Fetch full message content from Gmail API."""
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = self._extract_body(msg.get("payload", {}))

            return {
                "id": msg["id"],
                "threadId": msg.get("threadId"),
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "body": body[:5000],  # cap at 5KB per §11.9
                "labelIds": msg.get("labelIds", []),
                "internalDate": msg.get("internalDate"),
            }
        except Exception as e:
            logger.error("gmail_fetch_message_failed", msg_id=msg_id, error=str(e))
            return None

    def _extract_body(self, payload: dict, depth: int = 0) -> str:
        """Recursively extract plain text body from MIME parts."""
        if depth > 5:
            return ""
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        for part in payload.get("parts", []):
            result = self._extract_body(part, depth + 1)
            if result:
                return result
        return ""

    async def _is_already_processed(
        self, connector_instance_id: uuid.UUID, gmail_message_id: str
    ) -> bool:
        """Idempotency check — has this message already been processed?"""
        result = await self._session.execute(
            select(GmailProcessedMessage)
            .where(GmailProcessedMessage.connector_instance_id == connector_instance_id)
            .where(GmailProcessedMessage.gmail_message_id == gmail_message_id)
        )
        return result.scalar_one_or_none() is not None

    async def _persist_and_extract(
        self,
        instance: HouseholdConnectorInstance,
        sync_state: GmailSyncState,
        msg_data: dict,
    ) -> None:
        """Persist the message and feed it to the extraction pipeline."""
        # Parse received date
        received_at = None
        if msg_data.get("internalDate"):
            received_at = datetime.fromtimestamp(
                int(msg_data["internalDate"]) / 1000, tz=timezone.utc
            )

        # Persist (idempotent — unique on connector_instance_id + gmail_message_id)
        processed = GmailProcessedMessage(
            connector_instance_id=instance.id,
            household_id=instance.household_id,
            gmail_message_id=msg_data["id"],
            gmail_thread_id=msg_data.get("threadId"),
            from_email=msg_data.get("from"),
            subject=msg_data.get("subject"),
            snippet=msg_data.get("snippet"),
            label_ids=",".join(msg_data.get("labelIds", [])),
            received_at=received_at,
            extracted=False,
        )
        self._session.add(processed)
        await self._session.flush()

        # Create an inbound message for the extraction pipeline
        from app.modules.inbound.models import InboundMessage
        inbound_msg = InboundMessage(
            household_id=instance.household_id,
            household_member_id=instance.connected_by_member_id,
            source="email",
            provider_message_id=f"gmail:{msg_data['id']}",
            sender_email=msg_data.get("from"),
            media_type="text",
            raw_text=f"Subject: {msg_data.get('subject', '')}\n\n{msg_data.get('body', '')}",
            status="processing",
        )
        self._session.add(inbound_msg)
        await self._session.flush()

        # Enqueue extraction
        try:
            from app.modules.inbound.router import _enqueue_extraction
            await _enqueue_extraction(
                message_id=str(inbound_msg.id),
                household_id=str(instance.household_id),
                member_id=str(instance.connected_by_member_id),
            )
            processed.extracted = True
        except Exception as e:
            logger.error("gmail_extraction_enqueue_failed", msg_id=msg_data["id"], error=str(e))

        # Publish event
        from app.platform.events.outbox import add_event_to_outbox
        await add_event_to_outbox(self._session, "GmailMessageReceived", {
            "household_id": str(instance.household_id),
            "member_id": str(instance.connected_by_member_id),
            "gmail_message_id": msg_data["id"],
            "from": msg_data.get("from"),
            "subject": msg_data.get("subject"),
            "inbound_message_id": str(inbound_msg.id),
        })
