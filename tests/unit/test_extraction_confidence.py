"""Unit tests for the extraction orchestrator confidence computation."""
import pytest

from app.modules.extraction.orchestrator import ExtractionOrchestrator


class TestConfidenceComputation:
    def test_no_tool_calls_returns_full_confidence(self):
        """No actionable content (chit-chat) → confidence 1.0."""
        extracted = {"tool_calls": [], "classification": "chit_chat"}
        # We test the static method directly without a DB session
        conf = ExtractionOrchestrator._compute_confidence(None, extracted)  # type: ignore
        assert conf == 1.0

    def test_single_draft_item_uses_its_confidence(self):
        extracted = {
            "tool_calls": [
                {
                    "name": "create_draft_item",
                    "arguments": {"title": "Leo pickup", "category": "kids_logistics", "confidence": 0.95},
                }
            ]
        }
        conf = ExtractionOrchestrator._compute_confidence(None, extracted)  # type: ignore
        assert conf == 0.95

    def test_multiple_items_uses_minimum(self):
        """With multiple items, confidence is the minimum — the weakest link."""
        extracted = {
            "tool_calls": [
                {
                    "name": "create_draft_item",
                    "arguments": {"title": "A", "category": "kids_logistics", "confidence": 0.95},
                },
                {
                    "name": "create_draft_item",
                    "arguments": {"title": "B", "category": "parent_care", "confidence": 0.70},
                },
            ]
        }
        conf = ExtractionOrchestrator._compute_confidence(None, extracted)  # type: ignore
        assert conf == 0.70

    def test_non_item_tool_calls_are_high_confidence(self):
        """request_clarification, escalate_to_ops etc. are high-confidence by nature."""
        extracted = {
            "tool_calls": [
                {
                    "name": "request_clarification",
                    "arguments": {"question_text": "Is this for Leo or Mia?"},
                }
            ]
        }
        conf = ExtractionOrchestrator._compute_confidence(None, extracted)  # type: ignore
        assert conf == 0.95

    def test_confidence_below_threshold_requires_review(self):
        """If confidence < 0.90 (default), extraction requires human review."""
        from app.platform.config import get_settings
        settings = get_settings()

        extracted = {
            "tool_calls": [
                {
                    "name": "create_draft_item",
                    "arguments": {"confidence": 0.75, "title": "Ambiguous", "category": "household_admin"},
                }
            ]
        }
        conf = ExtractionOrchestrator._compute_confidence(None, extracted)  # type: ignore
        assert conf < settings.confidence_threshold
