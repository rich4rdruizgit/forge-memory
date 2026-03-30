"""Core MCP tools for forge-memory.

Plain functions (no decorators) — server.py registers them with FastMCP.
Each function gets the DB connection via ``db.get_db()`` and returns a
JSON-serializable dict.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from forge_memory.db import get_db
from forge_memory.models import (
    NotFoundError,
    ObservationType,
    Scope,
    ValidationError,
)
from forge_memory.search import get_by_id, get_recent, search

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_type(type_str: str) -> ObservationType:
    """Validate and return an ObservationType from a raw string.

    Raises ValidationError if the type is not recognized.
    """
    try:
        return ObservationType(type_str)
    except ValueError:
        valid = [t.value for t in ObservationType]
        raise ValidationError(
            "type",
            f"Invalid type '{type_str}'. Valid types: {', '.join(valid)}",
        )


def _validate_scope(scope_str: str) -> Scope:
    """Validate and return a Scope from a raw string.

    Raises ValidationError if the scope is not recognized.
    """
    try:
        return Scope(scope_str)
    except ValueError:
        valid = [s.value for s in Scope]
        raise ValidationError(
            "scope",
            f"Invalid scope '{scope_str}'. Valid scopes: {', '.join(valid)}",
        )


def _build_tags_text(tags: list[str] | None) -> str:
    """Build the denormalized tags_text string for FTS5 indexing."""
    if not tags:
        return ""
    return " ".join(tag.lower() for tag in tags)


def _insert_tags(conn, observation_id: int, tags: list[str] | None) -> None:
    """Insert tag rows for an observation.

    Tags are stored with their original case in the tags table.
    The denormalized tags_text column (for FTS5) is lowercased separately.
    """
    if not tags:
        return
    for tag in tags:
        conn.execute(
            "INSERT INTO tags (observation_id, tag) VALUES (?, ?)",
            [observation_id, tag],
        )


def _delete_tags(conn, observation_id: int) -> None:
    """Delete all tags for an observation."""
    conn.execute(
        "DELETE FROM tags WHERE observation_id = ?",
        [observation_id],
    )


def _observation_to_dict(obs) -> dict:
    """Convert an Observation dataclass to a JSON-serializable dict."""
    return {
        "id": obs.id,
        "title": obs.title,
        "content": obs.content,
        "type": obs.type.value if hasattr(obs.type, "value") else obs.type,
        "scope": obs.scope.value if hasattr(obs.scope, "value") else obs.scope,
        "project": obs.project,
        "topic_key": obs.topic_key,
        "tags": obs.tags,
        "created_at": str(obs.created_at) if obs.created_at else None,
        "updated_at": str(obs.updated_at) if obs.updated_at else None,
        "feature_slug": obs.feature_slug,
        "quality_score": obs.quality_score,
        "is_active": obs.is_active,
    }


def _search_result_to_dict(sr) -> dict:
    """Convert a SearchResult dataclass to a JSON-serializable dict."""
    return {
        "id": sr.id,
        "title": sr.title,
        "content_preview": sr.content_preview,
        "type": sr.type.value if hasattr(sr.type, "value") else sr.type,
        "score": sr.score,
        "tags": sr.tags,
        "project": sr.project,
        "topic_key": sr.topic_key,
        "updated_at": str(sr.updated_at) if sr.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "because", "but", "and",
    "or", "if", "while", "that", "this", "what", "which", "who",
    "el", "la", "los", "las", "un", "una", "de", "del", "en", "con",
    "por", "para", "que", "se", "su", "es", "son", "fue", "ser",
    "como", "más", "pero", "sus", "le", "ya", "lo", "sin", "sobre",
    "este", "entre", "cuando", "muy", "sin", "sobre", "también",
    "hasta", "desde", "donde", "quien", "todo", "esta", "esto",
})

_MAX_AUTO_TAGS = 10
_BACKTICK_RE = re.compile(r"`([A-Za-z_]\w{2,}(?:\.\w+)?)`")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _auto_generate_tags(
    title: str,
    content: str,
    obs_type: str,
    project: str | None,
) -> list[str]:
    """Generate tags automatically from observation metadata and content.

    Always includes the observation type and project (if provided).
    Extracts keywords from the title, backtick identifiers and markdown
    headings from the content.  Returns lowercased, deduplicated tags
    capped at ``_MAX_AUTO_TAGS``.
    """
    tags: list[str] = []
    seen: set[str] = set()

    def _add(tag: str) -> None:
        low = tag.lower().strip()
        if low and low not in seen:
            seen.add(low)
            tags.append(low)

    # 1. Always include type
    _add(obs_type)

    # 2. Always include project
    if project:
        _add(project)

    # 3. Title keywords (split, filter stopwords, >= 3 chars)
    for word in re.split(r"[\s\-_/]+", title):
        clean = re.sub(r"[^a-zA-Z0-9]", "", word)
        if len(clean) >= 3 and clean.lower() not in _STOPWORDS:
            _add(clean)

    # 4. Backtick identifiers from content
    for match in _BACKTICK_RE.finditer(content):
        ident = match.group(1)
        # Store as single lowered token (e.g. AuthService.validate -> authservice.validate)
        _add(ident)

    # 5. Markdown headings from content
    for match in _HEADING_RE.finditer(content):
        heading = match.group(1).strip()
        if heading.lower() not in seen:
            seen.add(heading.lower())
            tags.append(heading.lower())

    return tags[:_MAX_AUTO_TAGS]


def _merge_tags(
    user_tags: list[str] | None,
    auto_tags: list[str],
) -> list[str]:
    """Merge user-provided tags with auto-generated tags.

    User tags take priority and keep their original case.
    Auto tags fill the remaining slots up to ``_MAX_AUTO_TAGS``.
    """
    if not user_tags:
        return auto_tags[:_MAX_AUTO_TAGS]

    merged = list(user_tags)
    seen = {t.lower() for t in merged}
    for tag in auto_tags:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            merged.append(tag)
        if len(merged) >= _MAX_AUTO_TAGS:
            break
    return merged[:_MAX_AUTO_TAGS]


# ---------------------------------------------------------------------------
# Auto-suggestion helper (v0.2)
# ---------------------------------------------------------------------------

# Thresholds for auto-suggestion
_SUGGEST_MIN_CONTENT_LEN = 50
_SUGGEST_MIN_OBSERVATIONS = 5
_SUGGEST_SCORE_THRESHOLD = 0.6
_SUGGEST_BUDGET_SEC = 0.05  # 50ms wall-clock budget


def _suggest_similar(
    conn,
    saved_id: int,
    title: str,
    content: str,
    project: str,
) -> list[dict]:
    """Find similar observations after save. Returns list of suggestions.

    Skip conditions:
    - content < 50 chars
    - project has < 5 active observations

    Respects a 50ms wall-clock budget.
    """
    if len(content) < _SUGGEST_MIN_CONTENT_LEN:
        return []

    # Check observation count
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE project = ? AND is_active = 1",
            [project],
        )
        count = cursor.fetchone()[0]
        if count < _SUGGEST_MIN_OBSERVATIONS:
            return []
    except Exception:
        logger.exception("Auto-suggestion count check failed")
        return []

    # Run search with time budget
    try:
        start = time.monotonic()
        results = search(
            conn,
            query=title,
            project=project,
            limit=5,
            exclude_id=saved_id,
        )
        elapsed = time.monotonic() - start
        if elapsed > _SUGGEST_BUDGET_SEC:
            return []

        return [
            {"id": r.id, "title": r.title, "score": round(r.score, 4)}
            for r in results
            if r.score > _SUGGEST_SCORE_THRESHOLD
        ]
    except Exception:
        logger.exception("Auto-suggestion search failed (non-fatal)")
        return []


# ---------------------------------------------------------------------------
# Core tools
# ---------------------------------------------------------------------------


def forge_mem_save(
    title: str,
    content: str,
    type: str,
    project: str,
    scope: str = "project",
    topic_key: Optional[str] = None,
    tags: Optional[list[str]] = None,
    feature_slug: Optional[str] = None,
    quality_score: Optional[float] = None,
    suggest: bool = True,
) -> dict:
    """Save an observation to persistent memory.

    Creates a new observation or updates an existing one when ``topic_key``
    already exists for the same project (upsert semantics).  Tags are stored
    both as individual rows and as a denormalized text field for FTS5 search.

    Returns ``{"id": <int>, "status": "created"|"updated"}``.
    """
    obs_type = _validate_type(type)
    obs_scope = _validate_scope(scope)

    # --- Auto-tagging: merge user tags with auto-generated ---
    auto_tags = _auto_generate_tags(title, content, obs_type.value, project)
    merged_tags = _merge_tags(tags, auto_tags)

    tags_text = _build_tags_text(merged_tags)
    conn = get_db()

    # --- Upsert: check if topic_key already exists for this project ---
    existing_id = None
    if topic_key:
        cursor = conn.execute(
            "SELECT id FROM observations "
            "WHERE project = ? AND topic_key = ? AND is_active = 1",
            [project, topic_key],
        )
        row = cursor.fetchone()
        if row:
            existing_id = row[0]

    if existing_id is not None:
        # UPDATE existing observation
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE observations "
            "SET title = ?, content = ?, type = ?, scope = ?, tags_text = ?, "
            "    updated_at = ?, feature_slug = ?, quality_score = ? "
            "WHERE id = ?",
            [title, content, obs_type.value, obs_scope.value, tags_text, now,
             feature_slug, quality_score, existing_id],
        )
        _delete_tags(conn, existing_id)
        _insert_tags(conn, existing_id, merged_tags)
        conn.commit()
        logger.info("Updated observation id=%d topic_key=%r", existing_id, topic_key)
        saved_id = existing_id
        status = "updated"

        # --- Auto-suggestion (v0.2) ---
        suggestions = _suggest_similar(conn, saved_id, title, content, project) if suggest else []
        return {"id": saved_id, "status": status, "suggestions": suggestions}

    # INSERT new observation
    cursor = conn.execute(
        "INSERT INTO observations "
        "(title, content, type, scope, project, topic_key, tags_text, "
        " feature_slug, quality_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [title, content, obs_type.value, obs_scope.value, project, topic_key,
         tags_text, feature_slug, quality_score],
    )
    new_id = cursor.lastrowid
    _insert_tags(conn, new_id, merged_tags)
    conn.commit()
    logger.info("Created observation id=%d topic_key=%r", new_id, topic_key)
    saved_id = new_id
    status = "created"

    # --- Auto-suggestion (v0.2) ---
    suggestions = _suggest_similar(conn, saved_id, title, content, project) if suggest else []
    return {"id": saved_id, "status": status, "suggestions": suggestions}


def forge_mem_search(
    query: str,
    project: str,
    type: Optional[str] = None,
    limit: int = 10,
    scope: Optional[str] = None,
) -> dict:
    """Search observations using full-text search with BM25 + recency ranking.

    Returns matching observations ordered by relevance score. Use ``type``
    to filter by observation type (e.g. ``"decision"``, ``"pattern"``).
    Use ``scope`` to filter by scope (e.g. ``"project"``, ``"personal"``).

    Returns ``{"results": [...], "count": <int>}``.
    """
    if type is not None:
        _validate_type(type)
    if scope is not None:
        _validate_scope(scope)

    conn = get_db()
    results = search(conn, query, project, type_filter=type, limit=limit, scope=scope)
    return {
        "results": [_search_result_to_dict(r) for r in results],
        "count": len(results),
    }


def forge_mem_get(id: int) -> dict:
    """Get a single observation by ID with full content and tags.

    Raises ``NotFoundError`` if the observation does not exist or is inactive.

    Returns the full observation as a dict.
    """
    conn = get_db()
    obs = get_by_id(conn, id)
    if obs is None:
        raise NotFoundError("observation", id)
    return _observation_to_dict(obs)


def forge_mem_update(
    id: int,
    content: Optional[str] = None,
    title: Optional[str] = None,
    type: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """Update an existing observation's fields.

    Only the provided fields are updated — omit a field to leave it unchanged.
    Works on any observation regardless of active status (``get_by_id`` returns
    both active and inactive observations).  Raises ``NotFoundError`` if the
    observation does not exist at all.

    Returns ``{"id": <int>, "status": "updated"}``.
    """
    conn = get_db()

    # Verify observation exists
    obs = get_by_id(conn, id)
    if obs is None:
        raise NotFoundError("observation", id)

    # Build SET clause dynamically for provided fields
    updates: list[str] = []
    params: list = []

    if title is not None:
        updates.append("title = ?")
        params.append(title)

    if content is not None:
        updates.append("content = ?")
        params.append(content)

    if type is not None:
        obs_type = _validate_type(type)
        updates.append("type = ?")
        params.append(obs_type.value)

    if tags is not None:
        tags_text = _build_tags_text(tags)
        updates.append("tags_text = ?")
        params.append(tags_text)

    if not updates and tags is None:
        # Nothing to update
        return {"id": id, "status": "updated"}

    # Always bump updated_at
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    updates.append("updated_at = ?")
    params.append(now)

    params.append(id)
    sql = f"UPDATE observations SET {', '.join(updates)} WHERE id = ?"
    conn.execute(sql, params)

    # Replace tags if provided
    if tags is not None:
        _delete_tags(conn, id)
        _insert_tags(conn, id, tags)

    conn.commit()
    logger.info("Updated observation id=%d fields=%s", id, [u.split(" =")[0] for u in updates])
    return {"id": id, "status": "updated"}


def forge_mem_delete(id: int) -> dict:
    """Soft-delete an observation by setting is_active = 0.

    The observation is not physically removed — it becomes invisible to
    searches and lookups.  Raises ``NotFoundError`` if the observation
    does not exist at all.  Idempotent: calling delete on an already-deleted
    observation succeeds without error.

    Returns ``{"id": <int>, "status": "deleted"}``.
    """
    conn = get_db()

    # Check if observation exists at all (active or inactive)
    cursor = conn.execute(
        "SELECT id, is_active FROM observations WHERE id = ?",
        [id],
    )
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError("observation", id)

    # Already deleted — return success idempotently
    if row[1] == 0:
        logger.info("Observation id=%d already soft-deleted (idempotent)", id)
        return {"id": id, "status": "deleted"}

    conn.execute(
        "UPDATE observations SET is_active = 0 WHERE id = ?",
        [id],
    )
    conn.commit()
    logger.info("Soft-deleted observation id=%d", id)
    return {"id": id, "status": "deleted"}


def forge_mem_synonym_add(
    term: str,
    synonym: str,
    language: str = "en",
) -> dict:
    """Add a synonym pair for search query expansion.

    Both directions are implicit — adding ``("auth", "autenticación")``
    means searching for either term will expand to both.

    Idempotent: adding an existing pair returns ``status: "exists"``.

    Returns ``{"term": ..., "synonym": ..., "status": "created"|"exists"}``.
    """
    if not term or not term.strip():
        raise ValidationError("term", "Term cannot be empty")
    if not synonym or not synonym.strip():
        raise ValidationError("synonym", "Synonym cannot be empty")

    term_clean = term.strip().lower()
    synonym_clean = synonym.strip().lower()

    if term_clean == synonym_clean:
        raise ValidationError("synonym", "Term and synonym cannot be identical")

    conn = get_db()

    # Check if pair already exists (either direction)
    cursor = conn.execute(
        "SELECT id FROM synonyms "
        "WHERE (LOWER(term) = ? AND LOWER(synonym) = ?) "
        "   OR (LOWER(term) = ? AND LOWER(synonym) = ?)",
        [term_clean, synonym_clean, synonym_clean, term_clean],
    )
    if cursor.fetchone():
        return {"term": term_clean, "synonym": synonym_clean, "status": "exists"}

    conn.execute(
        "INSERT INTO synonyms (term, synonym, language) VALUES (?, ?, ?)",
        [term_clean, synonym_clean, language],
    )
    conn.commit()
    return {"term": term_clean, "synonym": synonym_clean, "status": "created"}


def forge_mem_context(
    project: str,
    limit: int = 20,
    scope: Optional[str] = None,
) -> dict:
    """Get the most recent observations for a project.

    Useful for recovering context at the start of a session — returns
    observations ordered by ``updated_at`` descending.
    Use ``scope`` to filter by scope (e.g. ``"project"``, ``"personal"``).

    Returns ``{"observations": [...], "count": <int>}``.
    """
    if scope is not None:
        _validate_scope(scope)

    conn = get_db()
    results = get_recent(conn, project, limit=limit, scope=scope)
    return {
        "observations": [_search_result_to_dict(r) for r in results],
        "count": len(results),
    }
