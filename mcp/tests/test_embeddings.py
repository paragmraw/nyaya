"""Unit tests for ``nyaya.embeddings``.

These tests do not require fastembed to be installed, the model to be
downloaded, or a network connection. We monkeypatch the internal ``_model``
helper to inject canned vectors and to simulate the "fastembed missing"
condition. The real ``embed_query`` calls ``.tolist()`` on each yielded
embedding (fastembed returns numpy arrays), so our fake model yields
``_Vec`` objects that implement ``.tolist()``.
"""

from __future__ import annotations

import pytest

from nyaya import embeddings
from nyaya.exceptions import EmbeddingUnavailable, SearchError


class _Vec:
    """Stand-in for a numpy array yielded by fastembed's ``TextEmbedding.embed``."""

    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values

    def __len__(self) -> int:
        return len(self._values)


class _FakeModel:
    """Fake fastembed model that yields ``_Vec`` objects from ``embed()``."""

    def __init__(self, dim: int = 1024, count: int | None = None) -> None:
        # count=None means "yield one vec per input"; an int forces exactly
        # that many yields regardless of input length.
        self._dim = dim
        self._count = count
        self.seen: list[str] = []

    def embed(self, texts):
        self.seen = list(texts)
        n = len(texts) if self._count is None else self._count
        for _ in range(n):
            yield _Vec([0.0] * self._dim)


def _reset_caches() -> None:
    """Clear the model and query caches so tests don't leak state."""
    try:
        embeddings._model_cache.clear()
    except AttributeError:
        pass
    try:
        embeddings._query_cache.clear()
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# EXPECTED_DIM
# ---------------------------------------------------------------------------

def test_expected_dim_is_1024():
    """The pgvector columns are 1024-d; the default model must match."""
    assert embeddings.EXPECTED_DIM == 1024


# ---------------------------------------------------------------------------
# embed_query — input validation
# ---------------------------------------------------------------------------

def test_embed_query_empty_raises_search_error():
    """An empty query is a client error -> SearchError (not EmbeddingUnavailable)."""
    _reset_caches()
    with pytest.raises(SearchError):
        embeddings.embed_query("")


def test_embed_query_whitespace_raises_search_error():
    """A whitespace-only query is treated as empty and rejected."""
    _reset_caches()
    with pytest.raises(SearchError):
        embeddings.embed_query("   \n\t  ")


def test_embed_query_empty_repeated_still_raises():
    """A second call with an empty query still raises (cachetools.cached does
    not cache exceptions, so the guard re-runs)."""
    _reset_caches()
    with pytest.raises(SearchError):
        embeddings.embed_query("")
    with pytest.raises(SearchError):
        embeddings.embed_query("")


# ---------------------------------------------------------------------------
# embed_query — fastembed missing
# ---------------------------------------------------------------------------

def test_embed_query_fastembed_missing_raises_embedding_unavailable(monkeypatch):
    """If fastembed cannot be imported, embed_query surfaces EmbeddingUnavailable."""

    def _boom():
        raise EmbeddingUnavailable("simulated: fastembed not installed")

    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", _boom)
    with pytest.raises(EmbeddingUnavailable):
        embeddings.embed_query("right to privacy")


# ---------------------------------------------------------------------------
# embed_query — dimension validation
# ---------------------------------------------------------------------------

def test_embed_query_wrong_dimension_raises_search_error(monkeypatch):
    """A model returning a vector with the wrong dimensionality raises SearchError."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=768))
    with pytest.raises(SearchError) as exc_info:
        embeddings.embed_query("a real query")
    assert "1024" in str(exc_info.value)


def test_embed_query_correct_dimension(monkeypatch):
    """A model returning a 1024-d vector is accepted."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=1024))
    vec = embeddings.embed_query("a real query")
    assert len(vec) == 1024


def test_embed_query_returns_list_of_floats(monkeypatch):
    """The returned vector is a plain list of floats (JSON-serializable)."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=1024))
    vec = embeddings.embed_query("another query")
    assert isinstance(vec, list)
    assert all(isinstance(x, float) for x in vec)


def test_embed_query_empty_model_output_raises(monkeypatch):
    """If the model yields no vectors, embed_query raises SearchError."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=1024, count=0))
    with pytest.raises(SearchError):
        embeddings.embed_query("non-empty query")


def test_embed_query_caches_successful_results(monkeypatch):
    """A successful embedding is cached: the model is only called once for the
    same query (the second call hits the query cache)."""
    calls = {"n": 0}

    class _CountingModel:
        def embed(self, texts):
            calls["n"] += 1
            for _ in texts:
                yield _Vec([0.1] * 1024)

    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _CountingModel())
    v1 = embeddings.embed_query("cached query")
    v2 = embeddings.embed_query("cached query")
    assert v1 == v2
    assert calls["n"] == 1  # model.embed called only once


# ---------------------------------------------------------------------------
# embed_texts — batch helper
# ---------------------------------------------------------------------------

def test_embed_texts_guards_empty_strings(monkeypatch):
    """Empty/whitespace-only texts are replaced with a placeholder so fastembed
    doesn't emit a zero vector (which would pollute the embedding space)."""
    fake = _FakeModel(dim=1024)
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: fake)
    out = embeddings.embed_texts(["real text", "", "   "])
    assert len(out) == 3
    # The empty/whitespace strings were replaced with a non-empty placeholder.
    assert fake.seen[1] != ""
    assert fake.seen[2] != ""
    assert fake.seen[0] == "real text"


def test_embed_texts_validates_dimension(monkeypatch):
    """embed_texts raises SearchError if the first vector has the wrong dim."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=512))
    with pytest.raises(SearchError):
        embeddings.embed_texts(["a", "b"])


def test_embed_texts_correct_dimension(monkeypatch):
    """embed_texts returns one vector per input text when dims match."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=1024))
    out = embeddings.embed_texts(["one", "two", "three"])
    assert len(out) == 3
    assert all(len(v) == 1024 for v in out)


def test_embed_texts_empty_list_returns_empty(monkeypatch):
    """An empty input list yields an empty output list (no model call)."""
    fake = _FakeModel(dim=1024)
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: fake)
    out = embeddings.embed_texts([])
    assert out == []
    assert fake.seen == []


def test_embed_texts_no_vectors_no_dimension_check(monkeypatch):
    """If the model yields no vectors for a non-empty list, embed_texts
    returns [] without raising (the dimension guard only runs when there is
    a first vector to inspect)."""
    _reset_caches()
    monkeypatch.setattr(embeddings, "_model", lambda: _FakeModel(dim=1024, count=0))
    out = embeddings.embed_texts(["one"])
    assert out == []
