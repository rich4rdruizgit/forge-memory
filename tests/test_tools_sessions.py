"""Tests for forge_memory.tools.sessions — session lifecycle MCP tools."""

from __future__ import annotations

import pytest

from forge_memory.models import NotFoundError
from forge_memory.tools.sessions import (
    forge_mem_session_end,
    forge_mem_session_start,
    forge_mem_session_summary,
)


# ---------------------------------------------------------------------------
# forge_mem_session_start
# ---------------------------------------------------------------------------


class TestForgeMemSessionStart:
    """forge_mem_session_start creates a new session."""

    def test_creates_session(self, db):
        """Returns session_id, status 'started', and started_at."""
        result = forge_mem_session_start(project="test-proj")
        assert result["status"] == "started"
        assert isinstance(result["session_id"], int)
        assert result["session_id"] > 0
        assert "started_at" in result

    def test_creates_session_with_feature_slug(self, db):
        """feature_slug is persisted in the sessions table."""
        result = forge_mem_session_start(
            project="test-proj", feature_slug="FEAT-042"
        )
        row = db.execute(
            "SELECT feature_slug FROM sessions WHERE id = ?",
            [result["session_id"]],
        ).fetchone()
        assert row[0] == "FEAT-042"


# ---------------------------------------------------------------------------
# forge_mem_session_end
# ---------------------------------------------------------------------------


class TestForgeMemSessionEnd:
    """forge_mem_session_end closes an existing session."""

    def test_ends_session_with_summary(self, db):
        """Sets ended_at and summary, returns status 'ended'."""
        start = forge_mem_session_start(project="proj")
        result = forge_mem_session_end(
            session_id=start["session_id"],
            summary="Implemented auth flow",
        )
        assert result["status"] == "ended"
        assert result["session_id"] == start["session_id"]
        assert "ended_at" in result

        # Verify summary in DB
        row = db.execute(
            "SELECT summary, ended_at FROM sessions WHERE id = ?",
            [start["session_id"]],
        ).fetchone()
        assert row[0] == "Implemented auth flow"
        assert row[1] is not None

    def test_raises_not_found_for_missing_session(self, db):
        """NotFoundError raised for non-existent session ID."""
        with pytest.raises(NotFoundError):
            forge_mem_session_end(session_id=99999, summary="nope")


# ---------------------------------------------------------------------------
# forge_mem_session_summary
# ---------------------------------------------------------------------------


class TestForgeMemSessionSummary:
    """forge_mem_session_summary creates a standalone completed session."""

    def test_creates_completed_session(self, db):
        """Creates and immediately closes a session in one call."""
        result = forge_mem_session_summary(
            project="proj",
            summary="Quick session summary content",
        )
        assert result["status"] == "completed"
        assert isinstance(result["session_id"], int)
        assert result["summary_length"] == len("Quick session summary content")

        # Verify both started_at and ended_at are set
        row = db.execute(
            "SELECT started_at, ended_at, summary FROM sessions WHERE id = ?",
            [result["session_id"]],
        ).fetchone()
        assert row[0] is not None  # started_at
        assert row[1] is not None  # ended_at
        assert row[2] == "Quick session summary content"

    def test_with_feature_slug(self, db):
        """feature_slug is persisted for standalone summary."""
        result = forge_mem_session_summary(
            project="proj",
            summary="Session with feature",
            feature_slug="FEAT-007",
        )
        row = db.execute(
            "SELECT feature_slug FROM sessions WHERE id = ?",
            [result["session_id"]],
        ).fetchone()
        assert row[0] == "FEAT-007"


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestConcurrentSessions:
    """Multiple sessions for the same project work independently."""

    def test_two_sessions_same_project(self, db):
        """Starting two sessions for the same project yields distinct IDs."""
        s1 = forge_mem_session_start(project="proj")
        s2 = forge_mem_session_start(project="proj")

        assert s1["session_id"] != s2["session_id"]
        assert s1["status"] == "started"
        assert s2["status"] == "started"

        # End them independently with different summaries
        e1 = forge_mem_session_end(
            session_id=s1["session_id"],
            summary="Session 1 work",
        )
        e2 = forge_mem_session_end(
            session_id=s2["session_id"],
            summary="Session 2 work",
        )
        assert e1["status"] == "ended"
        assert e2["status"] == "ended"

        # Verify each has its own summary
        row1 = db.execute(
            "SELECT summary FROM sessions WHERE id = ?",
            [s1["session_id"]],
        ).fetchone()
        row2 = db.execute(
            "SELECT summary FROM sessions WHERE id = ?",
            [s2["session_id"]],
        ).fetchone()
        assert row1[0] == "Session 1 work"
        assert row2[0] == "Session 2 work"


class TestEndAlreadyEndedSession:
    """Ending a session that already has ended_at set."""

    def test_overwrites_summary_and_ended_at(self, db):
        """Calling session_end on an already-ended session succeeds
        and overwrites the summary and ended_at timestamp."""
        start = forge_mem_session_start(project="proj")
        sid = start["session_id"]

        # End the session a first time
        first_end = forge_mem_session_end(
            session_id=sid,
            summary="First summary",
        )
        assert first_end["status"] == "ended"
        first_ended_at = first_end["ended_at"]

        # End the same session again with a different summary
        second_end = forge_mem_session_end(
            session_id=sid,
            summary="Revised summary",
        )
        assert second_end["status"] == "ended"

        # Verify the summary was overwritten in the DB
        row = db.execute(
            "SELECT summary, ended_at FROM sessions WHERE id = ?",
            [sid],
        ).fetchone()
        assert row[0] == "Revised summary"
        # ended_at should be updated (>= first)
        assert row[1] >= first_ended_at


class TestSessionLifecycle:
    """End-to-end session lifecycle: start -> end."""

    def test_full_lifecycle(self, db):
        """Start a session, then end it with a summary."""
        start = forge_mem_session_start(project="lifecycle-proj")
        assert start["status"] == "started"
        sid = start["session_id"]

        end = forge_mem_session_end(
            session_id=sid,
            summary="Completed feature X with tests",
        )
        assert end["status"] == "ended"
        assert end["session_id"] == sid

        # Verify final DB state
        row = db.execute(
            "SELECT project, started_at, ended_at, summary FROM sessions WHERE id = ?",
            [sid],
        ).fetchone()
        assert row[0] == "lifecycle-proj"
        assert row[1] is not None
        assert row[2] is not None
        assert row[3] == "Completed feature X with tests"
