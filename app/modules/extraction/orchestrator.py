"""Extraction orchestrator — runs the LLM agent and dispatches results.

Flow (§11.3):
1. Receive message_id and context
2. Fetch message, prepare prompt
3. Call LLM agent with tool schema
4. Parse tool calls → create/update domain objects
5. Confidence >= threshold → auto-confirm; < threshold → route to ops queue
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.extraction.agent_tools import AGENT_TOOLS, SYSTEM_PROMPT
from app.modules.extraction.llm_client import LLMTask, get_llm_client
from app.modules.extraction.models import ExtractionResult
from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()


class ExtractionOrchestrator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._llm = get_llm_client()

    async def extract(
        self,
        message_id: uuid.UUID,
        household_id: uuid.UUID,
        member_id: uuid.UUID,
        raw_text: Optional[str],
        media_type: str,
        media_url: Optional[str] = None,
        household_members: Optional[list[dict]] = None,
    ) -> ExtractionResult:
        """Run the full extraction pipeline for an inbound message."""

        # Build the user message
        user_content = self._build_user_content(
            raw_text=raw_text,
            media_type=media_type,
            media_url=media_url,
            household_members=household_members or [],
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # Determine task type for model routing
        task = LLMTask.EXTRACT_VISION if media_type in ("image", "pdf") else LLMTask.EXTRACT_TEXT

        # Call LLM
        response = await self._llm.complete(
            task=task,
            messages=messages,
            tools=AGENT_TOOLS,
            household_id=str(household_id),
        )

        # Parse tool calls from response
        extracted_json = self._parse_response(response.content)
        confidence = self._compute_confidence(extracted_json)

        requires_review = confidence < _settings.confidence_threshold

        # Save extraction result
        result = ExtractionResult(
            inbound_message_id=message_id,
            household_id=household_id,
            extracted_json=extracted_json,
            confidence_score=round(confidence, 2),
            model_name=response.model,
            model_version="",
            requires_human_review=requires_review,
        )
        self._session.add(result)
        await self._session.flush()

        logger.info(
            "extraction_complete",
            message_id=str(message_id),
            confidence=confidence,
            requires_review=requires_review,
            tool_calls=len(extracted_json.get("tool_calls", [])),
        )

        return result

    def _build_user_content(
        self,
        raw_text: Optional[str],
        media_type: str,
        media_url: Optional[str],
        household_members: list[dict],
    ) -> str | list:
        """Build the user message content, optionally with image."""
        member_context = ""
        if household_members:
            names = ", ".join(
                f"{m.get('display_name')} ({m.get('role')})"
                for m in household_members
            )
            member_context = f"\nHousehold members: {names}\n"

        text_part = raw_text or "(no text)"

        if media_type in ("image", "pdf") and media_url:
            # Vision call — include image URL
            return [
                {"type": "text", "text": f"{member_context}Message: {text_part}"},
                {
                    "type": "image_url",
                    "image_url": {"url": media_url, "detail": "high"},
                },
            ]

        return f"{member_context}Message: {text_part}"

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse tool calls from LLM response."""
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "tool_calls" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: unclassifiable response
        return {
            "tool_calls": [],
            "text_response": content,
            "classification": "unclassifiable",
        }

    def _compute_confidence(self, extracted_json: dict) -> float:
        """Compute overall confidence from tool calls."""
        tool_calls = extracted_json.get("tool_calls", [])

        if not tool_calls:
            # No actionable content — this is fine (chit-chat, etc.)
            return 1.0

        # Use the minimum confidence across all draft items
        confidences = []
        for tc in tool_calls:
            if tc.get("name") == "create_draft_item":
                conf = tc.get("arguments", {}).get("confidence", 0.5)
                confidences.append(float(conf))

        if not confidences:
            return 0.95  # Non-item tool calls (clarification, support) are high-confidence

        return min(confidences)
