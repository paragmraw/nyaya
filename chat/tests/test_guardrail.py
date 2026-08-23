"""Tests for nyaya_chat.guardrail -- two-tier intent classification."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nyaya_chat.guardrail import (
    Intent,
    classify_intent,
    classify_intent_tier1,
    classify_intent_tier2,
    get_canned_response,
)

# ---------------------------------------------------------------------------
# Tier 1: Rule-based classifier tests
# ---------------------------------------------------------------------------

class TestTier1Greetings:
    def test_hello(self):
        assert classify_intent_tier1("hello") == Intent.GREETING

    def test_hi(self):
        assert classify_intent_tier1("hi") == Intent.GREETING

    def test_hey(self):
        assert classify_intent_tier1("hey") == Intent.GREETING

    def test_good_morning(self):
        assert classify_intent_tier1("Good morning!") == Intent.GREETING

    def test_good_evening(self):
        assert classify_intent_tier1("good evening") == Intent.GREETING

    def test_greetings(self):
        assert classify_intent_tier1("greetings") == Intent.GREETING

    def test_hi_with_punctuation(self):
        assert classify_intent_tier1("hi!") == Intent.GREETING

    def test_hello_there(self):
        assert classify_intent_tier1("hello there") == Intent.GREETING

    def test_not_greeting_with_legal_content(self):
        """A message that starts with 'hi' but has a legal question is NOT a greeting."""
        assert classify_intent_tier1("hi, what is IPC section 302?") is None


class TestTier1Capability:
    def test_what_can_you_do(self):
        assert classify_intent_tier1("what can you do?") == Intent.CAPABILITY

    def test_who_are_you(self):
        assert classify_intent_tier1("who are you?") == Intent.CAPABILITY

    def test_what_do_you_know(self):
        assert classify_intent_tier1("what do you know?") == Intent.CAPABILITY

    def test_help(self):
        assert classify_intent_tier1("help") == Intent.CAPABILITY

    def test_tell_me_about_yourself(self):
        assert classify_intent_tier1("tell me about yourself") == Intent.CAPABILITY

    def test_what_is_nyaya(self):
        assert classify_intent_tier1("what is nyaya?") == Intent.CAPABILITY

    def test_not_capability_with_legal_keyword(self):
        """Capability words but also legal keywords should NOT be classified as capability."""
        assert classify_intent_tier1("what can you tell me about IPC section 302?") is None


class TestTier1Thanks:
    def test_thanks(self):
        assert classify_intent_tier1("thanks") == Intent.THANKS

    def test_thank_you(self):
        assert classify_intent_tier1("thank you") == Intent.THANKS

    def test_great(self):
        assert classify_intent_tier1("great!") == Intent.THANKS

    def test_awesome(self):
        assert classify_intent_tier1("awesome") == Intent.THANKS

    def test_appreciate_it(self):
        assert classify_intent_tier1("appreciate it!") == Intent.THANKS


class TestTier1OffTopic:
    def test_weather(self):
        assert classify_intent_tier1("what's the weather today?") == Intent.OFF_TOPIC

    def test_joke(self):
        assert classify_intent_tier1("tell me a joke") == Intent.OFF_TOPIC

    def test_recipe(self):
        assert classify_intent_tier1("how to make biryani recipe") == Intent.OFF_TOPIC

    def test_python_code(self):
        assert classify_intent_tier1("write python code for a web server") == Intent.OFF_TOPIC

    def test_cricket_score(self):
        assert classify_intent_tier1("what's the cricket score?") == Intent.OFF_TOPIC

    def test_stock_price(self):
        assert classify_intent_tier1("bitcoin stock price") == Intent.OFF_TOPIC

    def test_us_constitution(self):
        assert classify_intent_tier1("explain the US constitution") == Intent.OFF_TOPIC

    def test_movie_recommendation(self):
        assert classify_intent_tier1("recommend a movie to watch") == Intent.OFF_TOPIC


class TestTier1Legal:
    def test_legal_section(self):
        assert classify_intent_tier1("What is IPC section 302?") is None

    def test_legal_article(self):
        assert classify_intent_tier1("What does Article 21 say?") is None

    def test_legal_semantic(self):
        assert classify_intent_tier1("What is the legal definition of good faith?") is None

    def test_legal_comparison(self):
        assert classify_intent_tier1("Compare IPC and BNS for murder") is None

    def test_legal_judgment(self):
        assert classify_intent_tier1("What was the Kesavananda Bharati case about?") is None

    def test_legal_definition(self):
        assert classify_intent_tier1("What does dishonestly mean in IPC?") is None

    def test_legal_short_number(self):
        assert classify_intent_tier1("302") is None


class TestTier1EdgeCases:
    def test_empty(self):
        assert classify_intent_tier1("") is None

    def test_whitespace_only(self):
        assert classify_intent_tier1("   ") is None

    def test_ambiguous_not_greeting(self):
        """A longer message starting with 'hey' should not be classified as greeting."""
        assert classify_intent_tier1("hey, can you help me understand criminal law?") is None


# ---------------------------------------------------------------------------
# Canned response tests
# ---------------------------------------------------------------------------

class TestCannedResponses:
    def test_greeting_response_has_hello(self):
        resp = get_canned_response(Intent.GREETING)
        assert "Hello" in resp
        assert "Indian law" in resp

    def test_capability_response_lists_features(self):
        resp = get_canned_response(Intent.CAPABILITY)
        assert "I can" in resp
        assert "IPC" in resp
        assert "Constitution" in resp
        assert "judgments" in resp

    def test_thanks_response_brief(self):
        resp = get_canned_response(Intent.THANKS)
        assert "welcome" in resp.lower()

    def test_off_topic_response_redirects_to_legal(self):
        resp = get_canned_response(Intent.OFF_TOPIC)
        assert "Indian law" in resp
        assert "can't help" in resp.lower() or "cannot help" in resp.lower()

    def test_unknown_intent_falls_back_to_off_topic(self):
        resp = get_canned_response(Intent.LEGAL)
        # LEGAL doesn't have a canned response -- falls back to off_topic
        assert "Indian law" in resp


# ---------------------------------------------------------------------------
# Tier 2: LLM classifier tests
# ---------------------------------------------------------------------------

class TestTier2Classifier:
    @pytest.mark.asyncio
    async def test_tier2_returns_legal(self, monkeypatch):
        """When the LLM says 'legal', classify as LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "legal"
        mock_model.ainvoke = AsyncMock(return_value=mock_result)

        result = await classify_intent_tier2("What is IPC 302?", mock_model, settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_tier2_returns_greeting(self, monkeypatch):
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "greeting"
        mock_model.ainvoke = AsyncMock(return_value=mock_result)

        result = await classify_intent_tier2("hey there what's up", mock_model, settings)
        assert result == Intent.GREETING

    @pytest.mark.asyncio
    async def test_tier2_returns_off_topic(self, monkeypatch):
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "off_topic"
        mock_model.ainvoke = AsyncMock(return_value=mock_result)

        result = await classify_intent_tier2("write a poem about cats", mock_model, settings)
        assert result == Intent.OFF_TOPIC

    @pytest.mark.asyncio
    async def test_tier2_timeout_fails_open(self, monkeypatch):
        """On timeout, fail open to LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 0.01  # 10ms timeout

        mock_model = MagicMock()

        async def slow_invoke(msgs):
            await asyncio.sleep(1)
            return MagicMock()

        mock_model.ainvoke = slow_invoke

        result = await classify_intent_tier2("test", mock_model, settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_tier2_error_fails_open(self, monkeypatch):
        """On any error, fail open to LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=RuntimeError("API down"))

        result = await classify_intent_tier2("test", mock_model, settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_tier2_unparseable_response_fails_open(self, monkeypatch):
        """If the LLM returns something unparseable, fail open to LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "I think this is a legal question about IPC 302."
        mock_model.ainvoke = AsyncMock(return_value=mock_result)

        result = await classify_intent_tier2("test", mock_model, settings)
        assert result == Intent.LEGAL


# ---------------------------------------------------------------------------
# Public API: classify_intent (two-tier) tests
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    @pytest.mark.asyncio
    async def test_tier1_greeting_skips_tier2(self):
        """When Tier 1 matches, Tier 2 is never called."""
        settings = MagicMock()
        settings.guardrail_enabled = True

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock()

        result = await classify_intent("hello", mock_model, settings)
        assert result == Intent.GREETING
        mock_model.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier1_off_topic_skips_tier2(self):
        settings = MagicMock()
        settings.guardrail_enabled = True

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock()

        result = await classify_intent("tell me a joke", mock_model, settings)
        assert result == Intent.OFF_TOPIC
        mock_model.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier1_unknown_falls_to_tier2(self):
        settings = MagicMock()
        settings.guardrail_enabled = True
        settings.guardrail_classifier_timeout_s = 10.0

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "legal"
        mock_model.ainvoke = AsyncMock(return_value=mock_result)

        result = await classify_intent("What is IPC 302?", mock_model, settings)
        assert result == Intent.LEGAL
        mock_model.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_guardrail_disabled_returns_legal(self):
        """When guardrail_enabled is False, always return LEGAL."""
        settings = MagicMock()
        settings.guardrail_enabled = False

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock()

        result = await classify_intent("hello", mock_model, settings)
        assert result == Intent.LEGAL
        mock_model.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_guardrail_disabled_for_legal_question(self):
        """Even with guardrail disabled, legal questions are still LEGAL."""
        settings = MagicMock()
        settings.guardrail_enabled = False

        mock_model = MagicMock()
        result = await classify_intent("What is IPC 302?", mock_model, settings)
        assert result == Intent.LEGAL
