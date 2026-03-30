"""FTS5 search engine for forge-memory.

Provides full-text search over observations using SQLite FTS5 with compound
ranking: BM25, recency, tag bonus, and quality score.

Ranking formula (v0.2):
    final_score = 0.50 * bm25_norm + 0.20 * recency + 0.15 * tag_bonus + 0.15 * quality
    where:
        bm25_norm   = abs(bm25) / max_bm25  (0..1)
        recency     = max(0, 1.0 - days_since_update / 365)  (0..1)
        tag_bonus   = min(1.0, matching_tags / total_query_terms)  (0..1)
        quality     = quality_score column, default 0.5 if NULL  (0..1)

Search pipeline (v0.2):
    1. tokenize(query) -> list of cleaned tokens
    2. expand_synonyms(conn, tokens) -> list of OR groups
    3. build_fts_query(groups) -> FTS5 MATCH string
    4. batch_fetch_tags for all candidates
    5. 4-factor compound ranking

BM25 note: FTS5's ``bm25()`` returns *negative* scores (more negative =
better match).  We negate them so higher = better.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from forge_memory.models import (
    Observation,
    ObservationType,
    Scope,
    SearchResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Characters that have special meaning in FTS5 MATCH expressions.
_FTS5_SPECIAL = re.compile(r'[*^"():{}<>!\[\]|&+\-\\]')

# Ranking weights (v0.2 — 4-factor compound scoring)
_W_BM25 = 0.50
_W_RECENCY = 0.20
_W_TAG_BONUS = 0.15
_W_QUALITY = 0.15

# Default quality score for observations with NULL quality_score
_QUALITY_DEFAULT = 0.5

# Recency decay window in days (linear decay over 1 year)
_RECENCY_WINDOW_DAYS = 365.0

# Over-fetch multiplier — we pull extra rows from FTS5 so re-ranking in
# Python can surface results that BM25 alone would have pushed below limit.
_OVERFETCH_MULTIPLIER = 3

# Content preview length for SearchResult
_PREVIEW_LEN = 300


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def tokenize(query: str) -> list[str]:
    """Strip FTS5 special characters, split into tokens, and lowercase.

    Returns an empty list for blank or whitespace-only input.

    Examples::

        >>> tokenize('Auth bug "fix"')
        ['auth', 'bug', 'fix']
        >>> tokenize('hello*world')
        ['helloworld']
        >>> tokenize('')
        []
    """
    if not query or not query.strip():
        return []

    cleaned = _FTS5_SPECIAL.sub("", query)
    tokens = [t.strip().lower() for t in cleaned.split() if t.strip()]
    return tokens


def expand_synonyms(
    conn: sqlite3.Connection,
    terms: list[str],
    language: str = "es",
) -> list[list[str]]:
    """For each term, look up synonyms bidirectionally and return OR groups.

    Each group is ``[original_term, synonym1, synonym2, ...]``.
    Tokens with no synonyms return as single-element groups.

    The lookup is bidirectional: if ``(term='auth', synonym='autenticación')``
    exists, searching for ``'autenticación'`` also expands to ``'auth'``.

    Parameters
    ----------
    conn:
        Active database connection.
    terms:
        List of cleaned, lowercased tokens.
    language:
        Language filter for synonyms (default ``"es"``).  Pass ``None``
        to search across all languages.

    Returns
    -------
    list[list[str]]
        One group per input term.

    Examples::

        >>> expand_synonyms(conn, ["auth", "bug"])
        [["auth", "autenticación", "authentication"], ["bug", "error", "fallo"]]
    """
    if not terms:
        return []

    result: list[list[str]] = []
    for term in terms:
        group = [term]

        # Bidirectional lookup
        if language is not None:
            sql = (
                "SELECT synonym FROM synonyms WHERE LOWER(term) = ? AND language = ? "
                "UNION "
                "SELECT term FROM synonyms WHERE LOWER(synonym) = ? AND language = ?"
            )
            params: list = [term, language, term, language]
        else:
            sql = (
                "SELECT synonym FROM synonyms WHERE LOWER(term) = ? "
                "UNION "
                "SELECT term FROM synonyms WHERE LOWER(synonym) = ?"
            )
            params = [term, term]

        try:
            cursor = conn.execute(sql, params)
            for row in cursor.fetchall():
                syn = row[0].lower()
                if syn not in group:
                    group.append(syn)
        except Exception:
            logger.exception("Synonym lookup failed for term=%r", term)

        result.append(group)
    return result


def build_fts_query(term_groups: list[list[str]]) -> str:
    """Build an FTS5 MATCH string from expanded term groups.

    Single-element groups become a quoted term: ``"bug"``.
    Multi-element groups become an OR expression:
    ``("auth" OR "autenticación")``.
    Groups are joined with a space (implicit AND in FTS5).

    Returns an empty string if there are no groups.

    Examples::

        >>> build_fts_query([["auth", "autenticación"], ["bug"]])
        '("auth" OR "autenticación") "bug"'
        >>> build_fts_query([["fix"]])
        '"fix"'
        >>> build_fts_query([])
        ''
    """
    if not term_groups:
        return ""

    parts: list[str] = []
    has_or_group = False
    for group in term_groups:
        if not group:
            continue
        if len(group) == 1:
            parts.append(f'"{group[0]}"')
        else:
            or_expr = " OR ".join(f'"{t}"' for t in group)
            parts.append(f"({or_expr})")
            has_or_group = True

    # FTS5 requires explicit AND between groups when any group uses
    # parenthesized OR expressions.  Implicit AND (space) only works
    # between plain quoted terms.
    joiner = " AND " if has_or_group else " "
    return joiner.join(parts)


def sanitize_fts_query(query: str) -> str:
    """Escape FTS5 special characters and produce a safe MATCH expression.

    Backward-compatible wrapper: calls ``tokenize`` + ``build_fts_query``
    without synonym expansion.

    Examples::

        >>> sanitize_fts_query('auth bug "fix"')
        '"auth" "bug" "fix"'
        >>> sanitize_fts_query('hello*world')
        '"helloworld"'
        >>> sanitize_fts_query('')
        ''
    """
    tokens = tokenize(query)
    if not tokens:
        return ""
    # No synonym expansion — single-element groups
    token_groups = [[t] for t in tokens]
    return build_fts_query(token_groups)


def search(
    conn: sqlite3.Connection,
    query: str,
    project: str,
    type_filter: Optional[str] = None,
    limit: int = 10,
    scope: Optional[str] = None,
    exclude_id: Optional[int] = None,
) -> list[SearchResult]:
    """Full-text search with synonym expansion + 4-factor compound ranking.

    Ranking: BM25(0.50) + recency(0.20) + tag_bonus(0.15) + quality(0.15).

    Parameters
    ----------
    conn:
        Active database connection (sqlite3 or sqlcipher3).
    query:
        Raw search query from the user/agent.
    project:
        Project filter (required — observations are always scoped).
    type_filter:
        Optional observation type filter (e.g. ``"decision"``).
    limit:
        Maximum results to return (default 10).
    scope:
        Optional scope filter (e.g. ``"project"`` or ``"personal"``).
    exclude_id:
        Optional observation ID to exclude (used by auto-suggestion).

    Returns
    -------
    list[SearchResult]
        Ordered by final score descending.  Empty list when *query* is
        blank or no matches found.
    """
    # --- v0.2 pipeline: tokenize → expand → build ---
    tokens = tokenize(query)
    if not tokens:
        return []

    token_groups = expand_synonyms(conn, tokens, language=None)
    safe_query = build_fts_query(token_groups)
    if not safe_query:
        return []

    # ------------------------------------------------------------------
    # Build the SQL — parameterized, never interpolated
    # ------------------------------------------------------------------
    sql = """
        SELECT
            o.id,
            o.title,
            o.content,
            o.type,
            o.scope,
            o.project,
            o.topic_key,
            o.tags_text,
            o.created_at,
            o.updated_at,
            o.feature_slug,
            o.quality_score,
            o.is_active,
            bm25(observations_fts) AS bm25_score
        FROM observations_fts AS fts
        JOIN observations AS o ON o.id = fts.rowid
        WHERE observations_fts MATCH ?
          AND o.project = ?
          AND o.is_active = 1
    """
    params: list = [safe_query, project]

    if exclude_id is not None:
        sql += "  AND o.id != ?\n"
        params.append(exclude_id)

    if type_filter is not None:
        sql += "  AND o.type = ?\n"
        params.append(type_filter)

    if scope is not None:
        sql += "  AND o.scope = ?\n"
        params.append(scope)

    # Order by raw BM25 (ascending, since more negative = better) and
    # over-fetch so Python re-ranking has room to reorder.
    sql += "  ORDER BY bm25_score\n"
    sql += "  LIMIT ?\n"
    params.append(limit * _OVERFETCH_MULTIPLIER)

    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
    except Exception:
        logger.exception("FTS5 search failed for query=%r project=%r", query, project)
        return []

    if not rows:
        return []

    # ------------------------------------------------------------------
    # Batch fetch tags (single IN query — no N+1)
    # ------------------------------------------------------------------
    obs_ids = [row[0] for row in rows]
    tags_map = _batch_fetch_tags(conn, obs_ids)

    # ------------------------------------------------------------------
    # Re-rank with 4-factor compound scoring
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    scored_rows: list[tuple[float, sqlite3.Row | tuple]] = []

    # Find the max BM25 magnitude for normalization (avoid division by zero)
    bm25_values = [abs(row[13]) for row in rows]
    max_bm25 = max(bm25_values) if bm25_values else 1.0
    if max_bm25 == 0:
        max_bm25 = 1.0

    for row in rows:
        # BM25 score: negate (so higher = better), then normalize to 0..1
        raw_bm25 = abs(row[13])
        normalized_bm25 = raw_bm25 / max_bm25

        # Recency boost: linear decay over 365 days
        updated_at_str = row[9]
        recency_boost = _compute_recency_boost(updated_at_str, now)

        # Tag bonus: substring match of original query tokens against tags
        obs_id = row[0]
        tag_bonus = _compute_tag_bonus(tokens, tags_map.get(obs_id, []))

        # Quality score: from observation, default 0.5 if NULL
        quality = row[11] if row[11] is not None else _QUALITY_DEFAULT

        final_score = (
            _W_BM25 * normalized_bm25
            + _W_RECENCY * recency_boost
            + _W_TAG_BONUS * tag_bonus
            + _W_QUALITY * quality
        )
        scored_rows.append((final_score, row))

    # Sort by final score descending
    scored_rows.sort(key=lambda x: x[0], reverse=True)

    # Truncate to requested limit
    scored_rows = scored_rows[:limit]

    # ------------------------------------------------------------------
    # Build SearchResult objects (tags from batch fetch — no extra queries)
    # ------------------------------------------------------------------
    results: list[SearchResult] = []
    for final_score, row in scored_rows:
        obs_id = row[0]

        results.append(
            SearchResult(
                id=obs_id,
                title=row[1],
                content_preview=row[2][:_PREVIEW_LEN] if row[2] else "",
                type=ObservationType(row[3]),
                score=round(final_score, 4),
                tags=tags_map.get(obs_id, []),
                project=row[5],
                topic_key=row[6],
                updated_at=_parse_timestamp(row[9]),
            )
        )

    return results


def get_recent(
    conn: sqlite3.Connection,
    project: str,
    limit: int = 20,
    scope: Optional[str] = None,
) -> list[SearchResult]:
    """Get the most recent observations for a project.

    Parameters
    ----------
    conn:
        Active database connection.
    project:
        Project filter (required).
    limit:
        Maximum results (default 20).

    Returns
    -------
    list[SearchResult]
        Ordered by ``updated_at`` descending.
    """
    sql = """
        SELECT
            id, title, content, type, scope, project,
            topic_key, tags_text, created_at, updated_at,
            feature_slug, quality_score, is_active
        FROM observations
        WHERE project = ?
          AND is_active = 1
    """
    params: list = [project]

    if scope is not None:
        sql += "  AND scope = ?\n"
        params.append(scope)

    sql += "  ORDER BY updated_at DESC\n"
    sql += "  LIMIT ?\n"
    params.append(limit)

    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
    except Exception:
        logger.exception("get_recent failed for project=%r", project)
        return []

    results: list[SearchResult] = []
    for row in rows:
        obs_id = row[0]
        tags = _fetch_tags(conn, obs_id)

        results.append(
            SearchResult(
                id=obs_id,
                title=row[1],
                content_preview=row[2][:_PREVIEW_LEN] if row[2] else "",
                type=ObservationType(row[3]),
                score=0.0,  # no search score for recency listing
                tags=tags,
                project=row[5],
                topic_key=row[6],
                updated_at=_parse_timestamp(row[9]),
            )
        )

    return results


def get_by_id(
    conn: sqlite3.Connection,
    observation_id: int,
) -> Optional[Observation]:
    """Get a single observation by ID with its tags.

    Parameters
    ----------
    conn:
        Active database connection.
    observation_id:
        The observation primary key.

    Returns
    -------
    Observation | None
        Full observation with tags, or ``None`` if not found / inactive.
    """
    sql = """
        SELECT
            id, title, content, type, scope, project,
            topic_key, tags_text, created_at, updated_at,
            feature_slug, quality_score, is_active
        FROM observations
        WHERE id = ?
    """
    try:
        cursor = conn.execute(sql, [observation_id])
        row = cursor.fetchone()
    except Exception:
        logger.exception("get_by_id failed for id=%d", observation_id)
        return None

    if row is None:
        return None

    tags = _fetch_tags(conn, row[0])

    return Observation(
        id=row[0],
        title=row[1],
        content=row[2],
        type=ObservationType(row[3]),
        scope=Scope(row[4]),
        project=row[5],
        topic_key=row[6],
        tags=tags,
        tags_text=row[7] or "",
        created_at=_parse_timestamp(row[8]),
        updated_at=_parse_timestamp(row[9]),
        feature_slug=row[10],
        quality_score=row[11],
        is_active=bool(row[12]),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _batch_fetch_tags(
    conn: sqlite3.Connection,
    observation_ids: list[int],
) -> dict[int, list[str]]:
    """Fetch tags for multiple observations in a single IN query.

    Returns a dict mapping observation_id to list of tags.
    Missing IDs get empty list via ``dict.get(id, [])``.
    """
    if not observation_ids:
        return {}

    placeholders = ",".join("?" * len(observation_ids))
    sql = f"SELECT observation_id, tag FROM tags WHERE observation_id IN ({placeholders})"

    tags_map: dict[int, list[str]] = {}
    try:
        cursor = conn.execute(sql, observation_ids)
        for row in cursor.fetchall():
            obs_id, tag = row[0], row[1]
            if obs_id not in tags_map:
                tags_map[obs_id] = []
            tags_map[obs_id].append(tag)
    except Exception:
        logger.exception("Batch tag fetch failed for %d observations", len(observation_ids))

    return tags_map


def _compute_tag_bonus(
    query_tokens: list[str],
    obs_tags: list[str],
) -> float:
    """Compute tag relevance bonus using substring matching.

    Formula: ``min(1.0, matching_tags / total_query_terms)``

    A tag "matches" if any query token is a substring of the lowercased
    tag. For example, query token ``"auth"`` matches tag
    ``"authentication"`` because ``"auth" in "authentication"`` is true.

    Returns 0.0 if there are no query tokens or no tags.
    """
    if not query_tokens or not obs_tags:
        return 0.0

    lowered_tags = [t.lower() for t in obs_tags]
    matching = 0
    for token in query_tokens:
        for tag in lowered_tags:
            if token in tag:
                matching += 1
                break  # count each query token at most once

    return min(1.0, matching / len(query_tokens))


def _compute_recency_boost(
    updated_at_str: Optional[str],
    now: datetime,
) -> float:
    """Linear recency decay: 1.0 for today, 0.0 after 365 days."""
    if not updated_at_str:
        return 0.0

    updated_at = _parse_timestamp(updated_at_str)
    if updated_at is None:
        return 0.0

    # Ensure both datetimes are comparable
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    delta_days = (now - updated_at).total_seconds() / 86400.0
    return max(0.0, 1.0 - delta_days / _RECENCY_WINDOW_DAYS)


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a SQLite TIMESTAMP string into a datetime object.

    Handles both ``YYYY-MM-DD HH:MM:SS`` and ISO 8601 formats.
    Returns ``None`` on parse failure.
    """
    if not value:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue

    # Last resort: fromisoformat (Python 3.11+ handles most variants)
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        logger.warning("Failed to parse timestamp: %r", value)
        return None


def _fetch_tags(conn: sqlite3.Connection, observation_id: int) -> list[str]:
    """Fetch all tags for an observation."""
    try:
        cursor = conn.execute(
            "SELECT tag FROM tags WHERE observation_id = ?",
            [observation_id],
        )
        return [row[0] for row in cursor.fetchall()]
    except Exception:
        logger.exception("Failed to fetch tags for observation_id=%d", observation_id)
        return []
