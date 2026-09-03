"""Unit tests for embeddings.py: shared lazy API clients and the overall
rerank deadline.

The reranker deadline test uses a fake client (in place of the module-level
httpx singleton) that stalls past the deadline, and asserts the call gives up
within the deadline instead of hanging for 3 attempts x 120s + backoff.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from nyaya import embeddings
from nyaya.exceptions import EmbeddingUnavailable


class _FakeRerankResponse:
    """Minimal httpx.Response stand-in for a successful rerank call."""

    def __init__(self, rankings: list[dict[str, Any]] | None = None) -> None:
        self._rankings = rankings or [
            {"index": 0, "logit": 1.5}, {"index": 1, "logit": 0.5},
        ]

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"rankings": self._rankings}


class _FakeRerankClient:
    """Stand-in for the shared httpx client: records post() calls.

    Fails with ``failure`` (after stalling ``stall_s`` seconds if set) unless
    a canned ``response`` is provided.
    """

    def __init__(
        self,
        response: Any = None,
        failure: Exception | None = None,
        stall_s: float = 0.0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response if response is not None else _FakeRerankResponse()
        self._failure = failure
        self._stall_s = stall_s

    def post(self, url: str, json: Any = None, timeout: float | None = None) -> Any:
        self.calls.append({"url": url, "timeout": timeout})
        if self._stall_s:
            time.sleep(self._stall_s)
        if self._failure is not None:
            raise self._failure
        return self._response


# ---------------------------------------------------------------------------
# Item 1a — lazy shared clients
# ---------------------------------------------------------------------------

def test_openai_client_is_shared(monkeypatch):
    """_get_openai_client builds once and returns the same instance after."""
    monkeypatch.setattr(embeddings, "_openai_client", None)
    first = embeddings._get_openai_client()
    second = embeddings._get_openai_client()
    assert first is not None
    assert first is second


def test_http_client_is_shared(monkeypatch):
    """_get_http_client builds one long-lived httpx.Client."""
    monkeypatch.setattr(embeddings, "_http_client", None)
    first = embeddings._get_http_client()
    second = embeddings._get_http_client()
    assert first is second
    assert isinstance(first, httpx.Client)


# ---------------------------------------------------------------------------
# Item 1b — overall rerank deadline
# ---------------------------------------------------------------------------

def test_rerank_gives_up_within_deadline(monkeypatch):
    """A reranker that stalls past the deadline fails within it (not 120s).

    With a stalled fake client, the deadline (not the 120s historic httpx
    timeout or the 3-attempt retry loop) must govern total wall-clock time.
    """
    monkeypatch.setattr(embeddings, "RERANK_DEADLINE_S", 1.0)
    fake = _FakeRerankClient(failure=httpx.ConnectError("stalled"), stall_s=0.4)
    monkeypatch.setattr(embeddings, "_get_http_client", lambda: fake)
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)

    start = time.monotonic()
    with pytest.raises(EmbeddingUnavailable, match="deadline"):
        embeddings.rerank_query("punishment for murder", ["a", "b"])
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"rerank took {elapsed:.2f}s; deadline not enforced"
    assert len(fake.calls) == 3  # attempts happen but are bounded by the deadline
    for entry in fake.calls:
        assert 0 < entry["timeout"] <= 1.0  # per-attempt timeout <= the deadline


def test_rerank_retries_within_deadline(monkeypatch):
    """A transient first-attempt failure that fits the deadline still retries."""
    # First call fails, second succeeds: a side-effect list drives that.
    responses = [httpx.ConnectError("transient"), _FakeRerankResponse()]
    calls: list[dict[str, Any]] = []

    class _Flaky:
        def post(self, url: str, json: Any = None, timeout: float | None = None) -> Any:
            calls.append({"url": url, "timeout": timeout})
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    flaky_client = _Flaky()
    monkeypatch.setattr(embeddings, "_get_http_client", lambda: flaky_client)
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)

    scores = embeddings.rerank_query("murder", ["a", "b"])
    assert scores == [1.5, 0.5]
    assert len(calls) == 2


def test_rerank_success_uses_shared_client(monkeypatch):
    """Happy path: shared client, NVIDIA rerank URL, index-sorted logits."""
    fake = _FakeRerankClient(
        response=_FakeRerankResponse(
            rankings=[{"index": 1, "logit": 0.25}, {"index": 0, "logit": 2.0}]
        )
    )
    monkeypatch.setattr(embeddings, "_get_http_client", lambda: fake)
    scores = embeddings.rerank_query("murder", ["a", "b"])
    assert scores == [2.0, 0.25]
    assert fake.calls[0]["url"].startswith("https://ai.api.nvidia.com/v1/retrieval/nvidia/")
    assert fake.calls[0]["timeout"] <= embeddings.RERANK_DEADLINE_S


def test_empty_candidates_short_circuits(monkeypatch):
    """No candidates means no HTTP call at all."""
    fake = _FakeRerankClient(failure=httpx.ConnectError("boom"))
    monkeypatch.setattr(embeddings, "_get_http_client", lambda: fake)
    assert embeddings.rerank_query("q", []) == []
    assert fake.calls == []
