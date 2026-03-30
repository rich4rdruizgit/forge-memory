"""Database connection layer for forge-memory.

Manages the SQLite connection lifecycle using stdlib ``sqlite3``.

Typical usage (called once at server startup)::

    from forge_memory.config import load_config
    from forge_memory.db import open_db, get_db, close_db

    conn = open_db(load_config())
    # … later, from any module …
    conn = get_db()
    # … at shutdown …
    close_db()
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from forge_memory.config import Config
from forge_memory.models import DatabaseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level connection holder (single-threaded MCP stdio — no pooling)
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def open_db(config: Config) -> sqlite3.Connection:
    """Open (or create) the forge-memory database and run migrations.

    After connecting the function:
    1. Applies ``PRAGMA journal_mode=WAL`` and ``PRAGMA foreign_keys=ON``.
    2. Runs pending schema migrations via :func:`forge_memory.migrations.run_migrations`.
    3. Stores the connection in a module-level variable for :func:`get_db`.

    Returns the ready-to-use connection.

    Raises:
        DatabaseError: On connection failure.
    """
    global _conn  # noqa: PLW0603

    # Ensure the parent directory exists (mkdir -p equivalent)
    db_dir = Path(config.db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    conn = _open_plain(config.db_path)

    # --- Common PRAGMAs ---
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception as exc:
        conn.close()
        raise DatabaseError(f"Failed to apply PRAGMAs: {exc}") from exc

    # --- Migrations ---
    try:
        from forge_memory.migrations import run_migrations  # noqa: PLC0415

        run_migrations(conn)
    except Exception as exc:
        conn.close()
        raise DatabaseError(f"Migration failed: {exc}") from exc

    _conn = conn
    logger.info("Database opened: %s", config.db_path)
    return conn


def get_db() -> sqlite3.Connection:
    """Return the current database connection.

    Raises:
        DatabaseError: If :func:`open_db` has not been called yet.
    """
    if _conn is None:
        raise DatabaseError("Database not initialized. Call open_db first.")
    return _conn


def close_db() -> None:
    """Close the database connection and clear the module-level reference."""
    global _conn  # noqa: PLW0603

    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            logger.exception("Error closing database connection")
        finally:
            _conn = None
        logger.info("Database connection closed.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_plain(db_path: str) -> sqlite3.Connection:
    """Open a plain (unencrypted) SQLite connection."""
    try:
        conn = sqlite3.connect(db_path)
    except Exception as exc:
        raise DatabaseError(
            f"Failed to open database at {db_path}: {exc}"
        ) from exc

    logger.info("Opened plain (unencrypted) database.")
    return conn
