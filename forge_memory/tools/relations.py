"""Relation tools for forge-memory knowledge graph.

Provides tools to create relations between observations and traverse
the knowledge graph using BFS with cycle detection.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import deque
from typing import Optional

from forge_memory.db import get_db
from forge_memory.models import (
    NotFoundError,
    RelationType,
    SYMMETRIC_RELATIONS,
    ValidationError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_DEPTH = 3
_MAX_RESULTS = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_relation_type(relation_type_str: str) -> RelationType:
    """Validate and return a RelationType from a raw string.

    Raises ValidationError if the type is not recognized.
    """
    try:
        return RelationType(relation_type_str)
    except ValueError:
        valid = [rt.value for rt in RelationType]
        raise ValidationError(
            "relation_type",
            f"Invalid relation_type '{relation_type_str}'. "
            f"Valid types: {', '.join(valid)}",
        )


def _observation_exists(conn: sqlite3.Connection, obs_id: int) -> bool:
    """Check if an observation exists and is active."""
    cursor = conn.execute(
        "SELECT 1 FROM observations WHERE id = ? AND is_active = 1", [obs_id]
    )
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def forge_mem_relate(
    source_id: int,
    target_id: int,
    relation_type: str,
) -> dict:
    """Create a relation between two observations.

    Validates both observations exist and relation_type is valid.
    Idempotent: returns status "exists" if the exact relation already exists.

    Returns ``{"id": ..., "source_id": ..., "target_id": ...,
               "relation_type": ..., "status": "created"|"exists"}``.
    """
    # Validate relation type
    rel_type = _validate_relation_type(relation_type)

    # No self-relations
    if source_id == target_id:
        raise ValidationError(
            "source_id/target_id",
            "Cannot create a self-relation (source_id == target_id).",
        )

    conn = get_db()

    # Validate both observations exist
    if not _observation_exists(conn, source_id):
        raise NotFoundError("observation", source_id)
    if not _observation_exists(conn, target_id):
        raise NotFoundError("observation", target_id)

    # Insert — handle UNIQUE constraint for idempotency
    try:
        cursor = conn.execute(
            "INSERT INTO relations (source_id, target_id, relation_type) "
            "VALUES (?, ?, ?)",
            [source_id, target_id, rel_type.value],
        )
        conn.commit()
        logger.info(
            "Created relation id=%d: %d -[%s]-> %d",
            cursor.lastrowid, source_id, rel_type.value, target_id,
        )
        return {
            "id": cursor.lastrowid,
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": rel_type.value,
            "status": "created",
        }
    except sqlite3.IntegrityError:
        # Duplicate — fetch existing id
        cursor = conn.execute(
            "SELECT id FROM relations "
            "WHERE source_id = ? AND target_id = ? AND relation_type = ?",
            [source_id, target_id, rel_type.value],
        )
        row = cursor.fetchone()
        existing_id = row[0] if row else 0
        logger.info(
            "Relation already exists id=%d: %d -[%s]-> %d",
            existing_id, source_id, rel_type.value, target_id,
        )
        return {
            "id": existing_id,
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": rel_type.value,
            "status": "exists",
        }


def forge_mem_related(
    id: int,
    relation_type: Optional[str] = None,
    depth: int = 1,
) -> dict:
    """Traverse the knowledge graph from an observation using BFS.

    For symmetric relation types (related, contradicts), follows edges in
    both directions.  For directional types (extends, replaces, depends_on),
    only follows forward (source -> target) direction.

    Parameters
    ----------
    id:
        Starting observation ID.
    relation_type:
        Optional filter — only follow this relation type.
        If None, follows all types.
    depth:
        Maximum traversal depth (1-3, default 1).

    Returns ``{"id": ..., "depth": ..., "relations": [...]}``.
    """
    # Validate relation_type if provided
    rel_type_filter: Optional[RelationType] = None
    if relation_type is not None:
        rel_type_filter = _validate_relation_type(relation_type)

    # Validate depth
    if depth < 1:
        raise ValidationError("depth", "depth must be between 1 and 3")
    depth = min(depth, _MAX_DEPTH)

    conn = get_db()

    # Validate observation exists
    if not _observation_exists(conn, id):
        raise NotFoundError("observation", id)

    # BFS traversal
    visited: set[int] = {id}  # don't revisit start node or any visited node
    queue: deque[tuple[int, int]] = deque()  # (obs_id, current_depth)
    queue.append((id, 0))
    results: list[dict] = []

    while queue and len(results) < _MAX_RESULTS:
        current_id, current_depth = queue.popleft()

        if current_depth >= depth:
            continue

        # Find neighbors at current_depth + 1
        neighbors = _get_neighbors(
            conn, current_id, rel_type_filter,
        )

        for neighbor_id, found_rel_type in neighbors:
            if neighbor_id in visited:
                continue
            if len(results) >= _MAX_RESULTS:
                break

            visited.add(neighbor_id)

            # Fetch observation summary
            cursor = conn.execute(
                "SELECT id, title, type FROM observations WHERE id = ?",
                [neighbor_id],
            )
            row = cursor.fetchone()
            if row is None:
                continue

            results.append({
                "id": row[0],
                "title": row[1],
                "type": row[2],
                "relation_type": found_rel_type,
                "depth": current_depth + 1,
            })

            # Enqueue for next depth level
            queue.append((neighbor_id, current_depth + 1))

    return {
        "id": id,
        "depth": depth,
        "relations": results,
    }


def _get_neighbors(
    conn: sqlite3.Connection,
    obs_id: int,
    rel_type_filter: Optional[RelationType],
) -> list[tuple[int, str]]:
    """Get neighbor observation IDs from the relations table.

    For symmetric types, queries both directions.
    For directional types, only follows source -> target.

    Returns list of (neighbor_id, relation_type_str).
    """
    neighbors: list[tuple[int, str]] = []

    if rel_type_filter is not None:
        # Single type — choose query strategy based on symmetry
        if rel_type_filter in SYMMETRIC_RELATIONS:
            # Both directions
            cursor = conn.execute(
                "SELECT source_id, target_id, relation_type FROM relations "
                "WHERE (source_id = ? OR target_id = ?) AND relation_type = ?",
                [obs_id, obs_id, rel_type_filter.value],
            )
            for row in cursor.fetchall():
                other = row[1] if row[0] == obs_id else row[0]
                neighbors.append((other, row[2]))
        else:
            # Directional — forward only
            cursor = conn.execute(
                "SELECT target_id, relation_type FROM relations "
                "WHERE source_id = ? AND relation_type = ?",
                [obs_id, rel_type_filter.value],
            )
            for row in cursor.fetchall():
                neighbors.append((row[0], row[1]))
    else:
        # All types — need to handle each relation's symmetry
        cursor = conn.execute(
            "SELECT source_id, target_id, relation_type FROM relations "
            "WHERE source_id = ? OR target_id = ?",
            [obs_id, obs_id],
        )
        for row in cursor.fetchall():
            src, tgt, rtype = row[0], row[1], row[2]
            try:
                rt = RelationType(rtype)
            except ValueError:
                continue

            if rt in SYMMETRIC_RELATIONS:
                # Follow both directions
                other = tgt if src == obs_id else src
                neighbors.append((other, rtype))
            else:
                # Directional — only if we are the source
                if src == obs_id:
                    neighbors.append((tgt, rtype))

    return neighbors
