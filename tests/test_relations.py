"""Tests for forge_memory.tools.relations — relation tools and BFS traversal."""

from __future__ import annotations

import pytest

from forge_memory.models import NotFoundError, ValidationError
from forge_memory.tools.relations import forge_mem_relate, forge_mem_related


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_observation(db, title: str, obs_id: int | None = None) -> int:
    """Insert a minimal observation and return its id."""
    cursor = db.execute(
        "INSERT INTO observations (title, content, type, scope, project) "
        "VALUES (?, ?, 'decision', 'project', 'test-proj')",
        [title, f"Content for {title}"],
    )
    db.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# forge_mem_relate
# ---------------------------------------------------------------------------


class TestForgeMemRelate:
    """forge_mem_relate creates edges in the knowledge graph."""

    def test_creates_relation(self, db):
        """Creates a relation and returns correct shape."""
        a = _create_observation(db, "Obs A")
        b = _create_observation(db, "Obs B")

        result = forge_mem_relate(a, b, "related")

        assert result["status"] == "created"
        assert result["source_id"] == a
        assert result["target_id"] == b
        assert result["relation_type"] == "related"
        assert isinstance(result["id"], int)
        assert result["id"] > 0

    def test_idempotent_on_duplicate(self, db):
        """Second call with same triple returns status 'exists'."""
        a = _create_observation(db, "Obs A")
        b = _create_observation(db, "Obs B")

        r1 = forge_mem_relate(a, b, "extends")
        assert r1["status"] == "created"

        r2 = forge_mem_relate(a, b, "extends")
        assert r2["status"] == "exists"
        assert r2["id"] == r1["id"]

    def test_rejects_invalid_relation_type(self, db):
        """Invalid relation_type raises ValidationError."""
        a = _create_observation(db, "Obs A")
        b = _create_observation(db, "Obs B")

        with pytest.raises(ValidationError, match="relation_type"):
            forge_mem_relate(a, b, "invalid_type")

    def test_rejects_self_relation(self, db):
        """source_id == target_id raises ValidationError."""
        a = _create_observation(db, "Obs A")

        with pytest.raises(ValidationError, match="self-relation"):
            forge_mem_relate(a, a, "related")

    def test_rejects_nonexistent_source(self, db):
        """Non-existent source_id raises NotFoundError."""
        b = _create_observation(db, "Obs B")

        with pytest.raises(NotFoundError):
            forge_mem_relate(9999, b, "related")

    def test_rejects_nonexistent_target(self, db):
        """Non-existent target_id raises NotFoundError."""
        a = _create_observation(db, "Obs A")

        with pytest.raises(NotFoundError):
            forge_mem_relate(a, 9999, "related")

    def test_rejects_soft_deleted_target(self, db):
        """Soft-deleted target raises NotFoundError."""
        a = _create_observation(db, "Obs A")
        b = _create_observation(db, "Obs B")
        # Soft-delete b
        db.execute("UPDATE observations SET is_active = 0 WHERE id = ?", [b])
        db.commit()

        with pytest.raises(NotFoundError):
            forge_mem_relate(a, b, "related")

    def test_rejects_soft_deleted_source(self, db):
        """Soft-deleted source raises NotFoundError."""
        a = _create_observation(db, "Obs A")
        b = _create_observation(db, "Obs B")
        # Soft-delete a
        db.execute("UPDATE observations SET is_active = 0 WHERE id = ?", [a])
        db.commit()

        with pytest.raises(NotFoundError):
            forge_mem_relate(a, b, "related")


# ---------------------------------------------------------------------------
# forge_mem_related
# ---------------------------------------------------------------------------


class TestForgeMemRelated:
    """forge_mem_related traverses the knowledge graph via BFS."""

    def test_depth_1_finds_direct_relations(self, db):
        """Depth=1 returns directly related observations."""
        a = _create_observation(db, "Root")
        b = _create_observation(db, "Child")
        forge_mem_relate(a, b, "extends")

        result = forge_mem_related(a, depth=1)

        assert result["id"] == a
        assert result["depth"] == 1
        assert len(result["relations"]) == 1
        assert result["relations"][0]["id"] == b
        assert result["relations"][0]["title"] == "Child"
        assert result["relations"][0]["relation_type"] == "extends"
        assert result["relations"][0]["depth"] == 1

    def test_depth_2_finds_transitive_relations(self, db):
        """Depth=2 finds observations two hops away."""
        a = _create_observation(db, "A")
        b = _create_observation(db, "B")
        c = _create_observation(db, "C")

        forge_mem_relate(a, b, "extends")
        forge_mem_relate(b, c, "extends")

        result = forge_mem_related(a, depth=2)

        ids = {r["id"] for r in result["relations"]}
        assert b in ids
        assert c in ids
        # B at depth 1, C at depth 2
        depths = {r["id"]: r["depth"] for r in result["relations"]}
        assert depths[b] == 1
        assert depths[c] == 2

    def test_filters_by_relation_type(self, db):
        """Only follows edges of the specified type."""
        a = _create_observation(db, "A")
        b = _create_observation(db, "B")
        c = _create_observation(db, "C")

        forge_mem_relate(a, b, "extends")
        forge_mem_relate(a, c, "related")

        result = forge_mem_related(a, relation_type="extends", depth=1)

        ids = {r["id"] for r in result["relations"]}
        assert b in ids
        assert c not in ids

    def test_symmetric_finds_both_directions(self, db):
        """Symmetric types (related) find targets AND sources."""
        a = _create_observation(db, "A")
        b = _create_observation(db, "B")

        # A -> B via 'related'
        forge_mem_relate(a, b, "related")

        # From B, should find A (reverse direction for symmetric)
        result = forge_mem_related(b, relation_type="related", depth=1)

        ids = {r["id"] for r in result["relations"]}
        assert a in ids

    def test_directional_only_follows_forward(self, db):
        """Directional types (extends) only follow source -> target."""
        a = _create_observation(db, "A")
        b = _create_observation(db, "B")

        # A -> B via 'extends'
        forge_mem_relate(a, b, "extends")

        # From B, should NOT find A (reverse direction for directional)
        result = forge_mem_related(b, relation_type="extends", depth=1)

        assert len(result["relations"]) == 0

    def test_max_depth_enforced(self, db):
        """Depth > 3 is capped to 3."""
        a = _create_observation(db, "A")
        b = _create_observation(db, "B")
        c = _create_observation(db, "C")
        d = _create_observation(db, "D")
        e = _create_observation(db, "E")

        forge_mem_relate(a, b, "extends")
        forge_mem_relate(b, c, "extends")
        forge_mem_relate(c, d, "extends")
        forge_mem_relate(d, e, "extends")

        # Request depth=5, should be capped to 3
        result = forge_mem_related(a, depth=5)

        assert result["depth"] == 3
        ids = {r["id"] for r in result["relations"]}
        assert b in ids
        assert c in ids
        assert d in ids
        # e is at depth 4 — should NOT be included
        assert e not in ids

    def test_no_duplicates_with_cycles(self, db):
        """BFS handles cycles without duplicates."""
        a = _create_observation(db, "A")
        b = _create_observation(db, "B")
        c = _create_observation(db, "C")

        # A -> B -> C -> A (cycle via symmetric 'related')
        forge_mem_relate(a, b, "related")
        forge_mem_relate(b, c, "related")
        forge_mem_relate(c, a, "related")

        result = forge_mem_related(a, depth=3)

        # Each observation should appear at most once
        ids = [r["id"] for r in result["relations"]]
        assert len(ids) == len(set(ids))
        # Both B and C should be found
        assert b in ids
        assert c in ids

    def test_empty_for_no_relations(self, db):
        """Observation with no relations returns empty list."""
        a = _create_observation(db, "Lonely")

        result = forge_mem_related(a, depth=1)

        assert result["id"] == a
        assert result["relations"] == []

    def test_rejects_depth_less_than_1(self, db):
        """depth < 1 raises ValidationError."""
        a = _create_observation(db, "A")

        with pytest.raises(ValidationError, match="depth"):
            forge_mem_related(a, depth=0)

    def test_rejects_negative_depth(self, db):
        """Negative depth raises ValidationError."""
        a = _create_observation(db, "A")

        with pytest.raises(ValidationError, match="depth"):
            forge_mem_related(a, depth=-1)

    def test_rejects_nonexistent_observation(self, db):
        """Non-existent observation raises NotFoundError."""
        with pytest.raises(NotFoundError):
            forge_mem_related(9999)

    def test_max_results_cap(self, db):
        """BFS stops adding results after _MAX_RESULTS (50) is reached."""
        from forge_memory.tools.relations import _MAX_RESULTS

        # Create a root and more than MAX_RESULTS neighbors
        root = _create_observation(db, "Root")
        count = _MAX_RESULTS + 5  # over the cap

        for i in range(count):
            neighbor = _create_observation(db, f"Neighbor {i}")
            forge_mem_relate(root, neighbor, "related")

        result = forge_mem_related(root, depth=1)
        assert len(result["relations"]) <= _MAX_RESULTS

    def test_invalid_relation_type_in_db_is_skipped(self, db):
        """Relations with unrecognized types in DB are skipped during _get_neighbors."""
        import sqlite3
        from forge_memory.tools.relations import _get_neighbors

        # Create two observations
        a = _create_observation(db, "Root")
        b = _create_observation(db, "Neighbor")

        # Use a wrapper that injects an invalid relation_type in the result rows
        original_execute = db.execute

        class FakeRow:
            def __init__(self, src, tgt, rtype):
                self._data = (src, tgt, rtype)

            def __getitem__(self, idx):
                return self._data[idx]

        class InjectBadTypeConn:
            def __init__(self, inner, a_id, b_id):
                self._inner = inner
                self._a = a_id
                self._b = b_id
                self._inject_called = False

            def execute(self, sql, params=None):
                if "source_id = ? OR target_id = ?" in sql and not self._inject_called:
                    self._inject_called = True
                    # Return a cursor-like with a bad row
                    class FakeCursor:
                        def fetchall(self_):
                            return [
                                (self._a, self._b, "INVALID_TYPE_NOT_IN_ENUM"),
                            ]
                    return FakeCursor()
                if params is not None:
                    return self._inner.execute(sql, params)
                return self._inner.execute(sql)

        wrapped = InjectBadTypeConn(db, a, b)
        # _get_neighbors with rel_type_filter=None processes each type via RelationType()
        # and skips rows where the value is invalid — should return []
        result = _get_neighbors(wrapped, a, None)
        # Invalid type is skipped, so no neighbors
        assert result == []
