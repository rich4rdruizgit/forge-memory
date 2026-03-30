"""Shared fixtures for forge-memory test suite.

Provides an in-memory SQLite database with migrations applied and the
module-level ``forge_memory.db._conn`` patched so that ``get_db()`` returns
the test connection without requiring ``open_db()``.
"""

from __future__ import annotations

import sqlite3

import pytest

from forge_memory.migrations import run_migrations


@pytest.fixture()
def db(monkeypatch):
    """In-memory SQLite DB with migrations applied, patched into ``forge_memory.db._conn``.

    Tool functions that call ``get_db()`` will transparently receive this
    connection.  No ``open_db()`` call is needed.
    """
    import forge_memory.db as db_module

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)

    monkeypatch.setattr(db_module, "_conn", conn)

    yield conn

    conn.close()
    monkeypatch.setattr(db_module, "_conn", None)
