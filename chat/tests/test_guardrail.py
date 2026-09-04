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


class TestTier1RegexDoS:
    """Trailing whitespace/punctuation runs must not trigger polynomial
    backtracking (CodeQL py/polynomial-redos, alerts #14/#15)."""

    def test_trailing_whitespace_run_greeting(self):
        assert classify_intent_tier1("hi" + " " * 10_000) == Intent.GREETING

    def test_trailing_whitespace_run_thanks(self):
        assert classify_intent_tier1("ty" + " " * 10_000) == Intent.THANKS

    def test_trailing_punctuation_whitespace_mix(self):
        assert classify_intent_tier1("thanks" + "! " * 100) == Intent.THANKS

    def test_long_whitespace_input_is_fast(self):
        import time

        message = "ty" + " " * 100_000
        start = time.perf_counter()
        classify_intent_tier1(message)
        elapsed = time.perf_counter() - start
        # Polynomial backtracking on the old pattern would take far longer.
        assert elapsed < 0.5, f"tier 1 took {elapsed:.2f}s on whitespace run"
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
        """When the LLM returns Intent.LEGAL via structured output."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=Intent.LEGAL)
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent_tier2("What is IPC 302?", settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_tier2_returns_greeting(self, monkeypatch):
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=Intent.GREETING)
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent_tier2("hey there what's up", settings)
        assert result == Intent.GREETING

    @pytest.mark.asyncio
    async def test_tier2_returns_off_topic(self, monkeypatch):
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=Intent.OFF_TOPIC)
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent_tier2("write a poem about cats", settings)
        assert result == Intent.OFF_TOPIC

    @pytest.mark.asyncio
    async def test_tier2_timeout_fails_open(self, monkeypatch):
        """On timeout, fail open to LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 0.01  # 10ms timeout
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()

        async def slow_invoke(msgs):
            await asyncio.sleep(1)
            return Intent.LEGAL

        mock_structured.ainvoke = slow_invoke
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent_tier2("test", settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_tier2_error_fails_open(self, monkeypatch):
        """On any error, fail open to LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(side_effect=RuntimeError("API down"))
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent_tier2("test", settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_tier2_none_response_fails_open(self, monkeypatch):
        """If the model returns None (incomplete response), fail open to LEGAL."""
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=None)
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent_tier2("test", settings)
        assert result == Intent.LEGAL


class TestClassifierClientReuse:
    """The Tier-2 classifier client is built ONCE and reused across requests."""

    @staticmethod
    def _settings():
        settings = MagicMock()
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")
        return settings

    @staticmethod
    def _fake_classifier(intent_result):
        """A stand-in classifier client whose structured output returns intent_result."""
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=intent_result)
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)
        return mock_model

    @pytest.mark.asyncio
    async def test_two_classifications_build_one_client(self, monkeypatch):
        """N Tier-2 classifications through the public path construct ONE client."""
        from nyaya_chat import guardrail as guard_mod

        built = []

        def _counting_builder(_settings):
            client = self._fake_classifier(Intent.LEGAL)
            built.append(client)
            return client

        monkeypatch.setattr(guard_mod, "_build_classifier_model", _counting_builder)
        settings = self._settings()

        first = await classify_intent_tier2("hey there what's up", settings)
        second = await guard_mod.classify_intent_tier2(
            "so what do you make of that", settings
        )
        assert first == Intent.LEGAL
        assert second == Intent.LEGAL
        assert len(built) == 1  # one construction for N (2) calls
        assert guard_mod.get_classifier_model(settings) is built[0]

    def test_distinct_settings_build_distinct_clients(self, monkeypatch):
        """A changed/reloaded configuration is honoured, not served a stale client."""
        from nyaya_chat import guardrail as guard_mod

        count = {"n": 0}

        def _builder(_s):
            count["n"] += 1
            return self._fake_classifier(Intent.OFF_TOPIC)

        monkeypatch.setattr(guard_mod, "_build_classifier_model", _builder)
        s1, s2 = self._settings(), self._settings()
        m1 = guard_mod.get_classifier_model(s1)
        m2 = guard_mod.get_classifier_model(s2)
        assert m1 is not m2
        assert guard_mod.get_classifier_model(s1) is m1  # stable within one Settings
        assert count["n"] == 2

    def test_reset_classifier_cache_forces_rebuild(self, monkeypatch):
        """reset_classifier_cache drops the cached client; the next call rebuilds."""
        from nyaya_chat import guardrail as guard_mod

        count = {"n": 0}

        def _builder(_s):
            count["n"] += 1
            return self._fake_classifier(Intent.LEGAL)

        monkeypatch.setattr(guard_mod, "_build_classifier_model", _builder)
        settings = self._settings()
        guard_mod.get_classifier_model(settings)
        guard_mod.get_classifier_model(settings)
        assert count["n"] == 1
        guard_mod.reset_classifier_cache()
        guard_mod.get_classifier_model(settings)
        assert count["n"] == 2


# ---------------------------------------------------------------------------
# Public API: classify_intent (two-tier) tests
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    @pytest.mark.asyncio
    async def test_tier1_greeting_skips_tier2(self, monkeypatch):
        """When Tier 1 matches, Tier 2 is never called."""

        async def _tier2_must_not_run(message, settings):
            raise AssertionError("tier 2 must not be called when tier 1 matches")

        monkeypatch.setattr("nyaya_chat.guardrail.classify_intent_tier2", _tier2_must_not_run)
        settings = MagicMock()
        settings.guardrail_enabled = True

        result = await classify_intent("hello", settings)
        assert result == Intent.GREETING

    @pytest.mark.asyncio
    async def test_tier1_off_topic_skips_tier2(self, monkeypatch):
        async def _tier2_must_not_run(message, settings):
            raise AssertionError("tier 2 must not be called when tier 1 matches")

        monkeypatch.setattr("nyaya_chat.guardrail.classify_intent_tier2", _tier2_must_not_run)
        settings = MagicMock()
        settings.guardrail_enabled = True

        result = await classify_intent("tell me a joke", settings)
        assert result == Intent.OFF_TOPIC

    @pytest.mark.asyncio
    async def test_tier1_unknown_falls_to_tier2(self, monkeypatch):
        settings = MagicMock()
        settings.guardrail_enabled = True
        settings.guardrail_classifier_timeout_s = 10.0
        settings.guardrail_classifier_max_tokens = 32
        settings.llm_model = "nvidia/test"
        settings.llm_timeout_s = 60.0
        settings.nvidia_api_key = MagicMock()
        settings.nvidia_api_key.get_secret_value = MagicMock(return_value="test-key")

        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(return_value=Intent.LEGAL)
        mock_model = MagicMock()
        mock_model.with_structured_output = MagicMock(return_value=mock_structured)

        monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", lambda **kw: mock_model)

        result = await classify_intent("hey there what's up", settings)
        assert result == Intent.LEGAL
        mock_structured.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_guardrail_disabled_returns_legal(self, monkeypatch):
        """When guardrail_enabled is False, always return LEGAL."""

        async def _tier2_must_not_run(message, settings):
            raise AssertionError("tier 2 must not be called when the guardrail is disabled")

        monkeypatch.setattr("nyaya_chat.guardrail.classify_intent_tier2", _tier2_must_not_run)
        settings = MagicMock()
        settings.guardrail_enabled = False

        result = await classify_intent("hello", settings)
        assert result == Intent.LEGAL

    @pytest.mark.asyncio
    async def test_guardrail_disabled_for_legal_question(self):
        """Even with guardrail disabled, legal questions are still LEGAL."""
        settings = MagicMock()
        settings.guardrail_enabled = False

        result = await classify_intent("What is IPC 302?", settings)
        assert result == Intent.LEGAL
