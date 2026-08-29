"""LLM client wrapping LiteLLM for provider normalization, fallbacks, and cost tracking.

Routing table (§9.3):
- CLASSIFY: claude-haiku-4-5 (cheap — chit-chat, intent, triage)
- EXTRACT_TEXT: claude-sonnet-4-6
- EXTRACT_VISION: claude-sonnet-4-6 (photos, PDFs)
- BRIEFING: claude-haiku-4-5 (batched overnight)

Cost controls:
1. Prompt caching — stable prefix (tool schema + instructions + household roster)
2. Route by task — cheaper model for classification
3. Downscale images before vision calls (cap long edge)
4. Skip LLM where the answer is deterministic
"""
import enum
from typing import Optional, Protocol

import litellm
from app.platform.config import get_settings
from app.platform.observability import get_logger, log_cost_event

logger = get_logger(__name__)
_settings = get_settings()


class LLMTask(str, enum.Enum):
    CLASSIFY = "classify"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_VISION = "extract_vision"
    BRIEFING = "briefing"


# Model routing — Google Gemini (free tier: 15 RPM, 1500 req/day, 1M tokens/day)
ROUTING: dict[LLMTask, str] = {
    LLMTask.CLASSIFY: "gemini/gemini-2.0-flash",
    LLMTask.EXTRACT_TEXT: "gemini/gemini-2.0-flash",
    LLMTask.EXTRACT_VISION: "gemini/gemini-2.0-flash",
    LLMTask.BRIEFING: "gemini/gemini-2.0-flash",
}


class LLMResponse:
    def __init__(self, content: str, model: str, input_tokens: int, output_tokens: int) -> None:
        self.content = content
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def estimated_cost_cents(self) -> int:
        """Rough cost estimate in cents for logging to cost_ledger."""
        # GPT-4o-mini: ~$0.15/M input, $0.60/M output
        # GPT-4o: ~$2.50/M input, $10/M output
        if "mini" in self.model:
            cost_usd = (self.input_tokens * 0.15 + self.output_tokens * 0.60) / 1_000_000
        else:
            cost_usd = (self.input_tokens * 2.50 + self.output_tokens * 10.0) / 1_000_000
        return int(cost_usd * 100)


class LiteLLMClient:
    """Wraps LiteLLM. Business logic never imports litellm directly."""

    def __init__(self) -> None:
        # LiteLLM auto-detects the provider from the model name prefix
        # gemini/ → uses GEMINI_API_KEY env var
        # gpt- → uses OPENAI_API_KEY env var
        # claude- → uses ANTHROPIC_API_KEY env var
        pass

    async def complete(
        self,
        *,
        task: LLMTask,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        household_id: Optional[str] = None,
    ) -> LLMResponse:
        model = ROUTING[task]

        kwargs: dict = {
            "model": model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await litellm.acompletion(**kwargs)

            content = ""
            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content or ""
                # If tool calls present, serialize them
                if response.choices[0].message.tool_calls:
                    import json
                    tool_calls = [
                        {
                            "name": tc.function.name,
                            "arguments": json.loads(tc.function.arguments),
                        }
                        for tc in response.choices[0].message.tool_calls
                    ]
                    content = json.dumps({"tool_calls": tool_calls})

            usage = response.usage
            result = LLMResponse(
                content=content,
                model=model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            )

            # Log cost
            if household_id:
                log_cost_event(
                    household_id=household_id,
                    event_type="llm_tokens",
                    amount_cents=result.estimated_cost_cents(),
                    metadata={"model": model, "task": task.value, "tokens": result.total_tokens},
                )

            return result

        except Exception as e:
            logger.error("llm_call_failed", model=model, task=task.value, error=str(e))
            raise


_client: Optional[LiteLLMClient] = None


def get_llm_client() -> LiteLLMClient:
    global _client
    if _client is None:
        _client = LiteLLMClient()
    return _client
