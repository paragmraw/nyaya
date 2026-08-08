"""Tests for the ingestion CLI (``nyaya.scripts.ingest_cli``) and ``IngestDB``.

These tests do not require a live Postgres connection. They verify the CLI
surface (help, exit codes, subcommands) and the ``IngestDB`` table-name
allowlist in ``upsert_embedding``. The DB connection methods themselves are
not exercised (they need a real Postgres).
"""

from __future__ import annotations

import sys

import pytest

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_ingest_cli_main_exists_and_callable():
    """``ingest_cli.main`` is defined and callable."""
    from nyaya.scripts import ingest_cli

    assert callable(ingest_cli.main)


def test_ingest_cli_main_is_entry_point():
    """The module-level ``main`` matches the pyproject entry point target."""
    import nyaya.scripts.ingest_cli as ic

    assert ic.main.__name__ == "main"


def test_ingest_db_class_exists():
    """The IngestDB class is importable from nyaya.scripts.db."""
    from nyaya.scripts.db import IngestDB

    assert isinstance(IngestDB, type)


def test_ingest_db_has_expected_methods():
    """IngestDB exposes the upsert/commit/close surface used by ingestion scripts."""
    from nyaya.scripts.db import IngestDB

    expected = {
        "connect",
        "close",
        "apply_schema",
        "upsert_act",
        "upsert_chapter",
        "upsert_section",
        "upsert_article",
        "upsert_schedule",
        "upsert_amendment",
        "upsert_judgment",
        "add_cross_ref",
        "upsert_embedding",
        "fetch_all",
        "commit",
        "counts",
        "print_counts",
    }
    actual = set(dir(IngestDB))
    missing = expected - actual
    assert not missing, f"IngestDB missing methods: {missing}"


def test_ingest_db_context_manager_protocol():
    """IngestDB implements __enter__/__exit__ for use as a context manager."""
    from nyaya.scripts.db import IngestDB

    assert hasattr(IngestDB, "__enter__")
    assert hasattr(IngestDB, "__exit__")


# ---------------------------------------------------------------------------
# upsert_embedding — table-name allowlist (SQL-injection guard)
# ---------------------------------------------------------------------------

def _make_ingest_db_without_conn() -> object:
    """Build an IngestDB instance whose connection is a stub cursor factory.

    The allowlist check fires before any SQL is executed, so a working cursor
    is only needed for the *valid* table names. We provide one for both.
    """
    from nyaya.scripts.db import IngestDB

    instance = IngestDB.__new__(IngestDB)
    instance._database_url = "postgresql://nobody@nowhere/db"

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            self.last_sql = sql
            self.last_params = params

    class _FakeConn:
        def cursor(self):
            return _Cursor()

    instance._conn = _FakeConn()
    return instance


def test_upsert_embedding_allows_section():
    """'section' is on the allowlist and passes the guard."""
    db = _make_ingest_db_without_conn()
    db.upsert_embedding(table="section", owner_id="1", embedding=[0.1] * 1024)


def test_upsert_embedding_allows_article():
    """'article' is on the allowlist."""
    db = _make_ingest_db_without_conn()
    db.upsert_embedding(table="article", owner_id="1", embedding=[0.1] * 1024)


def test_upsert_embedding_allows_judgment():
    """'judgment' is on the allowlist."""
    db = _make_ingest_db_without_conn()
    db.upsert_embedding(table="judgment", owner_id="1", embedding=[0.1] * 1024)


def test_upsert_embedding_rejects_unknown_table():
    """A table name not in the allowlist raises ValueError (SQL-injection guard)."""
    db = _make_ingest_db_without_conn()
    with pytest.raises(ValueError):
        db.upsert_embedding(
            table="malicious; DROP TABLE acts;--",
            owner_id="1",
            embedding=[0.1] * 1024,
        )


def test_upsert_embedding_rejects_empty_table():
    """Empty string is rejected by the allowlist."""
    from nyaya.scripts.db import IngestDB

    instance = IngestDB.__new__(IngestDB)
    instance._database_url = "postgresql://nobody@nowhere/db"
    instance._conn = None  # The guard fires before any DB access.
    with pytest.raises(ValueError):
        instance.upsert_embedding(table="", owner_id="1", embedding=[0.1] * 1024)


def test_upsert_embedding_rejects_plural_variants():
    """'sections' (plural) is rejected — only singular names are allowed."""
    db = _make_ingest_db_without_conn()
    with pytest.raises(ValueError):
        db.upsert_embedding(table="sections", owner_id="1", embedding=[0.1] * 1024)


def test_upsert_embedding_rejects_uppercase():
    """The allowlist is case-sensitive; 'Section' is rejected."""
    db = _make_ingest_db_without_conn()
    with pytest.raises(ValueError):
        db.upsert_embedding(table="Section", owner_id="1", embedding=[0.1] * 1024)


def test_upsert_embedding_error_message_lists_allowed_tables():
    """The ValueError message names the allowed tables (a debugging aid)."""
    db = _make_ingest_db_without_conn()
    with pytest.raises(ValueError) as exc_info:
        db.upsert_embedding(table="bogus", owner_id="1", embedding=[0.1] * 1024)
    msg = str(exc_info.value)
    assert "section" in msg
    assert "article" in msg
    assert "judgment" in msg


def test_upsert_embedding_allowlist_is_exact():
    """The allowlist must contain exactly {section, article, judgment}."""
    import inspect

    from nyaya.scripts.db import IngestDB

    source = inspect.getsource(IngestDB.upsert_embedding)
    # The guard checks membership in the exact set of three.
    assert '"section", "article", "judgment"' in source, (
        "upsert_embedding must guard table against the section/article/judgment allowlist"
    )


# ---------------------------------------------------------------------------
# CLI behavior — no args prints usage and exits 2 (argparse behavior)
# ---------------------------------------------------------------------------

def test_cli_no_args_exits_2(monkeypatch, capsys):
    """``main()`` with no args (argparse subparser required=True) exits with
    code 2 (argparse's standard exit code for argument errors) and prints
    usage to stderr."""
    from nyaya.scripts import ingest_cli

    monkeypatch.setattr(sys, "argv", ["nyaya-ingest"])
    with pytest.raises(SystemExit) as exc_info:
        ingest_cli.main()
    assert exc_info.value.code == 2


def test_cli_no_args_prints_usage(monkeypatch, capsys):
    """With no args, argparse prints a usage line to stderr."""
    from nyaya.scripts import ingest_cli

    monkeypatch.setattr(sys, "argv", ["nyaya-ingest"])
    with pytest.raises(SystemExit):
        ingest_cli.main()
    captured = capsys.readouterr()
    # argparse prints usage to stderr; the usage line references the prog name.
    assert "nyaya-ingest" in captured.err
    assert "command" in (captured.err + captured.out)


def test_cli_unknown_command_exits_2(monkeypatch, capsys):
    """An unrecognized command exits with code 2 (argparse rejects unknown
    subcommands)."""
    from nyaya.scripts import ingest_cli

    monkeypatch.setattr(sys, "argv", ["nyaya-ingest", "bogus-command"])
    with pytest.raises(SystemExit) as exc_info:
        ingest_cli.main()
    assert exc_info.value.code == 2


def test_cli_help_flag_exits_0(monkeypatch, capsys):
    """``--help`` exits with code 0 and prints the help text."""
    from nyaya.scripts import ingest_cli

    monkeypatch.setattr(sys, "argv", ["nyaya-ingest", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        ingest_cli.main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "nyaya-ingest" in captured.out


def test_cli_argv_restored_after_test(monkeypatch):
    """Sanity: monkeypatch restores sys.argv after the test."""
    original = list(sys.argv)
    monkeypatch.setattr(sys, "argv", ["nyaya-ingest", "counts"])
    assert sys.argv != original


# ---------------------------------------------------------------------------
# CLI docstring lists all subcommands
# ---------------------------------------------------------------------------

def test_cli_docstring_lists_all_subcommands():
    """The module docstring (used as the help text) lists every subcommand."""
    from nyaya.scripts import ingest_cli

    doc = ingest_cli.__doc__ or ""
    # Every subcommand dispatched in _run_command must appear in the docstring.
    expected_subcommands = [
        "schema",
        "constitution",
        "bare-acts",
        "civictech",
        "sanhitas",
        "judgments",
        "cross-refs",
        "embeddings",
        "all",
        "counts",
    ]
    for cmd in expected_subcommands:
        assert cmd in doc, f"CLI docstring missing subcommand {cmd!r}"


def test_cli_docstring_is_nonempty():
    """The CLI module has a meaningful docstring (rendered as --help)."""
    from nyaya.scripts import ingest_cli

    assert ingest_cli.__doc__ is not None
    assert len(ingest_cli.__doc__.strip()) > 50


def test_cli_has_build_parser():
    """The CLI uses an argparse parser (the modern surface)."""
    from nyaya.scripts import ingest_cli

    assert hasattr(ingest_cli, "_build_parser")
    parser = ingest_cli._build_parser()
    # The parser exposes subcommands via add_subparsers.
    assert parser.prog == "nyaya-ingest"


def test_cli_main_inline_dispatch():
    """The CLI's main() contains the dispatch logic inline (testable via source)."""
    import inspect

    from nyaya.scripts import ingest_cli

    source = inspect.getsource(ingest_cli.main)
    for cmd in ("schema", "constitution", "bare_acts", "civictech", "sanhitas",
                "judgments", "cross_refs", "embeddings", "all", "counts"):
        assert cmd in source, f"main() source missing dispatch for {cmd!r}"


def test_cli_subcommands_registered_in_parser():
    """Every expected subcommand is registered in the argparse parser."""
    from nyaya.scripts import ingest_cli

    parser = ingest_cli._build_parser()
    # argparse stores subparsers in a choices dict on the subparsers action.
    subparsers_action = next(
        a for a in parser._actions if hasattr(a, "choices") and a.choices
    )
    registered = set(subparsers_action.choices.keys())
    expected = {
        "schema",
        "constitution",
        "bare-acts",
        "civictech",
        "sanhitas",
        "judgments",
        "cross-refs",
        "embeddings",
        "all",
        "counts",
    }
    assert expected.issubset(registered), (
        f"parser missing subcommands: {expected - registered}"
    )


def test_cli_subcommands_accept_hyphen_form():
    """Subcommands are registered with hyphens (e.g. 'bare-acts', 'cross-refs')
    and main normalizes them via ``args.command.replace('-', '_')``."""
    import inspect

    from nyaya.scripts import ingest_cli

    # main must normalize hyphens to underscores before dispatch.
    source = inspect.getsource(ingest_cli.main)
    assert 'replace("-", "_")' in source or "replace('-', '_')" in source, (
        "main() should normalize hyphenated commands to underscores"
    )


# ---------------------------------------------------------------------------
# Ingest modules importable (no syntax errors)
# ---------------------------------------------------------------------------

def test_ingest_modules_importable():
    """Each ingest_* module referenced by the CLI is importable (no syntax errors)."""
    import importlib

    modules = [
        "nyaya.scripts.ingest_constitution",
        "nyaya.scripts.ingest_bare_acts",
        "nyaya.scripts.ingest_civictech",
        "nyaya.scripts.ingest_sanhitas",
        "nyaya.scripts.ingest_judgments",
        "nyaya.scripts.build_cross_refs",
        "nyaya.scripts.build_embeddings",
    ]
    for mod_name in modules:
        importlib.import_module(mod_name)


def test_ingest_cli_module_importable():
    """The CLI module itself is importable."""
    import importlib

    importlib.import_module("nyaya.scripts.ingest_cli")


def test_ingest_db_module_importable():
    """The IngestDB module is importable."""
    import importlib

    mod = importlib.import_module("nyaya.scripts.db")
    assert hasattr(mod, "IngestDB")
