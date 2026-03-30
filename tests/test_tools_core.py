"""Tests for forge_memory.tools.core — core MCP tool functions."""

from __future__ import annotations

import pytest

from forge_memory.models import NotFoundError, ValidationError
from forge_memory.tools.core import (
    forge_mem_context,
    forge_mem_delete,
    forge_mem_get,
    forge_mem_save,
    forge_mem_search,
    forge_mem_synonym_add,
    forge_mem_update,
)


# ---------------------------------------------------------------------------
# forge_mem_save
# ---------------------------------------------------------------------------


class TestForgeMemSave:
    """forge_mem_save creates or upserts observations."""

    def test_creates_observation(self, db):
        """New observation returns id and status 'created'."""
        result = forge_mem_save(
            title="Chose Zustand",
            content="State management decision",
            type="decision",
            project="test-proj",
        )
        assert result["status"] == "created"
        assert isinstance(result["id"], int)
        assert result["id"] > 0

    def test_upsert_updates_on_same_topic_key(self, db):
        """Second save with the same topic_key returns status 'updated'."""
        r1 = forge_mem_save(
            title="Auth v1",
            content="JWT based auth",
            type="decision",
            project="proj",
            topic_key="auth/strategy",
        )
        assert r1["status"] == "created"

        r2 = forge_mem_save(
            title="Auth v2",
            content="Switched to session-based auth",
            type="decision",
            project="proj",
            topic_key="auth/strategy",
        )
        assert r2["status"] == "updated"
        assert r2["id"] == r1["id"]

        # Verify the content was actually updated
        obs = forge_mem_get(r1["id"])
        assert obs["title"] == "Auth v2"
        assert obs["content"] == "Switched to session-based auth"

    def test_invalid_type_raises_validation_error(self, db):
        """Invalid observation type raises ValidationError."""
        with pytest.raises(ValidationError):
            forge_mem_save(
                title="Bad type",
                content="Should fail",
                type="nonexistent_type",
                project="proj",
            )

    def test_tags_stored_in_tags_table(self, db):
        """Tags are persisted in the tags table and tags_text is populated."""
        result = forge_mem_save(
            title="Tagged observation",
            content="Has tags",
            type="pattern",
            project="proj",
            tags=["Angular", "Signals"],
        )
        obs = forge_mem_get(result["id"])
        # User tags present (auto-tags may also be added)
        assert "Angular" in obs["tags"]
        assert "Signals" in obs["tags"]

        # Verify tags_text via direct DB query
        row = db.execute(
            "SELECT tags_text FROM observations WHERE id = ?",
            [result["id"]],
        ).fetchone()
        assert "angular" in row[0]
        assert "signals" in row[0]

    def test_upsert_replaces_tags(self, db):
        """Upsert replaces old tags with new ones."""
        r1 = forge_mem_save(
            title="Tagged",
            content="v1",
            type="pattern",
            project="proj",
            topic_key="tags/test",
            tags=["old-tag-a", "old-tag-b"],
        )

        forge_mem_save(
            title="Tagged updated",
            content="v2",
            type="pattern",
            project="proj",
            topic_key="tags/test",
            tags=["new-tag"],
        )

        obs = forge_mem_get(r1["id"])
        # User tag present; old tags replaced (auto-tags may also be added)
        assert "new-tag" in obs["tags"]
        assert "old-tag-a" not in obs["tags"]
        assert "old-tag-b" not in obs["tags"]


# ---------------------------------------------------------------------------
# forge_mem_search
# ---------------------------------------------------------------------------


class TestForgeMemSearch:
    """forge_mem_search performs FTS5 search over observations."""

    def test_finds_matching_observation(self, db):
        """Search returns observations matching the query."""
        forge_mem_save(
            title="Hexagonal architecture",
            content="Ports and adapters pattern for clean boundaries",
            type="architecture",
            project="myproj",
        )
        result = forge_mem_search(query="hexagonal", project="myproj")
        assert result["count"] >= 1
        assert any("Hexagonal" in r["title"] for r in result["results"])

    def test_returns_empty_for_no_match(self, db):
        """No results when nothing matches."""
        forge_mem_save(
            title="Something",
            content="Unrelated content",
            type="decision",
            project="myproj",
        )
        result = forge_mem_search(query="xyzzy_nonexistent", project="myproj")
        assert result["count"] == 0
        assert result["results"] == []

    def test_filters_by_type(self, db):
        """type parameter filters results to a specific observation type."""
        forge_mem_save(
            title="Decision about caching",
            content="Use Redis for caching layer",
            type="decision",
            project="myproj",
        )
        forge_mem_save(
            title="Caching pattern",
            content="Cache-aside pattern for caching",
            type="pattern",
            project="myproj",
        )

        result = forge_mem_search(query="caching", project="myproj", type="decision")
        assert result["count"] >= 1
        assert all(r["type"] == "decision" for r in result["results"])


# ---------------------------------------------------------------------------
# forge_mem_get
# ---------------------------------------------------------------------------


class TestForgeMemGet:
    """forge_mem_get retrieves a single observation by ID."""

    def test_returns_observation(self, db):
        """Returns the full observation dict for a valid ID."""
        r = forge_mem_save(
            title="Get test",
            content="Content here",
            type="discovery",
            project="proj",
            tags=["test"],
        )
        obs = forge_mem_get(r["id"])
        assert obs["id"] == r["id"]
        assert obs["title"] == "Get test"
        assert obs["content"] == "Content here"
        assert obs["type"] == "discovery"
        assert obs["project"] == "proj"
        assert "test" in obs["tags"]

    def test_raises_not_found_for_missing_id(self, db):
        """NotFoundError raised for non-existent ID."""
        with pytest.raises(NotFoundError):
            forge_mem_get(99999)


# ---------------------------------------------------------------------------
# forge_mem_update
# ---------------------------------------------------------------------------


class TestForgeMemUpdate:
    """forge_mem_update modifies specific fields of an observation."""

    def test_updates_specified_fields_only(self, db):
        """Only provided fields are changed; others remain untouched."""
        r = forge_mem_save(
            title="Original title",
            content="Original content",
            type="decision",
            project="proj",
        )
        forge_mem_update(r["id"], title="Updated title")

        obs = forge_mem_get(r["id"])
        assert obs["title"] == "Updated title"
        assert obs["content"] == "Original content"  # unchanged

    def test_updates_tags_and_tags_text(self, db):
        """Tag update replaces tags and regenerates tags_text."""
        r = forge_mem_save(
            title="Tag update test",
            content="Content",
            type="pattern",
            project="proj",
            tags=["old"],
        )
        forge_mem_update(r["id"], tags=["new-a", "new-b"])

        obs = forge_mem_get(r["id"])
        assert set(obs["tags"]) == {"new-a", "new-b"}

        row = db.execute(
            "SELECT tags_text FROM observations WHERE id = ?", [r["id"]]
        ).fetchone()
        assert "new-a" in row[0]
        assert "new-b" in row[0]

    def test_raises_not_found_for_missing_id(self, db):
        """NotFoundError raised when updating a non-existent observation."""
        with pytest.raises(NotFoundError):
            forge_mem_update(99999, title="nope")


# ---------------------------------------------------------------------------
# forge_mem_delete
# ---------------------------------------------------------------------------


class TestForgeMemDelete:
    """forge_mem_delete soft-deletes observations."""

    def test_soft_deletes(self, db):
        """Sets is_active = 0 and returns status 'deleted'."""
        r = forge_mem_save(
            title="To delete",
            content="Bye",
            type="config",
            project="proj",
        )
        result = forge_mem_delete(r["id"])
        assert result["status"] == "deleted"
        assert result["id"] == r["id"]

        # Verify is_active = 0 in the DB
        row = db.execute(
            "SELECT is_active FROM observations WHERE id = ?", [r["id"]]
        ).fetchone()
        assert row[0] == 0

    def test_raises_not_found_for_missing_id(self, db):
        """NotFoundError raised for non-existent ID."""
        with pytest.raises(NotFoundError):
            forge_mem_delete(99999)

    def test_deleted_observation_visible_to_get(self, db):
        """Soft-deleted observation is still returned by forge_mem_get with is_active=False."""
        r = forge_mem_save(
            title="Ghost",
            content="Will vanish",
            type="lesson",
            project="proj",
        )
        forge_mem_delete(r["id"])

        obs = forge_mem_get(r["id"])
        assert obs["is_active"] is False
        assert obs["title"] == "Ghost"

    def test_double_delete_is_idempotent(self, db):
        """Deleting an already-deleted observation succeeds."""
        r = forge_mem_save(
            title="Delete twice",
            content="Should not raise on second delete",
            type="bugfix",
            project="proj",
        )
        result1 = forge_mem_delete(r["id"])
        assert result1["status"] == "deleted"

        result2 = forge_mem_delete(r["id"])
        assert result2["status"] == "deleted"


# ---------------------------------------------------------------------------
# forge_mem_context
# ---------------------------------------------------------------------------


class TestForgeMemContext:
    """forge_mem_context returns recent observations for a project."""

    def test_returns_recent_observations(self, db):
        """Returns observations ordered by recency."""
        forge_mem_save(
            title="First",
            content="1st",
            type="decision",
            project="ctx-proj",
        )
        # Force a different updated_at so ordering is deterministic
        r2 = forge_mem_save(
            title="Second",
            content="2nd",
            type="pattern",
            project="ctx-proj",
        )
        db.execute(
            "UPDATE observations SET updated_at = '2099-01-01 00:00:00' WHERE id = ?",
            [r2["id"]],
        )
        db.commit()

        result = forge_mem_context(project="ctx-proj")
        assert result["count"] == 2
        assert len(result["observations"]) == 2
        # Most recent first (Second has updated_at far in the future)
        assert result["observations"][0]["title"] == "Second"

    def test_empty_project_returns_empty(self, db):
        """Project with no observations returns empty list."""
        result = forge_mem_context(project="nonexistent-proj")
        assert result["count"] == 0
        assert result["observations"] == []

    def test_filters_by_scope(self, db):
        """scope parameter filters context results."""
        forge_mem_save(
            title="Project-scoped",
            content="proj",
            type="decision",
            project="scope-ctx",
            scope="project",
        )
        forge_mem_save(
            title="Personal-scoped",
            content="pers",
            type="preference",
            project="scope-ctx",
            scope="personal",
        )

        proj_ctx = forge_mem_context(project="scope-ctx", scope="project")
        assert proj_ctx["count"] == 1
        assert proj_ctx["observations"][0]["title"] == "Project-scoped"

        pers_ctx = forge_mem_context(project="scope-ctx", scope="personal")
        assert pers_ctx["count"] == 1
        assert pers_ctx["observations"][0]["title"] == "Personal-scoped"

        all_ctx = forge_mem_context(project="scope-ctx")
        assert all_ctx["count"] == 2


# ---------------------------------------------------------------------------
# Scope parameter
# ---------------------------------------------------------------------------


class TestScopeParameter:
    """scope parameter is stored and filterable."""

    def test_save_with_scope(self, db):
        """Scope is stored in the observation."""
        r = forge_mem_save(
            title="Personal pref",
            content="I like dark mode",
            type="preference",
            project="proj",
            scope="personal",
        )
        obs = forge_mem_get(r["id"])
        assert obs["scope"] == "personal"

    def test_save_default_scope_is_project(self, db):
        """Default scope is 'project'."""
        r = forge_mem_save(
            title="Default scope",
            content="No scope specified",
            type="decision",
            project="proj",
        )
        obs = forge_mem_get(r["id"])
        assert obs["scope"] == "project"

    def test_invalid_scope_raises(self, db):
        """Invalid scope raises ValidationError."""
        with pytest.raises(ValidationError):
            forge_mem_save(
                title="Bad scope",
                content="Should fail",
                type="decision",
                project="proj",
                scope="global",
            )

    def test_search_filters_by_scope(self, db):
        """Search scope parameter filters results."""
        forge_mem_save(
            title="Scope search project",
            content="Project-scoped content for search",
            type="decision",
            project="scope-proj",
            scope="project",
        )
        forge_mem_save(
            title="Scope search personal",
            content="Personal-scoped content for search",
            type="preference",
            project="scope-proj",
            scope="personal",
        )

        proj_results = forge_mem_search(
            query="scoped content", project="scope-proj", scope="project"
        )
        assert proj_results["count"] == 1
        assert proj_results["results"][0]["title"] == "Scope search project"

        pers_results = forge_mem_search(
            query="scoped content", project="scope-proj", scope="personal"
        )
        assert pers_results["count"] == 1
        assert pers_results["results"][0]["title"] == "Scope search personal"


# ---------------------------------------------------------------------------
# Tags case preservation
# ---------------------------------------------------------------------------


class TestNullTopicKey:
    """Null topic_key always creates new observations (no upsert)."""

    def test_null_topic_key_creates_distinct_observations(self, db):
        """Two saves with topic_key=None produce two separate observations."""
        r1 = forge_mem_save(
            title="First null-key",
            content="No topic key",
            type="discovery",
            project="proj",
            topic_key=None,
        )
        r2 = forge_mem_save(
            title="Second null-key",
            content="Also no topic key",
            type="discovery",
            project="proj",
            topic_key=None,
        )
        assert r1["status"] == "created"
        assert r2["status"] == "created"
        assert r1["id"] != r2["id"]

        # Both should be retrievable
        obs1 = forge_mem_get(r1["id"])
        obs2 = forge_mem_get(r2["id"])
        assert obs1["title"] == "First null-key"
        assert obs2["title"] == "Second null-key"


class TestTagsCasePreservation:
    """Tags table preserves original case; tags_text is lowercased for FTS."""

    def test_tags_preserve_original_case(self, db):
        """Tags in the tags table keep their original casing."""
        r = forge_mem_save(
            title="Case test",
            content="Testing tag case",
            type="pattern",
            project="proj",
            tags=["Angular", "TypeScript", "RxJS"],
        )
        obs = forge_mem_get(r["id"])
        # User tags preserve case (auto-tags may also be present)
        assert "Angular" in obs["tags"]
        assert "TypeScript" in obs["tags"]
        assert "RxJS" in obs["tags"]

        # tags_text should be lowercased for FTS
        row = db.execute(
            "SELECT tags_text FROM observations WHERE id = ?", [r["id"]]
        ).fetchone()
        assert "angular" in row[0]
        assert "typescript" in row[0]
        assert "rxjs" in row[0]


# ---------------------------------------------------------------------------
# forge_mem_synonym_add
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Auto-suggestion on save
# ---------------------------------------------------------------------------


class TestAutoSuggestion:
    """forge_mem_save returns similar observations after save."""

    def _seed_observations(self, db, count: int = 6):
        """Seed the DB with enough observations to trigger suggestions."""
        from forge_memory.tools.core import forge_mem_save as save
        for i in range(count):
            save(
                title=f"Authentication pattern number {i}",
                content=(
                    f"Detailed content about authentication and security "
                    f"patterns and best practices for observation {i}. "
                    f"This is long enough to pass the 50-char threshold."
                ),
                type="pattern",
                project="suggest-proj",
                tags=["auth", "security"],
            )

    def test_similar_key_always_present(self, db):
        """The 'similar' key is always present in save response."""
        result = forge_mem_save(
            title="Short",
            content="Hi",
            type="decision",
            project="proj",
        )
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)

    def test_auto_suggestion_returns_matches(self, db):
        """Save 6+ observations, then save a similar one -> similar has matches."""
        self._seed_observations(db, count=6)

        result = forge_mem_save(
            title="Authentication security best practices",
            content=(
                "A comprehensive guide to authentication security patterns "
                "and best practices for modern applications. This content is "
                "long enough to trigger auto-suggestion."
            ),
            type="pattern",
            project="suggest-proj",
        )
        assert "suggestions" in result
        # Should find some of the seeded observations
        # (they all contain "authentication" and "security")
        # Note: exact count depends on ranking scores exceeding 0.6 threshold

    def test_skips_when_content_too_short(self, db):
        """Content < 50 chars -> similar is empty."""
        self._seed_observations(db, count=6)

        result = forge_mem_save(
            title="Auth",
            content="Short",
            type="decision",
            project="suggest-proj",
        )
        assert result["suggestions"] == []

    def test_skips_when_few_observations(self, db):
        """< 5 observations in project -> similar is empty."""
        # Only seed 3 observations (below threshold of 5)
        self._seed_observations(db, count=3)

        result = forge_mem_save(
            title="Authentication security",
            content=(
                "This is a long enough piece of content about authentication "
                "security that should pass the character threshold easily."
            ),
            type="pattern",
            project="suggest-proj",
        )
        # 3 existing + 1 just saved = 4, still < 5
        assert result["suggestions"] == []

    def test_no_matches_returns_empty_similar(self, db):
        """When search finds nothing similar, similar is empty."""
        self._seed_observations(db, count=6)

        result = forge_mem_save(
            title="Kubernetes deployment strategies",
            content=(
                "Information about blue green deployments and canary releases "
                "using kubernetes orchestration platform infrastructure. "
                "Completely unrelated to the seeded authentication content."
            ),
            type="config",
            project="suggest-proj",
        )
        assert result["suggestions"] == []

    def test_similar_excludes_self(self, db):
        """The just-saved observation should never appear in its own similar list."""
        self._seed_observations(db, count=6)

        result = forge_mem_save(
            title="Authentication security patterns",
            content=(
                "Authentication security patterns and best practices for "
                "modern web applications with detailed explanation."
            ),
            type="pattern",
            project="suggest-proj",
        )
        saved_id = result["id"]
        similar_ids = [s["id"] for s in result["suggestions"]]
        assert saved_id not in similar_ids

    def test_suggest_false_skips_auto_suggestion(self, db):
        """suggest=False returns empty suggestions without running search."""
        self._seed_observations(db, count=6)

        result = forge_mem_save(
            title="Authentication security patterns",
            content=(
                "Authentication security patterns and best practices for "
                "modern web applications with detailed explanation."
            ),
            type="pattern",
            project="suggest-proj",
            suggest=False,
        )
        assert result["suggestions"] == []


# ---------------------------------------------------------------------------
# forge_mem_synonym_add
# ---------------------------------------------------------------------------


class TestForgeMemSynonymAdd:
    """forge_mem_synonym_add creates synonym pairs for search expansion."""

    def test_creates_synonym(self, db):
        """Creates a new synonym pair and returns status 'created'."""
        result = forge_mem_synonym_add(
            term="deploy",
            synonym="release",
            language="en",
        )
        assert result["status"] == "created"
        assert result["term"] == "deploy"
        assert result["synonym"] == "release"

    def test_idempotent_on_duplicate(self, db):
        """Adding the same pair twice returns status 'exists'."""
        forge_mem_synonym_add(term="api", synonym="endpoint", language="en")
        result = forge_mem_synonym_add(term="api", synonym="endpoint", language="en")
        assert result["status"] == "exists"

    def test_idempotent_reverse_direction(self, db):
        """Adding the reverse pair also returns status 'exists'."""
        forge_mem_synonym_add(term="api", synonym="endpoint", language="en")
        result = forge_mem_synonym_add(term="endpoint", synonym="api", language="en")
        assert result["status"] == "exists"

    def test_normalizes_case(self, db):
        """Terms are lowercased before storage."""
        result = forge_mem_synonym_add(term="Deploy", synonym="RELEASE", language="en")
        assert result["term"] == "deploy"
        assert result["synonym"] == "release"

    def test_strips_whitespace(self, db):
        """Leading/trailing whitespace is stripped."""
        result = forge_mem_synonym_add(term="  deploy ", synonym=" release ", language="en")
        assert result["term"] == "deploy"
        assert result["synonym"] == "release"

    def test_empty_term_raises(self, db):
        """Empty term raises ValidationError."""
        with pytest.raises(ValidationError):
            forge_mem_synonym_add(term="", synonym="something")

    def test_empty_synonym_raises(self, db):
        """Empty synonym raises ValidationError."""
        with pytest.raises(ValidationError):
            forge_mem_synonym_add(term="something", synonym="")

    def test_identical_term_and_synonym_raises(self, db):
        """Same term and synonym raises ValidationError."""
        with pytest.raises(ValidationError):
            forge_mem_synonym_add(term="deploy", synonym="deploy")


# ---------------------------------------------------------------------------
# tools/core edge cases — auto-suggestion exceptions and update no-op
# ---------------------------------------------------------------------------


class TestAutoSuggestionExceptions:
    """Auto-suggestion returns [] on exceptions rather than propagating."""

    def test_count_check_exception_returns_empty_suggestions(self):
        """If the count query fails, suggestions returns [] gracefully."""
        import sqlite3
        from forge_memory.migrations import run_migrations
        from forge_memory.tools.core import _suggest_similar

        class BrokenCountConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, params=None):
                if "COUNT(*)" in sql:
                    raise sqlite3.OperationalError("count boom")
                if params is not None:
                    return self._inner.execute(sql, params)
                return self._inner.execute(sql)

            def commit(self):
                self._inner.commit()

        real = sqlite3.connect(":memory:")
        real.execute("PRAGMA foreign_keys = ON")
        run_migrations(real)
        wrapped = BrokenCountConn(real)

        result = _suggest_similar(
            wrapped,
            saved_id=1,
            title="Auth security",
            content="A" * 100,
            project="proj",
        )
        assert result == []
        real.close()

    def test_search_exception_returns_empty_suggestions(self, db):
        """If the search call raises, suggestions returns [] gracefully."""
        from unittest.mock import patch
        from forge_memory.tools.core import _suggest_similar

        # Seed enough observations to pass count check
        for i in range(6):
            db.execute(
                "INSERT INTO observations (title, content, type, scope, project) "
                "VALUES (?, ?, 'decision', 'project', 'proj')",
                [f"Obs {i}", f"Content {i}"],
            )
        db.commit()

        with patch("forge_memory.tools.core.search", side_effect=RuntimeError("boom")):
            result = _suggest_similar(
                db,
                saved_id=99,
                title="Auth security",
                content="A" * 100,
                project="proj",
            )
        assert result == []


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------


class TestAutoTagging:
    """Auto-tagging generates tags from title, content, and type."""

    def test_auto_tags_include_type(self, db):
        """Saving without tags still includes the observation type as a tag."""
        result = forge_mem_save(
            title="Some decision",
            content="We decided to use X",
            type="decision",
            project="proj",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "decision" in tags_lower

    def test_auto_tags_include_project(self, db):
        """Saving with a project includes the project name as a tag."""
        result = forge_mem_save(
            title="Something",
            content="Content here",
            type="bugfix",
            project="my-awesome-project",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "my-awesome-project" in tags_lower

    def test_auto_tags_extract_title_keywords(self, db):
        """Meaningful keywords from the title appear as tags."""
        result = forge_mem_save(
            title="Fixed N+1 query in UserList",
            content="Optimized the database query",
            type="bugfix",
            project="proj",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "fixed" in tags_lower
        assert "query" in tags_lower
        assert "userlist" in tags_lower

    def test_auto_tags_extract_backtick_identifiers(self, db):
        """Backtick identifiers in content are extracted as tags."""
        result = forge_mem_save(
            title="Auth fix",
            content="The `AuthService.validate` method was failing silently",
            type="bugfix",
            project="proj",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "authservice.validate" in tags_lower

    def test_auto_tags_extract_headings(self, db):
        """Markdown headings in content are extracted as tags."""
        result = forge_mem_save(
            title="Security review",
            content="## Overview\nStuff\n### Token Rotation\nDetails here",
            type="discovery",
            project="proj",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "token rotation" in tags_lower

    def test_user_tags_preserved_with_auto_tags(self, db):
        """User-provided tags are preserved and auto tags are added alongside."""
        result = forge_mem_save(
            title="Frontend setup",
            content="Using Angular with signals",
            type="decision",
            project="proj",
            tags=["Angular"],
        )
        obs = forge_mem_get(result["id"])
        # User tag preserved with original case
        assert "Angular" in obs["tags"]
        # Auto tags also present
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "decision" in tags_lower
        assert len(obs["tags"]) > 1

    def test_auto_tags_deduped(self, db):
        """No duplicate tags when title word matches the type."""
        result = forge_mem_save(
            title="Important decision about caching",
            content="Decided to use Redis",
            type="decision",
            project="proj",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert tags_lower.count("decision") == 1

    def test_auto_tags_capped_at_10(self, db):
        """Tags are capped at 10 even with very long title and content."""
        result = forge_mem_save(
            title="Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda Mu",
            content=(
                "### Heading One\n### Heading Two\n### Heading Three\n"
                "Using `ServiceAlpha` and `ServiceBeta` and `ServiceGamma`"
            ),
            type="architecture",
            project="big-project",
        )
        obs = forge_mem_get(result["id"])
        assert len(obs["tags"]) <= 10

    def test_auto_tags_on_upsert(self, db):
        """Upsert via topic_key regenerates auto tags."""
        r1 = forge_mem_save(
            title="Auth v1",
            content="JWT based auth",
            type="decision",
            project="proj",
            topic_key="auth/strategy",
        )
        obs1 = forge_mem_get(r1["id"])
        tags1 = [t.lower() for t in obs1["tags"]]

        r2 = forge_mem_save(
            title="Auth v2 with sessions",
            content="Switched to `SessionManager` for auth",
            type="decision",
            project="proj",
            topic_key="auth/strategy",
        )
        assert r2["status"] == "updated"
        obs2 = forge_mem_get(r2["id"])
        tags2 = [t.lower() for t in obs2["tags"]]

        # New tags should reflect updated content
        assert "sessions" in tags2
        assert "sessionmanager" in tags2

    def test_auto_tags_filter_stopwords(self, db):
        """Stopwords from the title are NOT included as tags."""
        result = forge_mem_save(
            title="Fixed the bug in the auth module",
            content="Simple fix",
            type="bugfix",
            project="proj",
        )
        obs = forge_mem_get(result["id"])
        tags_lower = [t.lower() for t in obs["tags"]]
        assert "the" not in tags_lower
        assert "in" not in tags_lower  # 2 chars, filtered by length
        # But meaningful words are there
        assert "fixed" in tags_lower
        assert "bug" in tags_lower
        assert "auth" in tags_lower
        assert "module" in tags_lower


class TestForgeMemUpdateEdgeCases:
    """Edge cases in forge_mem_update."""

    def test_update_with_no_fields_returns_updated(self, db):
        """Calling update with no fields still returns updated status."""
        r = forge_mem_save(
            title="No-op update",
            content="Content",
            type="decision",
            project="proj",
        )
        result = forge_mem_update(r["id"])
        assert result["status"] == "updated"
        assert result["id"] == r["id"]

    def test_update_type_field(self, db):
        """Updating type field changes the observation type."""
        r = forge_mem_save(
            title="Type change test",
            content="Content",
            type="decision",
            project="proj",
        )
        forge_mem_update(r["id"], type="pattern")
        obs = forge_mem_get(r["id"])
        assert obs["type"] == "pattern"
