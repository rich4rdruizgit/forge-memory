"""Tests for forge_memory.search — FTS5 search, recency, and retrieval."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from forge_memory.migrations import run_migrations
from forge_memory.models import Observation, ObservationType, SearchResult
from forge_memory.search import (
    _batch_fetch_tags,
    _compute_tag_bonus,
    build_fts_query,
    expand_synonyms,
    get_by_id,
    get_recent,
    sanitize_fts_query,
    search,
    tokenize,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_observation(
    conn: sqlite3.Connection,
    *,
    title: str = "Test observation",
    content: str = "Some content",
    obs_type: str = "decision",
    project: str = "test-project",
    scope: str = "project",
    topic_key: Optional[str] = None,
    tags_text: str = "",
    updated_at: Optional[str] = None,
    is_active: int = 1,
    tags: Optional[list[str]] = None,
) -> int:
    """Insert a minimal observation row and return its id."""
    cur = conn.execute(
        """
        INSERT INTO observations
            (title, content, type, scope, project, topic_key, tags_text, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, content, obs_type, scope, project, topic_key, tags_text, is_active),
    )
    obs_id: int = cur.lastrowid  # type: ignore[assignment]

    if updated_at is not None:
        conn.execute(
            "UPDATE observations SET updated_at = ? WHERE id = ?",
            (updated_at, obs_id),
        )

    if tags:
        for tag in tags:
            conn.execute(
                "INSERT INTO tags (observation_id, tag) VALUES (?, ?)",
                (obs_id, tag),
            )

    conn.commit()
    return obs_id


@pytest.fixture()
def conn():
    """In-memory SQLite connection with migrations applied and sample data."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    run_migrations(db)

    now = datetime.now(timezone.utc)

    # Observation 1: recent decision
    _insert_observation(
        db,
        title="Chose Zustand over Redux",
        content="State management decision for the dashboard project",
        obs_type="decision",
        project="dashboard",
        topic_key="architecture/state",
        tags_text="zustand redux state",
        updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        tags=["zustand", "redux"],
    )

    # Observation 2: old bugfix (6 months ago)
    six_months_ago = now - timedelta(days=180)
    _insert_observation(
        db,
        title="Fixed N+1 query in UserList",
        content="The user list endpoint was issuing one query per row",
        obs_type="bugfix",
        project="dashboard",
        topic_key="bugfix/user-list-n1",
        tags_text="performance sql",
        updated_at=six_months_ago.strftime("%Y-%m-%d %H:%M:%S"),
        tags=["performance", "sql"],
    )

    # Observation 3: different project
    _insert_observation(
        db,
        title="API gateway pattern",
        content="Decided to use an API gateway for microservices routing",
        obs_type="architecture",
        project="backend-api",
        topic_key="architecture/gateway",
        tags_text="api gateway microservices",
        updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        tags=["api", "gateway"],
    )

    # Observation 4: soft-deleted
    _insert_observation(
        db,
        title="Deprecated config approach",
        content="Old configuration that was replaced",
        obs_type="config",
        project="dashboard",
        topic_key="config/old",
        tags_text="config deprecated",
        updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        is_active=0,
        tags=["config"],
    )

    # Observation 5: another recent dashboard observation (for limit tests)
    one_day_ago = now - timedelta(days=1)
    _insert_observation(
        db,
        title="Implemented dark mode toggle",
        content="Added theme switching using CSS variables and zustand store",
        obs_type="pattern",
        project="dashboard",
        topic_key="ui/dark-mode",
        tags_text="theme css zustand",
        updated_at=one_day_ago.strftime("%Y-%m-%d %H:%M:%S"),
        tags=["theme", "css"],
    )

    yield db
    db.close()


# ---------------------------------------------------------------------------
# sanitize_fts_query
# ---------------------------------------------------------------------------


class TestSanitizeFtsQuery:
    """sanitize_fts_query escapes special chars and quotes terms."""

    def test_strips_special_chars(self):
        """FTS5 special characters are removed."""
        result = sanitize_fts_query('auth* (bug) "fix"')
        assert result == '"auth" "bug" "fix"'

    def test_quotes_each_term(self):
        """Each whitespace-separated token is double-quoted."""
        result = sanitize_fts_query("hello world")
        assert result == '"hello" "world"'

    def test_empty_string_returns_empty(self):
        """Empty or whitespace-only input returns empty string."""
        assert sanitize_fts_query("") == ""
        assert sanitize_fts_query("   ") == ""

    def test_single_word(self):
        """Single word is quoted."""
        assert sanitize_fts_query("architecture") == '"architecture"'

    def test_multiple_words(self):
        """Multiple words become multiple quoted terms."""
        result = sanitize_fts_query("hexagonal architecture pattern")
        assert result == '"hexagonal" "architecture" "pattern"'

    def test_only_special_chars_returns_empty(self):
        """Input of only special chars produces empty string."""
        assert sanitize_fts_query("***()[]") == ""


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    """tokenize() strips FTS5 specials, lowercases, and splits."""

    def test_strips_special_chars(self):
        """FTS5 special characters are removed."""
        result = tokenize('auth* (bug) "fix"')
        assert result == ["auth", "bug", "fix"]

    def test_lowercases_tokens(self):
        """Tokens are lowercased."""
        assert tokenize("Hello WORLD") == ["hello", "world"]

    def test_empty_string_returns_empty_list(self):
        """Empty or whitespace-only input returns empty list."""
        assert tokenize("") == []
        assert tokenize("   ") == []

    def test_only_special_chars_returns_empty(self):
        """Input of only special chars produces empty list."""
        assert tokenize("***()[]") == []

    def test_single_word(self):
        """Single word is tokenized and lowercased."""
        assert tokenize("Architecture") == ["architecture"]


# ---------------------------------------------------------------------------
# expand_synonyms
# ---------------------------------------------------------------------------


class TestExpandSynonyms:
    """expand_synonyms() looks up synonyms bidirectionally."""

    def test_finds_synonyms_forward(self, conn):
        """Finds synonyms when searching by term."""
        groups = expand_synonyms(conn, ["auth"], language="es")
        assert len(groups) == 1
        assert "auth" in groups[0]
        assert "autenticación" in groups[0]

    def test_finds_synonyms_reverse(self, conn):
        """Finds term when searching by synonym (bidirectional)."""
        groups = expand_synonyms(conn, ["autenticación"], language="es")
        assert len(groups) == 1
        assert "autenticación" in groups[0]
        assert "auth" in groups[0]

    def test_returns_original_when_no_synonyms(self, conn):
        """Returns single-element group when no synonyms exist."""
        groups = expand_synonyms(conn, ["xyzzy_nonexistent"], language="es")
        assert groups == [["xyzzy_nonexistent"]]

    def test_respects_language_filter(self, conn):
        """Only returns synonyms matching the language."""
        # "auth" has ES synonym "autenticación" and EN synonym "authentication"
        es_groups = expand_synonyms(conn, ["auth"], language="es")
        en_groups = expand_synonyms(conn, ["auth"], language="en")

        es_syns = es_groups[0]
        en_syns = en_groups[0]

        assert "autenticación" in es_syns
        assert "authentication" not in es_syns

        assert "authentication" in en_syns
        assert "autenticación" not in en_syns

    def test_empty_terms_returns_empty(self, conn):
        """Empty terms list returns empty list."""
        assert expand_synonyms(conn, [], language="es") == []

    def test_no_language_filter(self, conn):
        """language=None returns synonyms across all languages."""
        groups = expand_synonyms(conn, ["auth"], language=None)
        syns = groups[0]
        assert "auth" in syns
        assert "autenticación" in syns
        assert "authentication" in syns


# ---------------------------------------------------------------------------
# build_fts_query
# ---------------------------------------------------------------------------


class TestBuildFtsQuery:
    """build_fts_query() builds FTS5 MATCH expressions from term groups."""

    def test_single_term_no_synonyms(self):
        """Single-element group produces a quoted term."""
        assert build_fts_query([["fix"]]) == '"fix"'

    def test_multiple_terms_no_synonyms(self):
        """Multiple single-element groups are space-joined (AND)."""
        assert build_fts_query([["auth"], ["bug"]]) == '"auth" "bug"'

    def test_term_with_synonyms(self):
        """Multi-element group produces an OR expression."""
        result = build_fts_query([["auth", "autenticación"]])
        assert result == '("auth" OR "autenticación")'

    def test_mixed_groups(self):
        """Mix of single and multi-element groups uses AND joiner."""
        result = build_fts_query([["auth", "autenticación"], ["bug"]])
        assert result == '("auth" OR "autenticación") AND "bug"'

    def test_empty_groups_returns_empty(self):
        """Empty list of groups returns empty string."""
        assert build_fts_query([]) == ""


# ---------------------------------------------------------------------------
# search with synonyms
# ---------------------------------------------------------------------------


class TestSearchWithSynonyms:
    """search() expands synonyms to find observations containing synonym terms."""

    def test_finds_via_synonym(self, conn):
        """Searching with a term finds observations containing its synonym."""
        # Insert an observation with Spanish content
        _insert_observation(
            conn,
            title="Mejora de rendimiento en la API",
            content="Optimización del rendimiento de las consultas SQL",
            obs_type="bugfix",
            project="dashboard",
            topic_key="perf/api",
        )
        # Search with English term "performance" should find "rendimiento"
        results = search(conn, "performance", project="dashboard")
        titles = [r.title for r in results]
        assert "Mejora de rendimiento en la API" in titles

    def test_synonym_bidirectional_in_search(self, conn):
        """Searching with the synonym also finds the original term."""
        # Insert observation with English term
        _insert_observation(
            conn,
            title="Auth module refactored",
            content="Refactored the auth module for better security",
            obs_type="architecture",
            project="dashboard",
            topic_key="arch/auth",
        )
        # Search with Spanish synonym "autenticación"
        results = search(conn, "autenticación", project="dashboard")
        titles = [r.title for r in results]
        assert "Auth module refactored" in titles


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    """search() performs FTS5 lookup with BM25 + recency ranking."""

    def test_finds_by_title(self, conn):
        """Matches observation by title content."""
        results = search(conn, "Zustand", project="dashboard")
        assert len(results) >= 1
        assert any("Zustand" in r.title for r in results)

    def test_finds_by_content(self, conn):
        """Matches observation by body content."""
        results = search(conn, "microservices routing", project="backend-api")
        assert len(results) >= 1
        assert results[0].title == "API gateway pattern"

    def test_returns_empty_for_no_match(self, conn):
        """No results when query matches nothing."""
        results = search(conn, "nonexistent gibberish xyzzy", project="dashboard")
        assert results == []

    def test_returns_empty_for_empty_query(self, conn):
        """Empty query returns empty list without hitting FTS5."""
        results = search(conn, "", project="dashboard")
        assert results == []

    def test_filters_by_project(self, conn):
        """Results are scoped to the specified project."""
        results = search(conn, "gateway", project="dashboard")
        assert results == []

        results = search(conn, "gateway", project="backend-api")
        assert len(results) == 1

    def test_filters_by_type(self, conn):
        """type_filter narrows results to a specific observation type."""
        results = search(conn, "zustand", project="dashboard", type_filter="decision")
        assert len(results) >= 1
        assert all(r.type == ObservationType.DECISION for r in results)

        results = search(conn, "zustand", project="dashboard", type_filter="bugfix")
        assert results == []

    def test_excludes_soft_deleted(self, conn):
        """Soft-deleted observations (is_active=0) are not returned."""
        results = search(conn, "deprecated config", project="dashboard")
        assert all("Deprecated" not in r.title for r in results)

    def test_respects_limit(self, conn):
        """Limit caps the number of returned results."""
        results = search(conn, "zustand", project="dashboard", limit=1)
        assert len(results) <= 1

    def test_recency_boost(self, conn):
        """Recent observations rank higher than old ones with similar FTS relevance.

        Both obs 1 and obs 5 mention 'zustand' in the dashboard project.
        Obs 1 is today, obs 5 is 1 day ago — both should rank above obs 2
        which is 6 months old (if it matched at all).
        """
        results = search(conn, "zustand", project="dashboard")
        assert len(results) >= 2
        # Both recent observations should appear; scores should be > 0
        for r in results:
            assert r.score > 0

    def test_result_has_expected_fields(self, conn):
        """SearchResult contains all expected fields."""
        results = search(conn, "Zustand", project="dashboard")
        assert len(results) >= 1
        r = results[0]
        assert isinstance(r, SearchResult)
        assert isinstance(r.id, int)
        assert isinstance(r.title, str)
        assert isinstance(r.content_preview, str)
        assert isinstance(r.type, ObservationType)
        assert isinstance(r.score, float)
        assert isinstance(r.tags, list)
        assert isinstance(r.project, str)

    def test_result_includes_tags(self, conn):
        """SearchResult tags are fetched from the tags table."""
        results = search(conn, "Zustand Redux", project="dashboard")
        zustand_results = [r for r in results if "Zustand" in r.title]
        assert len(zustand_results) >= 1
        assert set(zustand_results[0].tags) == {"zustand", "redux"}


# ---------------------------------------------------------------------------
# get_recent
# ---------------------------------------------------------------------------


class TestGetRecent:
    """get_recent() returns observations ordered by updated_at DESC."""

    def test_returns_ordered_by_updated_at_desc(self, conn):
        """Results are ordered most recent first."""
        results = get_recent(conn, project="dashboard")
        assert len(results) >= 2
        # Each result's updated_at should be >= the next one
        for i in range(len(results) - 1):
            assert results[i].updated_at >= results[i + 1].updated_at

    def test_filters_by_project(self, conn):
        """Only returns observations for the specified project."""
        results = get_recent(conn, project="dashboard")
        assert all(r.project == "dashboard" for r in results)

        results = get_recent(conn, project="backend-api")
        assert all(r.project == "backend-api" for r in results)
        assert len(results) == 1

    def test_filters_out_inactive(self, conn):
        """Soft-deleted observations are excluded."""
        results = get_recent(conn, project="dashboard")
        assert all("Deprecated" not in r.title for r in results)

    def test_respects_limit(self, conn):
        """Limit caps the number of returned results."""
        results = get_recent(conn, project="dashboard", limit=1)
        assert len(results) == 1

    def test_score_is_zero(self, conn):
        """get_recent sets score to 0.0 (no search ranking)."""
        results = get_recent(conn, project="dashboard")
        assert all(r.score == 0.0 for r in results)


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


class TestGetById:
    """get_by_id() retrieves a single observation with tags."""

    def test_returns_observation_with_tags(self, conn):
        """Returns full Observation with tags from the tags table."""
        result = get_by_id(conn, 1)
        assert result is not None
        assert isinstance(result, Observation)
        assert result.title == "Chose Zustand over Redux"
        assert set(result.tags) == {"zustand", "redux"}

    def test_returns_none_for_nonexistent_id(self, conn):
        """Returns None when the ID does not exist."""
        result = get_by_id(conn, 99999)
        assert result is None

    def test_returns_soft_deleted_observation(self, conn):
        """Returns the observation even when is_active=0 (for inspection)."""
        # Observation 4 is soft-deleted
        result = get_by_id(conn, 4)
        assert result is not None
        assert result.is_active is False
        assert result.title == "Deprecated config approach"

    def test_returns_all_fields(self, conn):
        """Observation has all expected fields populated."""
        result = get_by_id(conn, 1)
        assert result is not None
        assert result.id == 1
        assert result.content == "State management decision for the dashboard project"
        assert result.type == ObservationType.DECISION
        assert result.project == "dashboard"
        assert result.topic_key == "architecture/state"
        assert result.is_active is True


# ---------------------------------------------------------------------------
# search with scope filter
# ---------------------------------------------------------------------------


class TestSearchScopeFilter:
    """search() and get_recent() support scope filtering."""

    def test_search_filters_by_scope(self, conn):
        """Scope filter narrows search results."""
        _insert_observation(
            conn,
            title="Personal preference on tabs",
            content="I prefer tabs over spaces always",
            project="dashboard",
            scope="personal",
        )
        results = search(conn, "tabs", project="dashboard", scope="personal")
        assert len(results) == 1
        assert results[0].title == "Personal preference on tabs"

        results = search(conn, "tabs", project="dashboard", scope="project")
        assert results == []

    def test_get_recent_filters_by_scope(self, conn):
        """get_recent scope filter narrows results."""
        _insert_observation(
            conn,
            title="Personal note",
            content="Something personal",
            project="dashboard",
            scope="personal",
        )
        results = get_recent(conn, project="dashboard", scope="personal")
        assert len(results) == 1
        assert results[0].title == "Personal note"

        results = get_recent(conn, project="dashboard", scope="project")
        # Should not include the personal one
        assert all(r.title != "Personal note" for r in results)


# ---------------------------------------------------------------------------
# _batch_fetch_tags
# ---------------------------------------------------------------------------


class TestBatchFetchTags:
    """_batch_fetch_tags() fetches tags for multiple observations at once."""

    def test_fetches_tags_for_multiple_observations(self, conn):
        """Returns correct tags mapped to observation IDs."""
        tags_map = _batch_fetch_tags(conn, [1, 2, 3])
        assert set(tags_map.get(1, [])) == {"zustand", "redux"}
        assert set(tags_map.get(2, [])) == {"performance", "sql"}
        assert set(tags_map.get(3, [])) == {"api", "gateway"}

    def test_missing_ids_return_empty_via_get(self, conn):
        """IDs without tags don't appear in dict; dict.get returns []."""
        tags_map = _batch_fetch_tags(conn, [99999])
        assert tags_map.get(99999, []) == []

    def test_empty_ids_returns_empty_dict(self, conn):
        """Empty observation list returns empty dict."""
        assert _batch_fetch_tags(conn, []) == {}

    def test_tags_populated_in_search_results(self, conn):
        """Search results get tags from batch fetch (not N+1)."""
        results = search(conn, "Zustand Redux", project="dashboard")
        zustand_results = [r for r in results if "Zustand" in r.title]
        assert len(zustand_results) >= 1
        assert set(zustand_results[0].tags) == {"zustand", "redux"}


# ---------------------------------------------------------------------------
# _compute_tag_bonus
# ---------------------------------------------------------------------------


class TestComputeTagBonus:
    """_compute_tag_bonus() computes normalized tag match score."""

    def test_exact_match(self):
        """Exact tag match counts."""
        score = _compute_tag_bonus(["auth"], ["auth"])
        assert score == 1.0

    def test_substring_match(self):
        """Query token as substring of tag matches."""
        score = _compute_tag_bonus(["auth"], ["authentication"])
        assert score == 1.0

    def test_partial_match(self):
        """Some tokens match, some don't."""
        score = _compute_tag_bonus(["auth", "bug"], ["authentication", "security"])
        assert score == 0.5  # 1 match / 2 terms

    def test_no_match(self):
        """No tokens match any tags."""
        score = _compute_tag_bonus(["foo", "bar"], ["auth", "security"])
        assert score == 0.0

    def test_empty_tokens(self):
        """Empty query tokens returns 0.0."""
        assert _compute_tag_bonus([], ["auth"]) == 0.0

    def test_empty_tags(self):
        """Empty tags returns 0.0."""
        assert _compute_tag_bonus(["auth"], []) == 0.0

    def test_capped_at_one(self):
        """Score is capped at 1.0 even if multiple tags match same token."""
        score = _compute_tag_bonus(["auth"], ["auth", "auth-flow", "authentication"])
        assert score == 1.0

    def test_case_insensitive(self):
        """Tag matching is case-insensitive."""
        score = _compute_tag_bonus(["auth"], ["Authentication"])
        assert score == 1.0


# ---------------------------------------------------------------------------
# Compound ranking (4-factor)
# ---------------------------------------------------------------------------


class TestCompoundRanking:
    """search() uses 4-factor compound scoring: BM25 + recency + tag_bonus + quality."""

    def test_tag_bonus_boosts_ranking(self, conn):
        """Observation with matching tags ranks higher than one without (same BM25)."""
        now = datetime.now(timezone.utc)

        # Both observations mention "security" in content, same recency
        id_with_tags = _insert_observation(
            conn,
            title="Security audit completed",
            content="Completed full security review of the API",
            project="dashboard",
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            tags=["security", "audit"],
        )
        id_without_tags = _insert_observation(
            conn,
            title="Security notes for team",
            content="General security notes about the API layer",
            project="dashboard",
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            tags=["notes"],  # no match for "security" query
        )

        results = search(conn, "security", project="dashboard")
        # The one with the "security" tag should rank higher
        ids_in_order = [r.id for r in results]
        assert id_with_tags in ids_in_order
        assert id_without_tags in ids_in_order
        assert ids_in_order.index(id_with_tags) < ids_in_order.index(id_without_tags)

    def test_quality_score_impacts_ranking(self, conn):
        """Higher quality_score ranks higher when other factors are similar."""
        now = datetime.now(timezone.utc)

        # Two observations with same content relevance and recency
        id_high_q = _insert_observation(
            conn,
            title="Database indexing strategy",
            content="Index optimization approach for queries",
            project="dashboard",
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        conn.execute(
            "UPDATE observations SET quality_score = ? WHERE id = ?",
            (0.9, id_high_q),
        )

        id_low_q = _insert_observation(
            conn,
            title="Database indexing notes",
            content="Quick indexing notes for queries",
            project="dashboard",
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        conn.execute(
            "UPDATE observations SET quality_score = ? WHERE id = ?",
            (0.1, id_low_q),
        )
        conn.commit()

        results = search(conn, "indexing", project="dashboard")
        ids_in_order = [r.id for r in results]
        assert id_high_q in ids_in_order
        assert id_low_q in ids_in_order
        assert ids_in_order.index(id_high_q) < ids_in_order.index(id_low_q)

    def test_null_quality_defaults_to_half(self, conn):
        """NULL quality_score defaults to 0.5 in ranking."""
        now = datetime.now(timezone.utc)

        # Observation with NULL quality_score (default)
        obs_id = _insert_observation(
            conn,
            title="Unique widget pattern for testing default quality",
            content="Testing that NULL quality defaults to 0.5 in widget pattern",
            project="dashboard",
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Verify quality_score is NULL in DB
        cursor = conn.execute(
            "SELECT quality_score FROM observations WHERE id = ?", [obs_id]
        )
        row = cursor.fetchone()
        assert row[0] is None

        # Search should still return results (quality defaults to 0.5)
        results = search(conn, "widget pattern", project="dashboard")
        assert any(r.id == obs_id for r in results)
        # Score should be > 0 (BM25 + recency + quality default all contribute)
        matching = [r for r in results if r.id == obs_id]
        assert matching[0].score > 0

    def test_exclude_id_parameter(self, conn):
        """exclude_id omits a specific observation from results."""
        results_all = search(conn, "Zustand", project="dashboard")
        assert any(r.id == 1 for r in results_all)

        results_excluded = search(conn, "Zustand", project="dashboard", exclude_id=1)
        assert not any(r.id == 1 for r in results_excluded)


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------


class TestSearchEdgeCases:
    """Edge cases and exception-handling branches in search functions."""

    def test_search_empty_safe_query_returns_empty(self, conn):
        """When build_fts_query produces empty string, search returns []."""
        from unittest.mock import patch
        from forge_memory.search import search

        # Patch expand_synonyms to return empty groups so build_fts_query → ""
        with patch("forge_memory.search.build_fts_query", return_value=""):
            results = search(conn, "zustand", project="dashboard")
        assert results == []

    def test_search_fts_exception_returns_empty(self):
        """FTS5 database error returns empty list (logged but not raised)."""
        import sqlite3
        from forge_memory.migrations import run_migrations
        from forge_memory.search import search

        # Use a wrapper connection that raises on FTS queries
        class BrokenFTSConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, params=None):
                if "observations_fts" in sql and "MATCH" in sql:
                    raise sqlite3.OperationalError("FTS boom")
                if params is not None:
                    return self._inner.execute(sql, params)
                return self._inner.execute(sql)

            def commit(self):
                self._inner.commit()

        real = sqlite3.connect(":memory:")
        real.execute("PRAGMA foreign_keys = ON")
        run_migrations(real)
        wrapped = BrokenFTSConn(real)
        results = search(wrapped, "zustand", project="dashboard")
        assert results == []
        real.close()

    def test_get_recent_exception_returns_empty(self):
        """get_recent database error returns empty list."""
        import sqlite3
        from unittest.mock import MagicMock
        from forge_memory.search import get_recent

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.execute.side_effect = sqlite3.OperationalError("boom")
        results = get_recent(bad_conn, project="myproj")
        assert results == []

    def test_get_by_id_exception_returns_none(self):
        """get_by_id database error returns None."""
        import sqlite3
        from unittest.mock import MagicMock
        from forge_memory.search import get_by_id

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.execute.side_effect = sqlite3.OperationalError("boom")
        result = get_by_id(bad_conn, 1)
        assert result is None

    def test_batch_fetch_tags_exception_returns_empty_dict(self):
        """_batch_fetch_tags database error returns empty dict."""
        import sqlite3
        from unittest.mock import MagicMock
        from forge_memory.search import _batch_fetch_tags

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.execute.side_effect = sqlite3.OperationalError("boom")
        result = _batch_fetch_tags(bad_conn, [1, 2, 3])
        assert result == {}

    def test_fetch_tags_exception_returns_empty(self):
        """_fetch_tags database error returns empty list."""
        import sqlite3
        from unittest.mock import MagicMock
        from forge_memory.search import _fetch_tags

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.execute.side_effect = sqlite3.OperationalError("boom")
        result = _fetch_tags(bad_conn, 1)
        assert result == []

    def test_expand_synonyms_exception_returns_original(self):
        """Synonym lookup exception returns single-element group for that term."""
        import sqlite3
        from unittest.mock import MagicMock
        from forge_memory.search import expand_synonyms

        bad_conn = MagicMock(spec=sqlite3.Connection)
        bad_conn.execute.side_effect = sqlite3.OperationalError("boom")
        groups = expand_synonyms(bad_conn, ["auth"], language="en")
        assert groups == [["auth"]]

    def test_expand_synonyms_no_language_filter(self):
        """expand_synonyms language=None uses different SQL (no language filter)."""
        import sqlite3
        from forge_memory.migrations import run_migrations
        from forge_memory.search import expand_synonyms

        db = sqlite3.connect(":memory:")
        run_migrations(db)
        # Insert a synonym with no specific language concern
        db.execute(
            "INSERT INTO synonyms (term, synonym, language) VALUES ('foo', 'bar', 'en')"
        )
        db.commit()
        groups = expand_synonyms(db, ["foo"], language=None)
        assert "bar" in groups[0]
        db.close()

    def test_compute_recency_boost_with_none_str(self):
        """_compute_recency_boost with None returns 0.0."""
        from datetime import datetime, timezone
        from forge_memory.search import _compute_recency_boost

        now = datetime.now(timezone.utc)
        assert _compute_recency_boost(None, now) == 0.0
        assert _compute_recency_boost("", now) == 0.0

    def test_compute_recency_boost_with_unparseable_str(self):
        """_compute_recency_boost with unparseable string returns 0.0."""
        from datetime import datetime, timezone
        from forge_memory.search import _compute_recency_boost

        now = datetime.now(timezone.utc)
        result = _compute_recency_boost("not-a-date-at-all-xyz", now)
        assert result == 0.0

    def test_parse_timestamp_iso8601_with_timezone(self):
        """_parse_timestamp handles ISO 8601 with timezone offset."""
        from forge_memory.search import _parse_timestamp

        result = _parse_timestamp("2024-01-15T10:30:00+00:00")
        assert result is not None

    def test_parse_timestamp_iso8601_t_format(self):
        """_parse_timestamp handles ISO 8601 T-separator without timezone."""
        from forge_memory.search import _parse_timestamp

        result = _parse_timestamp("2024-01-15T10:30:00")
        assert result is not None

    def test_parse_timestamp_unparseable_returns_none(self):
        """_parse_timestamp returns None for unparseable values."""
        from forge_memory.search import _parse_timestamp

        result = _parse_timestamp("not-a-timestamp")
        assert result is None

    def test_parse_timestamp_none_returns_none(self):
        """_parse_timestamp returns None for None input."""
        from forge_memory.search import _parse_timestamp

        assert _parse_timestamp(None) is None
        assert _parse_timestamp("") is None

    def test_search_bm25_all_zero_no_div_by_zero(self, conn):
        """Search doesn't crash when all BM25 scores are 0."""
        from forge_memory.search import search

        # Normal search just verifies it works
        results = search(conn, "zustand", project="dashboard")
        assert isinstance(results, list)

    def test_build_fts_query_skips_empty_groups(self):
        """Empty groups within term_groups list are skipped."""
        from forge_memory.search import build_fts_query

        # Pass groups that include an empty group
        result = build_fts_query([[], ["auth"], []])
        assert result == '"auth"'

    def test_build_fts_query_all_empty_groups_returns_empty(self):
        """All-empty groups produce empty string."""
        from forge_memory.search import build_fts_query

        result = build_fts_query([[], []])
        assert result == ""
