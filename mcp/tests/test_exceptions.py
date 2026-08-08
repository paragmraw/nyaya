"""Unit tests for ``nyaya.exceptions``.

Verifies the error hierarchy: stable ``code`` attributes, the ``NotFound.kind``
field, and that every concrete exception is catchable via the ``NyayaError`` base.
"""

from __future__ import annotations

import pytest

from nyaya.exceptions import (
    ConfigurationError,
    DatabaseUnavailable,
    EmbeddingUnavailable,
    NotFound,
    NyayaError,
    SearchError,
)


def test_nyaya_error_has_code_and_hint_attributes():
    """The base class exposes ``code`` and ``hint`` as class-level attributes."""
    assert NyayaError.code == "nyaya_error"
    assert NyayaError.hint is None


def test_nyaya_error_message_set():
    """The message passed to the constructor is stored on the instance."""
    err = NyayaError("boom")
    assert err.message == "boom"
    assert str(err) == "boom"


def test_nyaya_error_hint_settable():
    """The hint kwarg is stored on the instance when provided."""
    err = NyayaError("boom", hint="try again")
    assert err.hint == "try again"


def test_nyaya_error_hint_defaults_none():
    """Without a hint kwarg, hint is None."""
    err = NyayaError("boom")
    assert err.hint is None


def test_not_found_default_code():
    """NotFound carries the stable 'not_found' code."""
    assert NotFound.code == "not_found"


def test_not_found_kind_attribute():
    """NotFound exposes a ``kind`` field distinguishing what was missing."""
    err = NotFound("missing", kind="section")
    assert err.kind == "section"


def test_not_found_kind_default():
    """The default kind is 'unknown' when not specified."""
    err = NotFound("missing")
    assert err.kind == "unknown"


def test_not_found_hint_settable_via_constructor():
    """NotFound accepts a hint via the constructor (inherited from NyayaError)."""
    err = NotFound("missing section", kind="section", hint="try search_law")
    assert err.hint == "try search_law"
    assert err.kind == "section"


def test_database_unavailable_code():
    """DatabaseUnavailable carries the 'database_unavailable' code."""
    assert DatabaseUnavailable.code == "database_unavailable"


def test_search_error_code():
    """SearchError carries the 'search_error' code."""
    assert SearchError.code == "search_error"


def test_configuration_error_code():
    """ConfigurationError carries the 'configuration_error' code."""
    assert ConfigurationError.code == "configuration_error"


def test_embedding_unavailable_code():
    """EmbeddingUnavailable carries the 'embedding_unavailable' code."""
    assert EmbeddingUnavailable.code == "embedding_unavailable"


@pytest.mark.parametrize(
    "exc_cls,args",
    [
        (NotFound, ("missing",)),
        (DatabaseUnavailable, ("db down",)),
        (SearchError, ("bad query",)),
        (ConfigurationError, ("bad env",)),
        (EmbeddingUnavailable, ("no fastembed",)),
    ],
)
def test_all_exceptions_catchable_as_nyaya_error(exc_cls, args):
    """Every concrete exception is a subclass of NyayaError and catchable as such."""
    with pytest.raises(NyayaError) as exc_info:
        raise exc_cls(*args)
    assert isinstance(exc_info.value, NyayaError)
    # The code attribute survives the raise.
    assert exc_info.value.code


def test_not_found_is_nyaya_error():
    """NotFound instances are NyayaError instances."""
    assert isinstance(NotFound("x"), NyayaError)


def test_exception_codes_are_unique():
    """Each concrete exception has a distinct code (clients branch on this)."""
    codes = {
        NyayaError.code,
        NotFound.code,
        DatabaseUnavailable.code,
        SearchError.code,
        ConfigurationError.code,
        EmbeddingUnavailable.code,
    }
    assert len(codes) == 6, "Error codes must be unique across the hierarchy"


def test_exception_message_preserved_in_str():
    """The message is accessible via str() (inherits from Exception)."""
    err = NotFound("section 999 not found", kind="section")
    assert "section 999" in str(err)


def test_database_unavailable_hint_inherited():
    """DatabaseUnavailable accepts the hint kwarg from NyayaError."""
    err = DatabaseUnavailable("db down", hint="check DATABASE_URL")
    assert err.hint == "check DATABASE_URL"
    assert err.code == "database_unavailable"
