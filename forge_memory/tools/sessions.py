"""MCP session tools for forge-memory.

Provides session lifecycle management: start, end, and standalone summary.
Sessions track temporal context — when work happened, what was accomplished,
and which feature was being worked on.

These are plain functions (no decorators). The server module registers them
as MCP tools.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from forge_memory.db import get_db
from forge_memory.models import NotFoundError

logger = logging.getLogger(__name__)


def forge_mem_session_start(
    project: str,
    feature_slug: Optional[str] = None,
) -> dict:
    """Start a new working session for a project.

    Creates a session record with the current timestamp. Use
    forge_mem_session_end to close it later with a summary.

    Args:
        project: Project identifier (e.g. "my-app").
        feature_slug: Optional Forge feature slug (e.g. "FEAT-001").

    Returns:
        dict with session_id, status, and started_at.
    """
    conn = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.execute(
        """
        INSERT INTO sessions (project, started_at, feature_slug)
        VALUES (?, ?, ?)
        """,
        [project, now, feature_slug],
    )
    conn.commit()

    session_id = cursor.lastrowid
    logger.info("Session started: id=%d project=%s", session_id, project)

    return {
        "session_id": session_id,
        "status": "started",
        "started_at": now,
    }


def forge_mem_session_end(
    session_id: int,
    summary: str,
) -> dict:
    """End an existing session with a summary of what was accomplished.

    Sets the ended_at timestamp and stores the summary. Raises NotFoundError
    if the session does not exist.

    Args:
        session_id: ID of the session to close.
        summary: Free-text summary of what was done during the session.

    Returns:
        dict with session_id, status, and ended_at.
    """
    conn = get_db()

    # Verify the session exists
    row = conn.execute(
        "SELECT id FROM sessions WHERE id = ?",
        [session_id],
    ).fetchone()

    if row is None:
        raise NotFoundError("session", session_id)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        """
        UPDATE sessions
        SET ended_at = ?, summary = ?
        WHERE id = ?
        """,
        [now, summary, session_id],
    )
    conn.commit()

    logger.info("Session ended: id=%d", session_id)

    return {
        "session_id": session_id,
        "status": "ended",
        "ended_at": now,
    }


def forge_mem_session_summary(
    project: str,
    summary: str,
    feature_slug: Optional[str] = None,
) -> dict:
    """Create a standalone session summary (no prior session_start needed).

    Opens and immediately closes a session in one atomic operation. Use this
    when you want to record what happened in a session without having called
    forge_mem_session_start first.

    Args:
        project: Project identifier (e.g. "my-app").
        summary: Free-text summary of the session.
        feature_slug: Optional Forge feature slug (e.g. "FEAT-001").

    Returns:
        dict with session_id, status, and summary_length.
    """
    conn = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.execute(
        """
        INSERT INTO sessions (project, started_at, ended_at, summary, feature_slug)
        VALUES (?, ?, ?, ?, ?)
        """,
        [project, now, now, summary, feature_slug],
    )
    conn.commit()

    session_id = cursor.lastrowid
    logger.info(
        "Session summary created: id=%d project=%s len=%d",
        session_id,
        project,
        len(summary),
    )

    return {
        "session_id": session_id,
        "status": "completed",
        "summary_length": len(summary),
    }
