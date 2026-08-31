"""Tests for nyaya_chat.llm — model caching, retry backoff."""

from __future__ import annotations

import pytest


def test_get_model_caches_instance(monkeypatch):
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    calls: list = []

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    m1 = llm.get_model()
    m2 = llm.get_model()
    assert m1 is m2


def test_reset_model_cache_clears(monkeypatch):
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            pass

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    m1 = llm.get_model()
    llm.reset_model_cache()
    m2 = llm.get_model()
    assert m1 is not m2


# ---------------------------------------------------------------------------
# Retry / backoff tests
# ---------------------------------------------------------------------------

class _RetryableError(Exception):
    def __init__(self, status_code=429):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class _NonRetryableError(Exception):
    pass


class _FlakyModel:
    """A model that fails N times with a retryable error, then succeeds."""

    def __init__(self, fail_times: int, result: str = "ok", status_code: int = 429):
        self.fail_times = fail_times
        self.result = result
        self.status_code = status_code
        self.invoke_count = 0

    async def ainvoke(self, messages, **kw):
        self.invoke_count += 1
        if self.invoke_count <= self.fail_times:
            raise _RetryableError(status_code=self.status_code)
        from langchain_core.messages import AIMessage
        return AIMessage(content=self.result)


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_errors(monkeypatch):
    from nyaya_chat.llm import ainvoke_with_retry
    model = _FlakyModel(fail_times=2)
    async def _noop_sleep(_delay):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    result = await ainvoke_with_retry(model, [], max_retries=4, base_delay=0.01)
    assert result.content == "ok"
    assert model.invoke_count == 3


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_retries(monkeypatch):
    from nyaya_chat.llm import ainvoke_with_retry
    model = _FlakyModel(fail_times=10)
    async def _noop_sleep(_delay):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    with pytest.raises(_RetryableError):
        await ainvoke_with_retry(model, [], max_retries=2, base_delay=0.01)
    assert model.invoke_count == 3


@pytest.mark.asyncio
async def test_non_retryable_error_raised_immediately(monkeypatch):
    from nyaya_chat.llm import ainvoke_with_retry

    class _BoomModel:
        async def ainvoke(self, messages, **kw):
            raise _NonRetryableError("bad request")

    async def _noop_sleep(_delay):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    with pytest.raises(_NonRetryableError):
        await ainvoke_with_retry(_BoomModel(), [], max_retries=4, base_delay=0.01)


@pytest.mark.asyncio
async def test_retry_on_500_error(monkeypatch):
    from nyaya_chat.llm import ainvoke_with_retry
    model = _FlakyModel(fail_times=1, status_code=503)
    async def _noop_sleep(_delay):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    result = await ainvoke_with_retry(model, [], max_retries=3, base_delay=0.01)
    assert result.content == "ok"


class _FlakyStreamModel:
    """A streaming model that raises retryable errors at scripted points.

    ``fail_before`` makes the first N stream attempts fail before yielding
    anything; ``chunks_before_error`` makes every attempt yield those chunks
    and then fail mid-stream.
    """

    def __init__(self, fail_before: int = 0, chunks_before_error: list[str] | None = None):
        self.fail_before = fail_before
        self.chunks_before_error = list(chunks_before_error or [])
        self.stream_count = 0

    async def astream(self, messages, **kw):
        from langchain_core.messages import AIMessageChunk
        self.stream_count += 1
        if self.stream_count <= self.fail_before:
            raise _RetryableError()
        if self.chunks_before_error:
            for c in self.chunks_before_error:
                yield AIMessageChunk(content=c)
            raise _RetryableError()
        yield AIMessageChunk(content="ok")


@pytest.mark.asyncio
async def test_astream_retry_before_first_chunk_succeeds(monkeypatch):
    """A retryable error BEFORE any chunk is yielded still retries and succeeds."""
    from nyaya_chat.llm import astream_with_retry
    model = _FlakyStreamModel(fail_before=2)
    async def _noop_sleep(_delay):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    chunks = [
        c async for c in astream_with_retry(model, [], max_retries=4, base_delay=0.01)
    ]
    assert "".join(c.content for c in chunks) == "ok"
    assert model.stream_count == 3


@pytest.mark.asyncio
async def test_astream_mid_stream_error_does_not_duplicate_output(monkeypatch):
    """Regression: a retryable error AFTER the first chunk must propagate.

    Chunks already yielded cannot be retracted, so restarting the stream
    would duplicate the partial answer (both in the accumulated text and on
    the SSE wire). The error must surface over the partial output instead.
    """
    from nyaya_chat.llm import astream_with_retry
    model = _FlakyStreamModel(chunks_before_error=["partial answer"])
    async def _noop_sleep(_delay):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    chunks = []
    with pytest.raises(_RetryableError):
        async for c in astream_with_retry(model, [], max_retries=4, base_delay=0.01):
            chunks.append(c)
    text = "".join(c.content for c in chunks)
    assert text == "partial answer"  # the pre-error chunk exactly once, not doubled
    assert model.stream_count == 1  # no restart attempted


def test_is_retryable_429():
    from nyaya_chat.llm import _is_retryable
    assert _is_retryable(_RetryableError(status_code=429)) is True


def test_is_retryable_500():
    from nyaya_chat.llm import _is_retryable
    assert _is_retryable(_RetryableError(status_code=503)) is True


def test_is_not_retryable_400():
    from nyaya_chat.llm import _is_retryable
    assert _is_retryable(_RetryableError(status_code=400)) is False


def test_is_not_retryable_non_http():
    from nyaya_chat.llm import _is_retryable
    assert _is_retryable(_NonRetryableError("bad")) is False


def test_is_retryable_rate_limit_in_message():
    from nyaya_chat.llm import _is_retryable
    assert _is_retryable(Exception("rate limit exceeded")) is True
    assert _is_retryable(Exception("server overloaded")) is True
