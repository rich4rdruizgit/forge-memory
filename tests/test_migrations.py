"""Tests for forge_memory.migrations — schema creation, FTS5, and constraints."""

from __future__ import annotations

import sqlite3
from typing import Optional

import pytest

from forge_memory.migrations import run_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    """In-memory SQLite connection with migrations applied."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    run_migrations(db)
    yield db
    db.close()


def _insert_observation(
    conn: sqlite3.Connection,
    *,
    title: str = "Test observation",
    content: str = "Some content",
    obs_type: str = "decision",
    project: str = "test-project",
    topic_key: Optional[str] = None,
    tags_text: str = "",
) -> int:
    """Insert a minimal observation row and return its id."""
    cur = conn.execute(
        """
        INSERT INTO observations (title, content, type, project, topic_key, tags_text)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, content, obs_type, project, topic_key, tags_text),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    """run_migrations creates the expected tables and metadata."""

    def test_creates_schema_version_table(self, conn):
        """schema_version table exists after migration."""
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "schema_version" in tables

    def test_creates_all_expected_tables(self, conn):
        """Core tables: observations, tags, sessions all exist."""
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in ("observations", "tags", "sessions"):
            assert table in tables, f"Missing table: {table}"

    def test_creates_fts5_virtual_table(self, conn):
        """FTS5 virtual table observations_fts exists."""
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "observations_fts" in tables

    def test_tags_text_column_exists(self, conn):
        """observations table has the tags_text column."""
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(observations)").fetchall()
        }
        assert "tags_text" in cols


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """run_migrations is safe to call repeatedly."""

    def test_running_twice_does_not_error(self):
        """Calling run_migrations twice on the same DB is a no-op."""
        db = sqlite3.connect(":memory:")
        run_migrations(db)
        run_migrations(db)  # must not raise
        version = db.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0]
        assert version == 2  # v001 + v002
        db.close()


# ---------------------------------------------------------------------------
# FTS5 full-text search
# ---------------------------------------------------------------------------


class TestFTS5Search:
    """FTS5 virtual table indexes observations for full-text search."""

    def test_insert_then_search_matches(self, conn):
        """Inserting an observation makes it findable via FTS5."""
        _insert_observation(
            conn,
            title="Hexagonal architecture",
            content="Ports and adapters pattern",
        )

        rows = conn.execute(
            "SELECT title FROM observations_fts WHERE observations_fts MATCH 'hexagonal'",
        ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == "Hexagonal architecture"

    def test_fts_trigger_on_delete(self, conn):
        """Deleting an observation removes it from FTS index."""
        obs_id = _insert_observation(conn, title="Temp note", content="Will be deleted")

        conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        conn.commit()

        rows = conn.execute(
            "SELECT title FROM observations_fts WHERE observations_fts MATCH 'deleted'",
        ).fetchall()
        assert len(rows) == 0

    def test_fts_trigger_on_update(self, conn):
        """Updating an observation refreshes the FTS index."""
        obs_id = _insert_observation(conn, title="Old title", content="Original content")

        conn.execute(
            "UPDATE observations SET title = ?, content = ? WHERE id = ?",
            ("New title", "Rewritten content", obs_id),
        )
        conn.commit()

        # Old content gone
        old = conn.execute(
            "SELECT * FROM observations_fts WHERE observations_fts MATCH 'original'",
        ).fetchall()
        assert len(old) == 0

        # New content indexed
        new = conn.execute(
            "SELECT title FROM observations_fts WHERE observations_fts MATCH 'rewritten'",
        ).fetchall()
        assert len(new) == 1
        assert new[0][0] == "New title"


# ---------------------------------------------------------------------------
# CHECK constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    """Database-level constraints enforce data integrity."""

    def test_rejects_invalid_observation_type(self, conn):
        """CHECK constraint on type rejects values not in the allowed list."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO observations (title, content, type, project)
                VALUES ('bad', 'data', 'INVALID_TYPE', 'proj')
                """
            )

    def test_unique_partial_index_on_topic_key(self, conn):
        """UNIQUE partial index prevents duplicate (project, topic_key) pairs."""
        _insert_observation(conn, project="p1", topic_key="arch/auth")

        with pytest.raises(sqlite3.IntegrityError):
            _insert_observation(conn, project="p1", topic_key="arch/auth")

    def test_null_topic_key_allows_duplicates(self, conn):
        """NULL topic_key rows are exempt from the unique index."""
        _insert_observation(conn, project="p1", topic_key=None)
        _insert_observation(conn, project="p1", topic_key=None)  # must not raise

        count = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE project = 'p1'"
        ).fetchone()[0]
        assert count == 2


# ---------------------------------------------------------------------------
# CASCADE delete
# ---------------------------------------------------------------------------


class TestCascadeDelete:
    """Foreign key ON DELETE CASCADE between observations and tags."""

    def test_deleting_observation_deletes_tags(self, conn):
        """Tags are removed when their parent observation is deleted."""
        obs_id = _insert_observation(conn, title="Tagged obs")
        conn.execute(
            "INSERT INTO tags (observation_id, tag) VALUES (?, ?)", (obs_id, "python")
        )
        conn.execute(
            "INSERT INTO tags (observation_id, tag) VALUES (?, ?)", (obs_id, "testing")
        )
        conn.commit()

        # Verify tags exist
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 2

        # Delete parent
        conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        conn.commit()

        # Tags should be gone
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# v002 — Search Enhanced (synonyms + relations)
# ---------------------------------------------------------------------------


class TestV002SynonymsTable:
    """v002 migration creates the synonyms table with correct structure."""

    def test_synonyms_table_created(self, conn):
        """synonyms table exists after migration."""
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "synonyms" in tables

    def test_synonyms_table_columns(self, conn):
        """synonyms table has the expected columns."""
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(synonyms)").fetchall()
        }
        assert cols == {"id", "term", "synonym", "language"}

    def test_default_synonyms_seeded(self, conn):
        """Seed data populates the synonyms table with > 0 rows."""
        count = conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]
        assert count > 0
        # We seed ~56 pairs
        assert count >= 50

    def test_required_synonym_pairs_exist(self, conn):
        """Key synonym pairs from the spec exist in seed data."""
        required_pairs = [
            ("performance", "rendimiento"),
            ("auth", "autenticación"),
            ("database", "base de datos"),
            ("testing", "pruebas"),
            ("error", "bug"),
            ("deploy", "despliegue"),
            ("config", "configuración"),
            ("users", "usuarios"),
            ("users", "clientes"),
        ]
        for term, synonym in required_pairs:
            row = conn.execute(
                "SELECT id FROM synonyms WHERE term = ? AND synonym = ?",
                (term, synonym),
            ).fetchone()
            assert row is not None, f"Missing synonym pair: ({term}, {synonym})"

    def test_synonyms_unique_constraint(self, conn):
        """UNIQUE constraint prevents duplicate (term, synonym, language) pairs."""
        # Insert a new pair first
        conn.execute(
            "INSERT INTO synonyms (term, synonym, language) VALUES ('test_x', 'test_y', 'en')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO synonyms (term, synonym, language) VALUES ('test_x', 'test_y', 'en')"
            )


class TestV002RelationsTable:
    """v002 migration creates the relations table with correct constraints."""

    def test_relations_table_created(self, conn):
        """relations table exists after migration."""
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "relations" in tables

    def test_relations_table_columns(self, conn):
        """relations table has the expected columns."""
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(relations)").fetchall()
        }
        assert cols == {"id", "source_id", "target_id", "relation_type", "created_at"}

    def test_relations_unique_constraint(self, conn):
        """UNIQUE(source_id, target_id, relation_type) prevents duplicates."""
        obs_a = _insert_observation(conn, title="Obs A")
        obs_b = _insert_observation(conn, title="Obs B")

        conn.execute(
            "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
            (obs_a, obs_b, "extends"),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
                (obs_a, obs_b, "extends"),
            )

    def test_relations_check_constraint_rejects_invalid_type(self, conn):
        """CHECK constraint rejects relation types not in the allowed list."""
        obs_a = _insert_observation(conn, title="Obs A")
        obs_b = _insert_observation(conn, title="Obs B")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
                (obs_a, obs_b, "INVALID_TYPE"),
            )

    def test_relations_fk_cascade_on_delete(self, conn):
        """Deleting an observation cascades to relations referencing it."""
        obs_a = _insert_observation(conn, title="Obs A")
        obs_b = _insert_observation(conn, title="Obs B")

        conn.execute(
            "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
            (obs_a, obs_b, "related"),
        )
        conn.commit()

        # Verify relation exists
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1

        # Delete source observation
        conn.execute("DELETE FROM observations WHERE id = ?", (obs_a,))
        conn.commit()

        # Relation should be gone (CASCADE)
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0

    def test_relations_fk_cascade_target_delete(self, conn):
        """Deleting the target observation also cascades to relations."""
        obs_a = _insert_observation(conn, title="Obs A")
        obs_b = _insert_observation(conn, title="Obs B")

        conn.execute(
            "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
            (obs_a, obs_b, "depends_on"),
        )
        conn.commit()

        # Delete target observation
        conn.execute("DELETE FROM observations WHERE id = ?", (obs_b,))
        conn.commit()

        # Relation should be gone
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0

    def test_relations_allows_different_types_same_pair(self, conn):
        """Same (source, target) can have multiple relation types."""
        obs_a = _insert_observation(conn, title="Obs A")
        obs_b = _insert_observation(conn, title="Obs B")

        conn.execute(
            "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
            (obs_a, obs_b, "extends"),
        )
        conn.execute(
            "INSERT INTO relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
            (obs_a, obs_b, "related"),
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        assert count == 2


class TestV002Idempotency:
    """v002 migration is idempotent — running twice does not error."""

    def test_v002_idempotent(self):
        """Running all migrations twice on the same DB causes no error."""
        db = sqlite3.connect(":memory:")
        db.execute("PRAGMA foreign_keys = ON")
        run_migrations(db)
        run_migrations(db)  # must not raise

        # Schema version should have exactly 2 entries (v001 + v002)
        version_count = db.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0]
        assert version_count == 2

        # Synonym count should not double
        count_first = db.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]
        run_migrations(db)
        count_second = db.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]
        assert count_first == count_second
        db.close()


class TestV001V002Sequential:  # noqa: D101
    """v001 and v002 run sequentially without issues."""

    def test_sequential_migration(self):
        """Applying v001 then v002 produces correct schema."""
        db = sqlite3.connect(":memory:")
        db.execute("PRAGMA foreign_keys = ON")
        run_migrations(db)

        # All tables should exist
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in ("observations", "tags", "sessions", "synonyms", "relations"):
            assert table in tables, f"Missing table: {table}"

        # Schema version should be at 2
        max_version = db.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert max_version == 2

        # v001 data should still work fine
        obs_id = _insert_observation(db, title="After both migrations")
        row = db.execute(
            "SELECT title FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        assert row[0] == "After both migrations"

        db.close()


# ---------------------------------------------------------------------------
# _discover_migrations edge cases
# ---------------------------------------------------------------------------


class TestDiscoverMigrationsEdgeCases:
    """_discover_migrations skips non-v* modules and modules without VERSION."""

    def test_skips_non_v_modules(self):
        """Modules not starting with 'v' are ignored."""
        import types
        from unittest.mock import patch
        from forge_memory.migrations import _discover_migrations

        v001 = types.ModuleType("forge_memory.migrations.v001_initial")
        v001.VERSION = 1

        def fake_import(name):
            if "v001_initial" in name:
                return v001
            raise ImportError(f"unexpected import: {name}")

        with patch("pkgutil.iter_modules") as mock_iter:
            # First item starts with 'h' — should be skipped
            mock_iter.return_value = [
                (None, "helpers", False),
                (None, "v001_initial", False),
            ]
            with patch("importlib.import_module", side_effect=fake_import):
                result = _discover_migrations()

        # Only the v* module should be included
        assert len(result) == 1
        assert result[0][0] == 1

    def test_skips_modules_without_version(self):
        """Modules with no VERSION attribute are ignored."""
        import types
        from unittest.mock import patch
        from forge_memory.migrations import _discover_migrations

        no_version_mod = types.ModuleType("forge_memory.migrations.v999_no_version")
        # Intentionally no VERSION attribute

        with patch("pkgutil.iter_modules") as mock_iter:
            mock_iter.return_value = [
                (None, "v999_no_version", False),
            ]
            with patch("importlib.import_module", return_value=no_version_mod):
                result = _discover_migrations()

        assert result == []
