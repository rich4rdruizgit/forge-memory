"""Migration runner for forge-memory.

Maintains a ``schema_version`` table and discovers migration modules
(``v001_*.py``, ``v002_*.py``, …) in this package directory.  Each module
must expose:

    VERSION: int          — sequential version number
    migrate(conn) -> None — applies the migration DDL/DML

Usage::

    from forge_memory.migrations import run_migrations
    run_migrations(conn)
"""

import importlib
import pkgutil
import sqlite3
from pathlib import Path


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create the ``schema_version`` table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()
    return row[0]


def _discover_migrations() -> list[tuple[int, str]]:
    """Discover migration modules in this package, sorted by VERSION.

    Returns a list of ``(version, module_name)`` tuples.
    """
    package_path = str(Path(__file__).parent)
    migrations: list[tuple[int, str]] = []

    for importer, module_name, is_pkg in pkgutil.iter_modules([package_path]):
        if not module_name.startswith("v"):
            continue
        full_name = f"forge_memory.migrations.{module_name}"
        mod = importlib.import_module(full_name)
        version = getattr(mod, "VERSION", None)
        if version is None:
            continue
        migrations.append((version, full_name))

    migrations.sort(key=lambda m: m[0])
    return migrations


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run all pending migrations in order.

    Idempotent — safe to call on every server start.  Each migration is
    recorded in ``schema_version`` so it only runs once.
    """
    _ensure_schema_version_table(conn)
    current = _get_current_version(conn)

    for version, module_name in _discover_migrations():
        if version <= current:
            continue
        mod = importlib.import_module(module_name)
        mod.migrate(conn)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (version,)
        )
        conn.commit()
