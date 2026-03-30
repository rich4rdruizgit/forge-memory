"""MCP server setup and tool registration for forge-memory.

Creates a FastMCP instance, registers all tool functions from
``tools/core.py`` and ``tools/sessions.py``, and manages the database
lifecycle via the FastMCP lifespan context manager.

This module is the wiring layer (L6) — it connects config, crypto, db,
and tool functions into a running MCP server.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP

from forge_memory.config import load_config
from forge_memory.db import close_db, open_db
from forge_memory.models import ForgeMemoryError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Manage database lifecycle: open on startup, close on shutdown."""
    logger.info("forge-memory server starting…")
    config = load_config()
    open_db(config)
    logger.info("Database ready.")
    try:
        yield
    finally:
        close_db()
        logger.info("forge-memory server stopped.")


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP("forge-memory", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Error-handling wrapper
# ---------------------------------------------------------------------------


def _handle_error(exc: Exception) -> dict:
    """Convert domain exceptions to MCP-friendly error dicts."""
    if isinstance(exc, NotFoundError):
        return {"error": True, "code": "not_found", "message": str(exc)}
    if isinstance(exc, ValidationError):
        return {"error": True, "code": "validation_error", "message": str(exc)}
    if isinstance(exc, ForgeMemoryError):
        return {"error": True, "code": "forge_memory_error", "message": str(exc)}
    # Unexpected — log full traceback, return generic message
    logger.exception("Unexpected error in tool execution")
    return {"error": True, "code": "internal_error", "message": "An unexpected error occurred."}


# ---------------------------------------------------------------------------
# Tool registration — Core tools
# ---------------------------------------------------------------------------

from forge_memory.tools.core import (  # noqa: E402
    forge_mem_context as _core_context,
    forge_mem_delete as _core_delete,
    forge_mem_get as _core_get,
    forge_mem_save as _core_save,
    forge_mem_search as _core_search,
    forge_mem_synonym_add as _core_synonym_add,
    forge_mem_update as _core_update,
)
from forge_memory.tools.relations import (  # noqa: E402
    forge_mem_relate as _relations_relate,
    forge_mem_related as _relations_related,
)


@mcp.tool()
def forge_mem_save(
    title: str,
    content: str,
    type: str,
    project: str,
    topic_key: Optional[str] = None,
    tags: Optional[list[str]] = None,
    feature_slug: Optional[str] = None,
    quality_score: Optional[float] = None,
    scope: Optional[str] = None,
    suggest: bool = True,
) -> dict:
    """Save an observation to persistent memory.

    Creates a new observation or updates an existing one when ``topic_key``
    already exists for the same project (upsert semantics).  Tags are stored
    both as individual rows and as a denormalized text field for FTS5 search.

    Use ``scope`` to set visibility: "project" (default) or "personal".
    Use ``suggest`` to control auto-suggestion (default True).

    Returns ``{"id": <int>, "status": "created"|"updated", "suggestions": [...]}``.
    """
    try:
        kwargs = dict(
            title=title,
            content=content,
            type=type,
            project=project,
            topic_key=topic_key,
            tags=tags,
            feature_slug=feature_slug,
            quality_score=quality_score,
            suggest=suggest,
        )
        if scope is not None:
            kwargs["scope"] = scope
        return _core_save(**kwargs)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
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
    Use ``scope`` to filter by "project" or "personal".

    Returns ``{"results": [...], "count": <int>}``.
    """
    try:
        kwargs = dict(query=query, project=project, type=type, limit=limit)
        if scope is not None:
            kwargs["scope"] = scope
        return _core_search(**kwargs)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_get(id: int) -> dict:
    """Get a single observation by ID with full content and tags.

    Returns the full observation as a dict. Returns an error if the
    observation does not exist or is inactive.
    """
    try:
        return _core_get(id=id)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_update(
    id: int,
    content: Optional[str] = None,
    title: Optional[str] = None,
    type: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """Update an existing observation's fields.

    Only the provided fields are updated — omit a field to leave it unchanged.
    Returns an error if the observation does not exist or is inactive.

    Returns ``{"id": <int>, "status": "updated"}``.
    """
    try:
        return _core_update(id=id, content=content, title=title, type=type, tags=tags)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_delete(id: int) -> dict:
    """Soft-delete an observation by setting is_active = 0.

    The observation is not physically removed — it becomes invisible to
    searches and lookups.  Returns an error if not found.

    Returns ``{"id": <int>, "status": "deleted"}``.
    """
    try:
        return _core_delete(id=id)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_context(
    project: str,
    limit: int = 20,
    scope: Optional[str] = None,
) -> dict:
    """Get the most recent observations for a project.

    Useful for recovering context at the start of a session — returns
    observations ordered by ``updated_at`` descending.
    Use ``scope`` to filter by "project" or "personal".

    Returns ``{"observations": [...], "count": <int>}``.
    """
    try:
        kwargs = dict(project=project, limit=limit)
        if scope is not None:
            kwargs["scope"] = scope
        return _core_context(**kwargs)
    except Exception as exc:
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Tool registration — Relation tools
# ---------------------------------------------------------------------------


@mcp.tool()
def forge_mem_relate(
    source_id: int,
    target_id: int,
    relation_type: str,
) -> dict:
    """Create a relation between two observations.

    Relation types: extends, contradicts, replaces, related, depends_on.
    Bidirectional types (related, contradicts) can be queried from either end.
    Idempotent: adding an existing relation returns status "exists".

    Returns dict with id, source_id, target_id, relation_type, and status.
    """
    try:
        return _relations_relate(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_related(
    id: int,
    relation_type: Optional[str] = None,
    depth: int = 1,
) -> dict:
    """Get observations related to the given one via graph traversal.

    Follows relations up to the specified depth (1-3). Symmetric types
    (related, contradicts) are followed in both directions. Directional
    types (extends, replaces, depends_on) follow forward only.

    Returns ``{"id": ..., "depth": ..., "relations": [...]}``.
    """
    try:
        return _relations_related(
            id=id,
            relation_type=relation_type,
            depth=depth,
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_synonym_add(
    term: str,
    synonym: str,
    language: str = "en",
) -> dict:
    """Add a synonym pair for search query expansion.

    When a user searches for "term", results matching "synonym" will
    also appear (and vice versa). Useful for multilingual projects
    (e.g. "users" <-> "usuarios") or domain-specific terms.

    Returns ``{"term": ..., "synonym": ..., "status": "created"|"exists"}``.
    """
    try:
        return _core_synonym_add(
            term=term,
            synonym=synonym,
            language=language,
        )
    except Exception as exc:
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Tool registration — Forge tools
# ---------------------------------------------------------------------------

from forge_memory.tools.forge import (  # noqa: E402
    forge_mem_knowledge_extract as _forge_extract,
    forge_mem_knowledge_search as _forge_search,
    forge_mem_feature_context as _forge_context,
)


@mcp.tool()
def forge_mem_knowledge_extract(
    project: str,
    feature_slug: str,
    spec_path: Optional[str] = None,
    verify_path: Optional[str] = None,
) -> dict:
    """Extract knowledge candidates from Forge spec/verify markdown files.

    Reads spec and/or verify files, parses markdown sections, and returns
    typed knowledge candidates (decision, pattern, contract, lesson) with
    confidence scores. Does NOT save -- the agent reviews and calls
    forge_mem_save for each approved candidate.

    Returns {"candidates": [...], "source_files": [...], "candidate_count": N}
    """
    try:
        return _forge_extract(
            project=project,
            feature_slug=feature_slug,
            spec_path=spec_path,
            verify_path=verify_path,
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_knowledge_search(
    project: str,
    query: str,
    types: Optional[list[str]] = None,
    limit: int = 5,
) -> dict:
    """Search knowledge grouped by Forge types: decisions, patterns, contracts, lessons, other.

    Runs a single search query and buckets results by observation type.
    Use ``types`` to request specific buckets only (e.g. ["decisions", "patterns"]).

    Returns {"decisions": [...], "patterns": [...], ..., "total_count": N}
    """
    try:
        return _forge_search(
            project=project,
            query=query,
            types=types,
            limit=limit,
        )
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_feature_context(
    project: str,
    feature_slug: str,
) -> dict:
    """Get all context for a feature: observations, sessions, and related knowledge.

    Aggregates everything tagged with the given feature_slug plus
    depth-1 graph relations from those observations.

    Returns {"feature_slug": ..., "observations": [...], "sessions": [...],
             "relations": [...], "counts": {...}}
    """
    try:
        return _forge_context(
            project=project,
            feature_slug=feature_slug,
        )
    except Exception as exc:
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Tool registration — Session tools
# ---------------------------------------------------------------------------

from forge_memory.tools.sessions import (  # noqa: E402
    forge_mem_session_end as _session_end,
    forge_mem_session_start as _session_start,
    forge_mem_session_summary as _session_summary,
)


@mcp.tool()
def forge_mem_session_start(
    project: str,
    feature_slug: Optional[str] = None,
) -> dict:
    """Start a new working session for a project.

    Creates a session record with the current timestamp. Use
    forge_mem_session_end to close it later with a summary.

    Returns dict with session_id, status, and started_at.
    """
    try:
        return _session_start(project=project, feature_slug=feature_slug)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_session_end(
    session_id: int,
    summary: str,
) -> dict:
    """End an existing session with a summary of what was accomplished.

    Sets the ended_at timestamp and stores the summary. Returns an error
    if the session does not exist.

    Returns dict with session_id, status, and ended_at.
    """
    try:
        return _session_end(session_id=session_id, summary=summary)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool()
def forge_mem_session_summary(
    project: str,
    summary: str,
    feature_slug: Optional[str] = None,
) -> dict:
    """Create a standalone session summary (no prior session_start needed).

    Opens and immediately closes a session in one atomic operation. Use this
    when you want to record what happened in a session without having called
    forge_mem_session_start first.

    Returns dict with session_id, status, and summary_length.
    """
    try:
        return _session_summary(
            project=project, summary=summary, feature_slug=feature_slug,
        )
    except Exception as exc:
        return _handle_error(exc)
