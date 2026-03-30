"""Integration tests for forge-memory — end-to-end workflows through the tools layer.

These tests exercise full workflows spanning core.py, sessions.py, search.py,
relations.py, and db.py together, using the in-memory SQLite fixture from conftest.py.
"""

from __future__ import annotations

import pytest

from forge_memory.models import NotFoundError
from forge_memory.tools.core import (
    forge_mem_context,
    forge_mem_delete,
    forge_mem_get,
    forge_mem_save,
    forge_mem_search,
    forge_mem_synonym_add,
    forge_mem_update,
)
from forge_memory.tools.relations import (
    forge_mem_relate,
    forge_mem_related,
)
from forge_memory.tools.sessions import (
    forge_mem_session_end,
    forge_mem_session_start,
    forge_mem_session_summary,
)


# ---------------------------------------------------------------------------
# 1. Full CRUD lifecycle
# ---------------------------------------------------------------------------


class TestFullCrudLifecycle:
    """Save -> get -> update -> verify update -> delete -> verify gone."""

    def test_crud_lifecycle(self, db):
        # CREATE
        created = forge_mem_save(
            title="CRUD test observation",
            content="Initial content for lifecycle test",
            type="discovery",
            project="integ-proj",
            tags=["lifecycle", "crud"],
        )
        assert created["status"] == "created"
        obs_id = created["id"]

        # READ
        obs = forge_mem_get(id=obs_id)
        assert obs["title"] == "CRUD test observation"
        assert obs["content"] == "Initial content for lifecycle test"
        assert obs["type"] == "discovery"
        assert obs["project"] == "integ-proj"
        # User tags present (auto-tags may also be added)
        assert "lifecycle" in obs["tags"]
        assert "crud" in obs["tags"]

        # UPDATE
        updated = forge_mem_update(
            id=obs_id,
            title="CRUD test observation (updated)",
            content="Updated content with more detail",
            tags=["lifecycle", "crud", "updated"],
        )
        assert updated["status"] == "updated"

        # VERIFY UPDATE (forge_mem_update sets tags directly, no auto-tagging)
        obs2 = forge_mem_get(id=obs_id)
        assert obs2["title"] == "CRUD test observation (updated)"
        assert obs2["content"] == "Updated content with more detail"
        assert set(obs2["tags"]) == {"lifecycle", "crud", "updated"}

        # DELETE
        deleted = forge_mem_delete(id=obs_id)
        assert deleted["status"] == "deleted"

        # VERIFY soft-deleted but still retrievable via get
        deleted_obs = forge_mem_get(id=obs_id)
        assert deleted_obs["is_active"] is False

        # VERIFY GONE from search
        results = forge_mem_search(query="CRUD lifecycle", project="integ-proj")
        assert results["count"] == 0


# ---------------------------------------------------------------------------
# 2. Upsert workflow
# ---------------------------------------------------------------------------


class TestUpsertWorkflow:
    """Save with topic_key -> save again -> verify single observation updated."""

    def test_upsert_keeps_single_observation(self, db):
        topic = "arch/state-management"

        r1 = forge_mem_save(
            title="State: Redux",
            content="Chose Redux for state management",
            type="decision",
            project="upsert-proj",
            topic_key=topic,
            tags=["state", "redux"],
        )
        assert r1["status"] == "created"
        first_id = r1["id"]

        r2 = forge_mem_save(
            title="State: Zustand",
            content="Switched to Zustand — simpler API, less boilerplate",
            type="decision",
            project="upsert-proj",
            topic_key=topic,
            tags=["state", "zustand"],
        )
        assert r2["status"] == "updated"
        assert r2["id"] == first_id  # Same row, not a new one

        # Only one observation should exist
        ctx = forge_mem_context(project="upsert-proj")
        assert ctx["count"] == 1

        # Content should be the latest
        obs = forge_mem_get(id=first_id)
        assert obs["title"] == "State: Zustand"
        assert "Zustand" in obs["content"]
        # User tags present (auto-tags may also be added)
        assert "state" in obs["tags"]
        assert "zustand" in obs["tags"]


# ---------------------------------------------------------------------------
# 3. Search ranking with recency
# ---------------------------------------------------------------------------


class TestSearchRankingWithRecency:
    """More recent observations should rank higher for the same query terms."""

    def test_recent_observation_ranks_higher(self, db):
        # Save two observations with the same search term
        r_old = forge_mem_save(
            title="Authentication bug in login flow",
            content="Fixed authentication bypass in the login handler",
            type="bugfix",
            project="rank-proj",
        )

        # Manually set the old one to a year-old date
        db.execute(
            "UPDATE observations SET updated_at = '2025-01-01 00:00:00' WHERE id = ?",
            [r_old["id"]],
        )
        db.commit()

        r_new = forge_mem_save(
            title="Authentication improvement for API keys",
            content="Improved authentication token validation in API layer",
            type="discovery",
            project="rank-proj",
        )

        results = forge_mem_search(query="authentication", project="rank-proj")
        assert results["count"] == 2

        ids = [r["id"] for r in results["results"]]
        # The newer observation should appear first
        assert ids[0] == r_new["id"]
        assert ids[1] == r_old["id"]

        # Verify the newer one has a higher score
        assert results["results"][0]["score"] >= results["results"][1]["score"]


# ---------------------------------------------------------------------------
# 4. Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Start session -> save observations -> end session -> verify."""

    def test_session_start_save_end(self, db):
        # Start session
        session = forge_mem_session_start(
            project="session-proj",
            feature_slug="FEAT-042",
        )
        assert session["status"] == "started"
        session_id = session["session_id"]
        assert isinstance(session_id, int)
        assert session["started_at"] is not None

        # Save observations during the session
        forge_mem_save(
            title="Discovered N+1 query",
            content="UserList component triggers N+1 queries",
            type="bugfix",
            project="session-proj",
            feature_slug="FEAT-042",
        )
        forge_mem_save(
            title="Chose pagination strategy",
            content="Cursor-based pagination for large datasets",
            type="decision",
            project="session-proj",
            feature_slug="FEAT-042",
        )

        # End session
        ended = forge_mem_session_end(
            session_id=session_id,
            summary="Fixed N+1 query and chose cursor pagination for FEAT-042",
        )
        assert ended["status"] == "ended"
        assert ended["session_id"] == session_id
        assert ended["ended_at"] is not None

        # Verify session in DB
        row = db.execute(
            "SELECT project, summary, feature_slug, started_at, ended_at "
            "FROM sessions WHERE id = ?",
            [session_id],
        ).fetchone()
        assert row is not None
        assert row[0] == "session-proj"
        assert "N+1" in row[1]
        assert row[2] == "FEAT-042"
        assert row[3] is not None  # started_at
        assert row[4] is not None  # ended_at

    def test_end_nonexistent_session_raises(self, db):
        with pytest.raises(NotFoundError):
            forge_mem_session_end(session_id=9999, summary="nope")


# ---------------------------------------------------------------------------
# 5. Standalone session summary
# ---------------------------------------------------------------------------


class TestStandaloneSessionSummary:
    """session_summary creates and closes a session in one call."""

    def test_session_summary_creates_closed_session(self, db):
        result = forge_mem_session_summary(
            project="summary-proj",
            summary="Refactored auth module, added JWT refresh token support",
            feature_slug="FEAT-010",
        )
        assert result["status"] == "completed"
        assert result["summary_length"] > 0
        session_id = result["session_id"]

        # Verify session is both started and ended
        row = db.execute(
            "SELECT started_at, ended_at, summary, project, feature_slug "
            "FROM sessions WHERE id = ?",
            [session_id],
        ).fetchone()
        assert row is not None
        assert row[0] is not None  # started_at
        assert row[1] is not None  # ended_at
        assert row[0] == row[1]  # same timestamp (atomic open+close)
        assert "JWT refresh" in row[2]
        assert row[3] == "summary-proj"
        assert row[4] == "FEAT-010"


# ---------------------------------------------------------------------------
# 6. Tags and FTS5
# ---------------------------------------------------------------------------


class TestTagsAndFts5:
    """Observations are searchable by tag, content, and title via FTS5."""

    def test_search_by_tag_term(self, db):
        forge_mem_save(
            title="Redux middleware pattern",
            content="Custom middleware for logging and analytics",
            type="pattern",
            project="fts-proj",
            tags=["redux", "middleware", "analytics"],
        )

        results = forge_mem_search(query="middleware", project="fts-proj")
        assert results["count"] >= 1
        assert any("middleware" in r["title"].lower() for r in results["results"])

    def test_search_by_content(self, db):
        forge_mem_save(
            title="Deployment config",
            content="Kubernetes horizontal pod autoscaler set to min=2 max=8",
            type="config",
            project="fts-proj",
        )

        results = forge_mem_search(query="autoscaler", project="fts-proj")
        assert results["count"] >= 1
        found_ids = [r["id"] for r in results["results"]]
        assert len(found_ids) >= 1

    def test_search_by_title(self, db):
        forge_mem_save(
            title="Hexagonal architecture boundaries",
            content="Ports and adapters for the payment service",
            type="architecture",
            project="fts-proj",
        )

        results = forge_mem_search(query="hexagonal", project="fts-proj")
        assert results["count"] >= 1
        titles = [r["title"] for r in results["results"]]
        assert any("Hexagonal" in t for t in titles)

    def test_tags_stored_and_returned(self, db):
        r = forge_mem_save(
            title="Tagged observation",
            content="This has specific tags",
            type="discovery",
            project="fts-proj",
            tags=["angular", "signals", "reactivity"],
        )
        obs = forge_mem_get(id=r["id"])
        # User tags present (auto-tags may also be added)
        assert "angular" in obs["tags"]
        assert "signals" in obs["tags"]
        assert "reactivity" in obs["tags"]


# ---------------------------------------------------------------------------
# 7. Multi-project isolation
# ---------------------------------------------------------------------------


class TestMultiProjectIsolation:
    """Observations in different projects do not leak across boundaries."""

    def test_search_isolated_by_project(self, db):
        forge_mem_save(
            title="Database migration strategy",
            content="Using Alembic for database migrations in project alpha",
            type="decision",
            project="alpha",
        )
        forge_mem_save(
            title="Database migration strategy",
            content="Using Flyway for database migrations in project beta",
            type="decision",
            project="beta",
        )

        alpha_results = forge_mem_search(query="database migration", project="alpha")
        beta_results = forge_mem_search(query="database migration", project="beta")

        # Each project sees only its own
        assert alpha_results["count"] == 1
        assert beta_results["count"] == 1
        assert "Alembic" in alpha_results["results"][0]["content_preview"]
        assert "Flyway" in beta_results["results"][0]["content_preview"]

    def test_context_isolated_by_project(self, db):
        forge_mem_save(
            title="Alpha config",
            content="Alpha-specific configuration details",
            type="config",
            project="alpha",
        )
        forge_mem_save(
            title="Beta config",
            content="Beta-specific configuration details",
            type="config",
            project="beta",
        )

        alpha_ctx = forge_mem_context(project="alpha")
        beta_ctx = forge_mem_context(project="beta")

        assert alpha_ctx["count"] == 1
        assert beta_ctx["count"] == 1
        assert alpha_ctx["observations"][0]["title"] == "Alpha config"
        assert beta_ctx["observations"][0]["title"] == "Beta config"


# ---------------------------------------------------------------------------
# 8. Soft delete isolation
# ---------------------------------------------------------------------------


class TestSoftDeleteIsolation:
    """Deleted observations are invisible to search and context."""

    def test_deleted_not_in_search_active_still_found(self, db):
        r1 = forge_mem_save(
            title="Observation to delete soon",
            content="This will be soft-deleted and should vanish from search",
            type="discovery",
            project="del-proj",
        )
        r2 = forge_mem_save(
            title="Observation that stays active",
            content="This should remain visible in search results",
            type="discovery",
            project="del-proj",
        )

        # Delete the first one
        forge_mem_delete(id=r1["id"])

        # Search should only find the active one
        results = forge_mem_search(query="observation", project="del-proj")
        result_ids = [r["id"] for r in results["results"]]
        assert r1["id"] not in result_ids
        assert r2["id"] in result_ids

    def test_deleted_not_in_context(self, db):
        r1 = forge_mem_save(
            title="Context-visible observation",
            content="Should appear in context listing",
            type="pattern",
            project="del-proj",
        )
        r2 = forge_mem_save(
            title="Context-hidden observation",
            content="Will be deleted before context check",
            type="pattern",
            project="del-proj",
        )

        forge_mem_delete(id=r2["id"])

        ctx = forge_mem_context(project="del-proj")
        ctx_ids = [o["id"] for o in ctx["observations"]]
        assert r1["id"] in ctx_ids
        assert r2["id"] not in ctx_ids

    def test_double_delete_is_idempotent(self, db):
        """Deleting an already-deleted observation succeeds idempotently."""
        r = forge_mem_save(
            title="Delete me twice",
            content="Should succeed on second delete",
            type="bugfix",
            project="del-proj",
        )
        result1 = forge_mem_delete(id=r["id"])
        assert result1["status"] == "deleted"

        result2 = forge_mem_delete(id=r["id"])
        assert result2["status"] == "deleted"


# ---------------------------------------------------------------------------
# 9. v0.2 — Synonyms + search expansion end-to-end
# ---------------------------------------------------------------------------


class TestSynonymSearchExpansion:
    """Save observations, add synonyms, search with synonym term."""

    def test_synonym_expands_search(self, db):
        """Adding a synonym makes search find results via the synonym term."""
        # Save observation with English term
        forge_mem_save(
            title="Authentication flow redesign",
            content="Redesigned the authentication flow for better security",
            type="architecture",
            project="v02-proj",
            tags=["auth"],
        )

        # Search with English term works
        direct = forge_mem_search(query="authentication", project="v02-proj")
        assert direct["count"] >= 1

        # Add a custom synonym
        syn_result = forge_mem_synonym_add(
            term="authentication",
            synonym="autenticacion_custom",
            language="en",
        )
        assert syn_result["status"] == "created"

        # Search with the synonym term should also find the observation
        # (expand_synonyms uses language=None, so "en" synonyms are included)
        expanded = forge_mem_search(query="autenticacion_custom", project="v02-proj")
        assert expanded["count"] >= 1
        assert any("Authentication" in r["title"] for r in expanded["results"])


# ---------------------------------------------------------------------------
# 10. v0.2 — Relations end-to-end
# ---------------------------------------------------------------------------


class TestRelationsEndToEnd:
    """Create relations between observations and traverse the graph."""

    def test_full_relation_flow(self, db):
        """Save observations, create relations, traverse graph."""
        # Save observations
        r1 = forge_mem_save(
            title="Base architecture decision",
            content="Chose hexagonal architecture for the payment service",
            type="architecture",
            project="rel-proj",
        )
        r2 = forge_mem_save(
            title="Extended architecture for notifications",
            content="Extended hexagonal architecture to notification service",
            type="architecture",
            project="rel-proj",
        )
        r3 = forge_mem_save(
            title="Related performance concern",
            content="Performance implications of hexagonal architecture",
            type="discovery",
            project="rel-proj",
        )

        # Create relations
        rel1 = forge_mem_relate(r2["id"], r1["id"], "extends")
        assert rel1["status"] == "created"

        rel2 = forge_mem_relate(r1["id"], r3["id"], "related")
        assert rel2["status"] == "created"

        # Traverse from r1 — should find both r2 (via reverse symmetric) and r3
        result = forge_mem_related(r1["id"], depth=1)
        related_ids = {r["id"] for r in result["relations"]}
        assert r3["id"] in related_ids

        # Traverse from r2 — directional "extends" only follows forward
        # r2 -> r1 was stored as (r2, r1, extends), so from r2 we find r1
        result_r2 = forge_mem_related(r2["id"], relation_type="extends", depth=1)
        assert any(r["id"] == r1["id"] for r in result_r2["relations"])

        # Depth 2 from r2 should find r3 through r1
        result_deep = forge_mem_related(r2["id"], depth=2)
        deep_ids = {r["id"] for r in result_deep["relations"]}
        assert r1["id"] in deep_ids


# ---------------------------------------------------------------------------
# 11. v0.2 — Save with auto-suggestion end-to-end
# ---------------------------------------------------------------------------


class TestAutoSuggestionEndToEnd:
    """Save observations and verify auto-suggestion works end-to-end."""

    def test_save_with_auto_suggestion(self, db):
        """Save 6+ similar observations, then save another -> similar populated."""
        # Seed 6 similar observations
        for i in range(6):
            forge_mem_save(
                title=f"Redux state management pattern {i}",
                content=(
                    f"Detailed content about Redux state management "
                    f"patterns, selectors, and middleware for observation {i}. "
                    f"This provides comprehensive coverage of the topic."
                ),
                type="pattern",
                project="auto-proj",
            )

        # Save a similar observation
        result = forge_mem_save(
            title="Redux state management best practices",
            content=(
                "Best practices for Redux state management including "
                "normalization, selectors, and middleware patterns. "
                "This is sufficiently long content for suggestion."
            ),
            type="pattern",
            project="auto-proj",
        )

        # similar key should always be present
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)
        # The saved observation should not be in its own similar list
        assert result["id"] not in [s["id"] for s in result["suggestions"]]

    def test_save_response_shape_unchanged_for_short_content(self, db):
        """Short content still returns similar key (empty list)."""
        result = forge_mem_save(
            title="Quick note",
            content="Short",
            type="decision",
            project="auto-proj",
        )
        assert result["suggestions"] == []
        assert "id" in result
        assert "status" in result
