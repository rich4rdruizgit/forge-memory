"""Forge-specific tools for knowledge extraction, grouped search, and feature context.

These tools bridge forge-memory with the Forge workflow (PRD -> EDD -> TDD -> SDD).
They compose existing infrastructure -- no new dependencies, no schema changes.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from forge_memory.db import get_db
from forge_memory.models import ObservationType
from forge_memory.search import search

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_FILE_SIZE = 1_048_576  # 1MB cap per file read
_MIN_CONFIDENCE = 0.3  # sections below this are noise
_SEARCH_MULTIPLIER = 4  # over-fetch multiplier for grouped search
_PREVIEW_LEN = 300  # content preview for feature_context observations

# Core Forge knowledge types -- these get their own buckets in grouped search
_FORGE_BUCKET_TYPES: dict[str, list[str]] = {
    "decisions": [ObservationType.DECISION.value],
    "patterns": [ObservationType.PATTERN.value],
    "contracts": [ObservationType.CONTRACT.value],
    "lessons": [ObservationType.LESSON.value],
    "discoveries": [ObservationType.DISCOVERY.value],
}

# ---------------------------------------------------------------------------
# Keyword classification maps
# ---------------------------------------------------------------------------

# Each key is an ObservationType value string.
# Each value is a set of lowercase keywords (English + Spanish).
_TYPE_KEYWORDS: dict[str, set[str]] = {
    "decision": {
        "decision", "decided", "chose", "chosen", "selected", "rejected",
        "tradeoff", "trade-off", "alternative", "approach", "why",
        "rationale", "justification",
        "decisión", "decidimos", "elegimos", "rechazamos", "alternativa",
        "enfoque", "justificación", "razón",
    },
    "pattern": {
        "pattern", "convention", "standard", "practice", "approach",
        "structure", "template", "recipe", "how-to", "guideline",
        "patrón", "convención", "estándar", "práctica", "plantilla",
        "estructura", "guía", "receta",
    },
    "contract": {
        "contract", "interface", "api", "endpoint", "signature",
        "schema", "protocol", "spec", "specification", "input", "output",
        "request", "response", "payload", "return",
        "contrato", "interfaz", "esquema", "protocolo", "especificación",
        "entrada", "salida", "respuesta",
    },
    "lesson": {
        "lesson", "learned", "gotcha", "pitfall", "caveat", "warning",
        "surprise", "unexpected", "careful", "watch out", "note",
        "tip", "trick", "insight",
        "lección", "aprendizaje", "cuidado", "ojo",
        "sorpresa", "inesperado", "advertencia", "nota", "truco",
    },
    "discovery": {
        "discovery", "found", "discovered", "realized", "noticed",
        "observation", "finding", "investigation",
        "descubrimiento", "encontramos", "descubrimos", "hallazgo",
        "investigación",
    },
    "bugfix": {
        "bug", "fix", "fixed", "error", "issue", "regression", "broken",
        "crash", "failure", "root cause", "workaround",
        "arreglo", "arreglamos", "problema", "fallo",
        "causa raíz", "solución temporal",
    },
}

# Priority order for tie-breaking (higher index = wins tie)
_TYPE_PRIORITY: list[str] = [
    "bugfix", "discovery", "lesson", "contract", "pattern", "decision",
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_file_safe(path: str) -> tuple[Optional[str], list[str]]:
    """Read a file with 1MB cap.

    Returns (content_or_None, warnings_list).
    """
    warnings: list[str] = []

    # Warn if outside $HOME
    try:
        home = os.path.expanduser("~")
        resolved = os.path.realpath(path)
        if not resolved.startswith(home):
            logger.warning("File path is outside $HOME: %s", path)
            warnings.append(f"Path outside home directory: {path}")
    except Exception:
        pass

    try:
        if not os.path.isfile(path):
            logger.warning("File not found: %s", path)
            warnings.append(f"File not found: {path}")
            return None, warnings

        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read(_MAX_FILE_SIZE)

        if size > _MAX_FILE_SIZE:
            logger.warning("File truncated at 1MB: %s", path)
            warnings.append(f"File truncated to 1MB: {path}")

        if not content.strip():
            warnings.append(f"Empty file: {path}")
            return None, warnings

        return content, warnings
    except PermissionError:
        logger.warning("Cannot read file: %s", path)
        warnings.append(f"Cannot read file: {path}")
        return None, warnings
    except Exception:
        logger.exception("Failed to read file: %s", path)
        warnings.append(f"Cannot read file: {path}")
        return None, warnings


def _parse_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content by ## headings.

    Returns list of (heading, body) tuples.
    Content before the first ## heading is discarded (no heading = no section).
    """
    if not content or not content.strip():
        return []

    raw_sections = re.split(r"^(?=## )", content, flags=re.MULTILINE)

    sections: list[tuple[str, str]] = []
    for raw in raw_sections:
        raw = raw.strip()
        if not raw:
            continue

        lines = raw.split("\n", 1)
        first_line = lines[0].strip()

        if first_line.startswith("## "):
            heading = first_line[3:].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
        else:
            # Content before any ## heading -- skip it
            continue

        if body:
            sections.append((heading, body))

    return sections


def _classify_section(heading: str, body: str) -> tuple[Optional[str], float]:
    """Classify a section into a type and compute confidence.

    Returns (type_str_or_None, confidence).
    Returns (None, 0.0) if no keywords match at all.
    """
    body_preview = body[:200]
    text = f"{heading} {body_preview}".lower()

    scores: dict[str, int] = {}
    for type_str, keywords in _TYPE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > 0:
            scores[type_str] = count

    if not scores:
        # Fallback: unclassifiable sections become discoveries (low confidence)
        return "discovery", 0.3

    # Best type: highest score, then priority for ties
    best_type = max(
        scores.keys(),
        key=lambda t: (
            scores[t],
            _TYPE_PRIORITY.index(t) if t in _TYPE_PRIORITY else -1,
        ),
    )

    # Compute confidence
    confidence = _compute_confidence(heading, body, best_type)
    return best_type, confidence


def _compute_confidence(heading: str, body: str, type_str: str) -> float:
    """Compute heuristic confidence score for a knowledge candidate.

    base(0.2) + heading_match(0.2) + body_length(0.2-0.3) + keyword_density(0-0.3)
    """
    base = 0.2

    # Heading clarity bonus
    heading_lower = heading.lower()
    keywords = _TYPE_KEYWORDS.get(type_str, set())
    heading_match = any(kw in heading_lower for kw in keywords)
    heading_bonus = 0.2 if heading_match else 0.0

    # Body length bonus
    body_len = len(body)
    if body_len > 300:
        length_bonus = 0.3
    elif body_len > 100:
        length_bonus = 0.2
    else:
        length_bonus = 0.0

    # Keyword density
    text = f"{heading} {body[:500]}".lower()
    if keywords:
        match_count = sum(1 for kw in keywords if kw in text)
        density = min(1.0, match_count / max(len(keywords), 1))
    else:
        density = 0.0
    density_bonus = density * 0.3

    return min(1.0, base + heading_bonus + length_bonus + density_bonus)


def _extract_tags(heading: str, body: str, feature_slug: str | None) -> list[str]:
    """Generate tags from section content.

    1. Always include feature_slug as a tag (if provided)
    2. Extract ### sub-headings as tags
    3. Extract backtick-quoted identifiers
    4. Deduplicate and cap at 10
    """
    tags: list[str] = []
    seen: set[str] = set()

    if feature_slug:
        tags.append(feature_slug)
        seen.add(feature_slug.lower())

    # Sub-headings as tags
    sub_headings = re.findall(r"^### (.+)$", body, flags=re.MULTILINE)
    for sh in sub_headings[:3]:
        tag = sh.strip().lower()
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)

    # Backtick identifiers
    identifiers = re.findall(r"`([A-Za-z_]\w{2,}(?:\.\w+)?)`", body)
    for ident in identifiers:
        ident_lower = ident.lower()
        if ident_lower not in seen and len(tags) < 10:
            tags.append(ident_lower)
            seen.add(ident_lower)

    return tags[:10]


def _build_candidate_title(heading: str, type_str: str) -> str:
    """Build a descriptive title for a knowledge candidate."""
    if heading and len(heading) > 3:
        return heading
    type_labels = {
        "decision": "Decision",
        "pattern": "Pattern",
        "contract": "Contract",
        "lesson": "Lesson Learned",
        "discovery": "Discovery",
        "bugfix": "Bug Fix",
    }
    label = type_labels.get(type_str, type_str.title())
    return f"{label} (extracted)"


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def forge_mem_knowledge_extract(
    project: str,
    feature_slug: str,
    spec_path: Optional[str] = None,
    verify_path: Optional[str] = None,
) -> dict:
    """Extract knowledge candidates from Forge spec/verify markdown files.

    Reads the provided file paths, parses markdown into sections,
    classifies each section using keyword heuristics, and returns
    typed KnowledgeCandidates for agent review.

    Does NOT save anything -- candidates are returned for review only.
    """
    if spec_path is None and verify_path is None:
        return {
            "status": "error",
            "message": "At least one of spec_path or verify_path is required",
            "candidates": [],
            "source_files": [],
            "candidate_count": 0,
        }

    candidates: list[dict] = []
    source_files: list[str] = []
    all_warnings: list[str] = []

    for path in [spec_path, verify_path]:
        if path is None:
            continue

        content, warnings = _read_file_safe(path)
        all_warnings.extend(warnings)

        if content is None:
            continue

        source_files.append(path)
        sections = _parse_sections(content)

        for heading, body in sections:
            type_str, confidence = _classify_section(heading, body)
            if type_str is None:
                continue
            if confidence < _MIN_CONFIDENCE:
                continue

            title = _build_candidate_title(heading, type_str)
            tags = _extract_tags(heading, body, feature_slug)

            candidates.append({
                "title": title,
                "content": body,
                "type": type_str,
                "tags": tags,
                "confidence": round(confidence, 2),
                "source_section": heading,
                "source_file": path,
            })

    result: dict = {
        "status": "ok",
        "candidates": candidates,
        "source_files": source_files,
        "candidate_count": len(candidates),
    }
    if all_warnings:
        result["warnings"] = all_warnings
    return result


def forge_mem_knowledge_search(
    project: str,
    query: str,
    types: Optional[list[str]] = None,
    limit: int = 5,
) -> dict:
    """Search knowledge with Forge-aware type grouping.

    Runs a single search(limit=limit*4) query and buckets results
    into Forge knowledge types: decisions, patterns, contracts, lessons, other.
    """
    if not query or not query.strip():
        return {
            "status": "error",
            "message": "query is required and cannot be empty",
        }

    conn = get_db()

    all_results = search(
        conn, query, project, limit=limit * _SEARCH_MULTIPLIER,
    )

    # Initialize all buckets
    buckets: dict[str, list[dict]] = {
        "decisions": [],
        "patterns": [],
        "contracts": [],
        "lessons": [],
        "discoveries": [],
        "other": [],
    }

    for r in all_results:
        type_val = r.type.value if hasattr(r.type, "value") else r.type
        result_dict = {
            "id": r.id,
            "title": r.title,
            "content_preview": r.content_preview,
            "type": type_val,
            "score": r.score,
            "tags": r.tags,
            "topic_key": r.topic_key,
            "updated_at": str(r.updated_at) if r.updated_at else None,
        }

        placed = False
        for bucket_name, bucket_types in _FORGE_BUCKET_TYPES.items():
            if type_val in bucket_types and len(buckets[bucket_name]) < limit:
                buckets[bucket_name].append(result_dict)
                placed = True
                break

        if not placed and len(buckets["other"]) < limit:
            buckets["other"].append(result_dict)

    # Filter by requested types if specified
    if types:
        valid_buckets = set(types) & set(buckets.keys())
        if not valid_buckets:
            # All types invalid -- return all buckets
            valid_buckets = set(buckets.keys())
        buckets = {k: v for k, v in buckets.items() if k in valid_buckets}

    total_count = sum(len(v) for v in buckets.values())

    return {"status": "ok", **buckets, "total_count": total_count}


def forge_mem_feature_context(
    project: str,
    feature_slug: str,
) -> dict:
    """Aggregate all context for a feature: observations, sessions, depth-1 relations.

    Returns empty aggregates (not errors) for non-existent feature_slugs.
    """
    conn = get_db()

    # --- 1. Observations by feature_slug ---
    obs_sql = """
        SELECT id, title, content, type, scope, project,
               topic_key, tags_text, created_at, updated_at,
               feature_slug, quality_score, is_active
        FROM observations
        WHERE project = ? AND feature_slug = ? AND is_active = 1
        ORDER BY updated_at DESC
    """
    cursor = conn.execute(obs_sql, [project, feature_slug])
    obs_rows = cursor.fetchall()

    observations: list[dict] = []
    obs_ids: list[int] = []
    for row in obs_rows:
        obs_ids.append(row[0])
        observations.append({
            "id": row[0],
            "title": row[1],
            "content_preview": row[2][:_PREVIEW_LEN] if row[2] else "",
            "type": row[3],
            "topic_key": row[6],
            "updated_at": str(row[9]) if row[9] else None,
        })

    # --- 2. Sessions by feature_slug ---
    session_sql = """
        SELECT id, project, started_at, ended_at, summary, feature_slug
        FROM sessions
        WHERE project = ? AND feature_slug = ?
        ORDER BY started_at DESC
    """
    cursor = conn.execute(session_sql, [project, feature_slug])
    session_rows = cursor.fetchall()

    sessions: list[dict] = []
    for row in session_rows:
        sessions.append({
            "id": row[0],
            "started_at": str(row[2]) if row[2] else None,
            "ended_at": str(row[3]) if row[3] else None,
            "summary_preview": row[4][:_PREVIEW_LEN] if row[4] else None,
        })

    # --- 3. Depth-1 relations (batch) ---
    relations: list[dict] = []
    if obs_ids:
        placeholders = ",".join("?" * len(obs_ids))

        rel_sql = f"""
            SELECT r.source_id, r.target_id, r.relation_type
            FROM relations r
            WHERE r.source_id IN ({placeholders})
               OR r.target_id IN ({placeholders})
        """
        cursor = conn.execute(rel_sql, obs_ids + obs_ids)
        rel_rows = cursor.fetchall()

        obs_id_set = set(obs_ids)
        neighbor_ids: set[int] = set()
        edge_data: list[tuple[int, int, str]] = []

        for src, tgt, rtype in rel_rows:
            other = tgt if src in obs_id_set else src
            if other not in obs_id_set:
                neighbor_ids.add(other)
                edge_data.append((src, tgt, rtype))

        if neighbor_ids:
            n_placeholders = ",".join("?" * len(neighbor_ids))
            detail_sql = f"""
                SELECT id, title, type
                FROM observations
                WHERE id IN ({n_placeholders}) AND is_active = 1
            """
            cursor = conn.execute(detail_sql, list(neighbor_ids))
            neighbor_map: dict[int, tuple[str, str]] = {}
            for row in cursor.fetchall():
                neighbor_map[row[0]] = (row[1], row[2])

            for src, tgt, rtype in edge_data:
                other = tgt if src in obs_id_set else src
                if other in neighbor_map:
                    n_title, n_type = neighbor_map[other]
                    relations.append({
                        "id": other,
                        "title": n_title,
                        "type": n_type,
                        "relation_type": rtype,
                        "direction": "outgoing" if src in obs_id_set else "incoming",
                    })

    return {
        "feature_slug": feature_slug,
        "observations": observations,
        "sessions": sessions,
        "relations": relations,
        "observation_count": len(observations),
        "session_count": len(sessions),
        "relation_count": len(relations),
    }
