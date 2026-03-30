"""Tests for forge_memory.db — database connection lifecycle."""


import pytest

import forge_memory.db as db_module
from forge_memory.config import Config
from forge_memory.db import close_db, get_db, open_db
from forge_memory.models import DatabaseError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_conn():
    """Reset the module-level _conn before and after every test."""
    db_module._conn = None
    yield
    # Teardown: close any leftover connection
    if db_module._conn is not None:
        try:
            db_module._conn.close()
        except Exception:
            pass
        db_module._conn = None


def _make_config(tmp_path, *, db_name="test.db"):
    """Build a Config pointing at a temp directory."""
    return Config(db_path=str(tmp_path / db_name))


# ---------------------------------------------------------------------------
# open_db — basic behaviour
# ---------------------------------------------------------------------------


class TestOpenDb:
    """open_db creates a working database with correct pragmas and tables."""

    def test_returns_working_connection(self, tmp_path):
        conn = open_db(_make_config(tmp_path))
        # Should be able to execute a trivial query
        result = conn.execute("SELECT 1").fetchone()
        assert result == (1,)

    def test_runs_migrations_tables_exist(self, tmp_path):
        conn = open_db(_make_config(tmp_path))
        # schema_version always exists after migrations
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "schema_version" in tables

    def test_enables_wal_mode(self, tmp_path):
        conn = open_db(_make_config(tmp_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_enables_foreign_keys(self, tmp_path):
        conn = open_db(_make_config(tmp_path))
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        cfg = Config(db_path=str(nested / "deep.db"))
        conn = open_db(cfg)
        assert nested.is_dir()
        conn.close()

    def test_in_memory_db(self, tmp_path):
        """open_db works with ':memory:' path."""
        cfg = Config(db_path=":memory:")
        conn = open_db(cfg)
        result = conn.execute("SELECT 1").fetchone()
        assert result == (1,)

    def test_stores_conn_in_module(self, tmp_path):
        conn = open_db(_make_config(tmp_path))
        assert db_module._conn is conn


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


class TestGetDb:
    """get_db returns the connection or raises if not initialised."""

    def test_returns_connection_after_open(self, tmp_path):
        expected = open_db(_make_config(tmp_path))
        assert get_db() is expected

    def test_raises_before_open(self):
        with pytest.raises(DatabaseError, match="not initialized"):
            get_db()


# ---------------------------------------------------------------------------
# close_db
# ---------------------------------------------------------------------------


class TestCloseDb:
    """close_db tears down the connection cleanly."""

    def test_sets_conn_to_none(self, tmp_path):
        open_db(_make_config(tmp_path))
        assert db_module._conn is not None
        close_db()
        assert db_module._conn is None

    def test_safe_when_already_closed(self):
        # Should not raise when _conn is already None
        close_db()
        assert db_module._conn is None

    def test_get_db_raises_after_close(self, tmp_path):
        open_db(_make_config(tmp_path))
        close_db()
        with pytest.raises(DatabaseError, match="not initialized"):
            get_db()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Error handling in open_db and _open_plain."""

    def test_open_db_migration_failure_closes_conn_and_raises(self, tmp_path):
        """Migration failure closes the connection and raises DatabaseError."""
        from unittest.mock import patch
        from forge_memory.db import open_db
        from forge_memory.models import DatabaseError

        with patch(
            "forge_memory.migrations.run_migrations",
            side_effect=RuntimeError("migration boom"),
        ):
            with pytest.raises(DatabaseError, match="Migration failed"):
                open_db(_make_config(tmp_path))
        # Connection should have been cleaned up
        assert db_module._conn is None

    def test_open_db_pragma_failure_closes_conn_and_raises(self, tmp_path):
        """PRAGMA failure closes the connection and raises DatabaseError."""
        import sqlite3
        from unittest.mock import patch, MagicMock
        from forge_memory.models import DatabaseError

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.execute.side_effect = sqlite3.OperationalError("PRAGMA boom")

        with patch("forge_memory.db._open_plain", return_value=bad_conn):
            with pytest.raises(DatabaseError, match="Failed to apply PRAGMAs"):
                open_db(_make_config(tmp_path))

        bad_conn.close.assert_called_once()

    def test_open_plain_raises_on_invalid_path(self):
        """_open_plain raises DatabaseError when the path is invalid."""
        from unittest.mock import patch
        from forge_memory.db import _open_plain
        from forge_memory.models import DatabaseError

        with patch("sqlite3.connect", side_effect=Exception("cannot open")):
            with pytest.raises(DatabaseError, match="Failed to open database"):
                _open_plain("/bad/path/db.sqlite")

    def test_close_db_handles_exception_silently(self):
        """close_db logs but does not re-raise if conn.close() raises."""
        import sqlite3
        from unittest.mock import MagicMock

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.close.side_effect = Exception("close boom")
        db_module._conn = bad_conn
        # Should not raise
        close_db()
        assert db_module._conn is None


