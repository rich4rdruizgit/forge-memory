"""Tests for forge_memory.server — FastMCP tool wrappers and error handling."""

from __future__ import annotations

import sqlite3

import pytest

from forge_memory.migrations import run_migrations
import forge_memory.db as db_module


# ---------------------------------------------------------------------------
# lifespan context manager
# ---------------------------------------------------------------------------


class TestLifespan:
    """lifespan opens/closes the database around server execution."""

    @pytest.mark.asyncio
    async def test_lifespan_opens_and_closes_db(self, tmp_path, monkeypatch):
        """lifespan opens a real DB and closes it cleanly."""
        from forge_memory.server import lifespan
        from forge_memory.config import Config

        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("FORGE_MEMORY_DB", db_path)
        monkeypatch.delenv("FORGE_MEMORY_ENCRYPTION", raising=False)

        monkeypatch.setattr(db_module, "_conn", None)

        fake_server = object()
        async with lifespan(fake_server):
            # Inside the context — DB should be open
            conn = db_module._conn
            assert conn is not None

        # After the context — DB should be closed
        assert db_module._conn is None


@pytest.fixture()
def db(monkeypatch):
    """In-memory SQLite DB with migrations applied, patched into forge_memory.db._conn."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    monkeypatch.setattr(db_module, "_conn", conn)
    yield conn
    conn.close()
    monkeypatch.setattr(db_module, "_conn", None)


# ---------------------------------------------------------------------------
# _handle_error
# ---------------------------------------------------------------------------


class TestHandleError:
    """_handle_error converts domain exceptions to MCP-friendly error dicts."""

    def test_not_found_error(self):
        from forge_memory.server import _handle_error
        from forge_memory.models import NotFoundError

        result = _handle_error(NotFoundError("observation", 42))
        assert result["error"] is True
        assert result["code"] == "not_found"
        assert "42" in result["message"]

    def test_validation_error(self):
        from forge_memory.server import _handle_error
        from forge_memory.models import ValidationError

        result = _handle_error(ValidationError("field", "bad value"))
        assert result["error"] is True
        assert result["code"] == "validation_error"
        assert "bad value" in result["message"]

    def test_forge_memory_error(self):
        from forge_memory.server import _handle_error
        from forge_memory.models import ForgeMemoryError

        result = _handle_error(ForgeMemoryError("something went wrong"))
        assert result["error"] is True
        assert result["code"] == "forge_memory_error"
        assert "something went wrong" in result["message"]

    def test_unexpected_error(self):
        from forge_memory.server import _handle_error

        result = _handle_error(RuntimeError("totally unexpected"))
        assert result["error"] is True
        assert result["code"] == "internal_error"
        assert "unexpected" in result["message"].lower()


# ---------------------------------------------------------------------------
# Server tool wrappers — success paths
# ---------------------------------------------------------------------------


class TestServerToolsSave:
    """forge_mem_save server wrapper delegates to core and returns correct shape."""

    def test_save_creates_observation(self, db):
        from forge_memory.server import forge_mem_save

        result = forge_mem_save(
            title="Chose Zustand",
            content="State management decision",
            type="decision",
            project="test-proj",
        )
        assert result["status"] == "created"
        assert isinstance(result["id"], int)

    def test_save_with_optional_scope(self, db):
        from forge_memory.server import forge_mem_save

        result = forge_mem_save(
            title="Personal pref",
            content="Dark mode is better",
            type="preference",
            project="test-proj",
            scope="personal",
        )
        assert result["status"] == "created"

    def test_save_without_scope_uses_default(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_get

        result = forge_mem_save(
            title="Default scope test",
            content="No scope argument",
            type="decision",
            project="test-proj",
        )
        obs = forge_mem_get(id=result["id"])
        assert obs["scope"] == "project"

    def test_save_with_tags(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_get

        result = forge_mem_save(
            title="Tagged obs",
            content="Has tags",
            type="pattern",
            project="test-proj",
            tags=["angular", "rxjs"],
        )
        obs = forge_mem_get(id=result["id"])
        # User tags present (auto-tags may also be added)
        assert "angular" in obs["tags"]
        assert "rxjs" in obs["tags"]

    def test_save_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_save

        result = forge_mem_save(
            title="Bad type",
            content="Content",
            type="INVALID_TYPE",
            project="proj",
        )
        assert result["error"] is True
        assert result["code"] == "validation_error"


class TestServerToolsSearch:
    """forge_mem_search server wrapper."""

    def test_search_returns_results(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_search

        forge_mem_save(
            title="Auth decision",
            content="We use JWT for authentication",
            type="decision",
            project="proj",
        )
        result = forge_mem_search(query="JWT authentication", project="proj")
        assert result["count"] >= 1

    def test_search_with_scope(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_search

        forge_mem_save(
            title="Personal pref",
            content="Personal preference content here",
            type="preference",
            project="proj",
            scope="personal",
        )
        result = forge_mem_search(
            query="preference content", project="proj", scope="personal"
        )
        assert result["count"] >= 1

    def test_search_without_scope_returns_all(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_search

        forge_mem_save(
            title="No scope search test",
            content="Some content for searching",
            type="decision",
            project="proj",
        )
        result = forge_mem_search(query="content searching", project="proj")
        assert result["count"] >= 1

    def test_search_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_search

        result = forge_mem_search(
            query="something", project="proj", type="INVALID_TYPE"
        )
        assert result["error"] is True


class TestServerToolsGet:
    """forge_mem_get server wrapper."""

    def test_get_existing_observation(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_get

        r = forge_mem_save(
            title="Get me",
            content="Here I am",
            type="discovery",
            project="proj",
        )
        obs = forge_mem_get(id=r["id"])
        assert obs["title"] == "Get me"

    def test_get_not_found_returns_error(self, db):
        from forge_memory.server import forge_mem_get

        result = forge_mem_get(id=99999)
        assert result["error"] is True
        assert result["code"] == "not_found"


class TestServerToolsUpdate:
    """forge_mem_update server wrapper."""

    def test_update_returns_updated(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_update

        r = forge_mem_save(
            title="Original",
            content="Content",
            type="decision",
            project="proj",
        )
        result = forge_mem_update(id=r["id"], title="Updated")
        assert result["status"] == "updated"

    def test_update_not_found_returns_error(self, db):
        from forge_memory.server import forge_mem_update

        result = forge_mem_update(id=99999, title="nope")
        assert result["error"] is True
        assert result["code"] == "not_found"


class TestServerToolsDelete:
    """forge_mem_delete server wrapper."""

    def test_delete_returns_deleted(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_delete

        r = forge_mem_save(
            title="Delete me",
            content="Bye",
            type="config",
            project="proj",
        )
        result = forge_mem_delete(id=r["id"])
        assert result["status"] == "deleted"

    def test_delete_not_found_returns_error(self, db):
        from forge_memory.server import forge_mem_delete

        result = forge_mem_delete(id=99999)
        assert result["error"] is True
        assert result["code"] == "not_found"


class TestServerToolsContext:
    """forge_mem_context server wrapper."""

    def test_context_returns_observations(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_context

        forge_mem_save(
            title="Context obs",
            content="Content",
            type="decision",
            project="ctx-proj",
        )
        result = forge_mem_context(project="ctx-proj")
        assert result["count"] >= 1

    def test_context_with_scope(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_context

        forge_mem_save(
            title="Personal context obs",
            content="Content",
            type="preference",
            project="ctx-proj",
            scope="personal",
        )
        result = forge_mem_context(project="ctx-proj", scope="personal")
        assert result["count"] >= 1

    def test_context_without_scope(self, db):
        from forge_memory.server import forge_mem_context

        result = forge_mem_context(project="empty-proj")
        assert result["count"] == 0

    def test_context_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_context

        result = forge_mem_context(project="proj", scope="INVALID_SCOPE")
        assert result["error"] is True


class TestServerToolsRelate:
    """forge_mem_relate and forge_mem_related server wrappers."""

    def _create_obs(self, db, title):
        cursor = db.execute(
            "INSERT INTO observations (title, content, type, scope, project) "
            "VALUES (?, 'Content', 'decision', 'project', 'test-proj')",
            [title],
        )
        db.commit()
        return cursor.lastrowid

    def test_relate_creates_relation(self, db):
        from forge_memory.server import forge_mem_relate

        a = self._create_obs(db, "Obs A")
        b = self._create_obs(db, "Obs B")
        result = forge_mem_relate(source_id=a, target_id=b, relation_type="related")
        assert result["status"] == "created"

    def test_relate_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_relate

        result = forge_mem_relate(source_id=1, target_id=1, relation_type="related")
        assert result["error"] is True

    def test_related_returns_neighbors(self, db):
        from forge_memory.server import forge_mem_relate, forge_mem_related

        a = self._create_obs(db, "Node A")
        b = self._create_obs(db, "Node B")
        forge_mem_relate(source_id=a, target_id=b, relation_type="related")
        result = forge_mem_related(id=a)
        assert result["id"] == a
        assert any(r["id"] == b for r in result["relations"])

    def test_related_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_related

        result = forge_mem_related(id=99999)
        assert result["error"] is True
        assert result["code"] == "not_found"


class TestServerToolsSynonymAdd:
    """forge_mem_synonym_add server wrapper."""

    def test_synonym_add_creates(self, db):
        from forge_memory.server import forge_mem_synonym_add

        result = forge_mem_synonym_add(term="deploy", synonym="release", language="en")
        assert result["status"] == "created"

    def test_synonym_add_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_synonym_add

        result = forge_mem_synonym_add(term="", synonym="release")
        assert result["error"] is True
        assert result["code"] == "validation_error"


class TestServerToolsForge:
    """forge_mem_knowledge_extract, search, and feature_context server wrappers."""

    def test_knowledge_extract_no_paths_error(self, db):
        from forge_memory.server import forge_mem_knowledge_extract

        result = forge_mem_knowledge_extract(
            project="proj", feature_slug="FEAT-01"
        )
        assert result["status"] == "error"

    def test_knowledge_extract_success(self, db, tmp_path):
        from forge_memory.server import forge_mem_knowledge_extract

        spec = tmp_path / "spec.md"
        spec.write_text(
            "## Decision: Use JWT\n\n"
            "We decided to use JWT because of the tradeoffs and alternatives.\n",
            encoding="utf-8",
        )
        result = forge_mem_knowledge_extract(
            project="proj",
            feature_slug="FEAT-01",
            spec_path=str(spec),
        )
        assert result["status"] == "ok"
        assert result["candidate_count"] >= 1

    def test_knowledge_extract_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_knowledge_extract
        from unittest.mock import patch

        with patch("forge_memory.server._forge_extract", side_effect=RuntimeError("boom")):
            result = forge_mem_knowledge_extract(
                project="proj", feature_slug="FEAT-01", spec_path="/any/path"
            )
        assert result["error"] is True

    def test_knowledge_search_returns_buckets(self, db):
        from forge_memory.server import forge_mem_save, forge_mem_knowledge_search

        forge_mem_save(
            title="Auth decision for JWT",
            content="We decided to use JWT authentication approach",
            type="decision",
            project="proj",
        )
        result = forge_mem_knowledge_search(project="proj", query="JWT authentication")
        assert "decisions" in result

    def test_knowledge_search_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_knowledge_search
        from unittest.mock import patch

        with patch("forge_memory.server._forge_search", side_effect=RuntimeError("boom")):
            result = forge_mem_knowledge_search(project="proj", query="something")
        assert result["error"] is True

    def test_feature_context_returns_data(self, db):
        from forge_memory.server import forge_mem_feature_context

        result = forge_mem_feature_context(project="proj", feature_slug="FEAT-01")
        assert result["feature_slug"] == "FEAT-01"

    def test_feature_context_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_feature_context
        from unittest.mock import patch

        with patch("forge_memory.server._forge_context", side_effect=RuntimeError("boom")):
            result = forge_mem_feature_context(project="proj", feature_slug="FEAT-01")
        assert result["error"] is True


class TestServerToolsSessions:
    """forge_mem_session_start, end, and summary server wrappers."""

    def test_session_start_creates(self, db):
        from forge_memory.server import forge_mem_session_start

        result = forge_mem_session_start(project="proj")
        assert result["status"] == "started"
        assert isinstance(result["session_id"], int)

    def test_session_start_with_feature_slug(self, db):
        from forge_memory.server import forge_mem_session_start

        result = forge_mem_session_start(project="proj", feature_slug="FEAT-01")
        assert result["status"] == "started"

    def test_session_start_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_session_start
        from unittest.mock import patch

        with patch("forge_memory.server._session_start", side_effect=RuntimeError("boom")):
            result = forge_mem_session_start(project="proj")
        assert result["error"] is True

    def test_session_end_closes(self, db):
        from forge_memory.server import forge_mem_session_start, forge_mem_session_end

        r = forge_mem_session_start(project="proj")
        result = forge_mem_session_end(session_id=r["session_id"], summary="Done.")
        assert result["status"] == "ended"

    def test_session_end_not_found_returns_error(self, db):
        from forge_memory.server import forge_mem_session_end

        result = forge_mem_session_end(session_id=99999, summary="Summary.")
        assert result["error"] is True
        assert result["code"] == "not_found"

    def test_session_summary_creates(self, db):
        from forge_memory.server import forge_mem_session_summary

        result = forge_mem_session_summary(project="proj", summary="This is a summary.")
        assert result["status"] == "completed"

    def test_session_summary_with_feature_slug(self, db):
        from forge_memory.server import forge_mem_session_summary

        result = forge_mem_session_summary(
            project="proj", summary="Summary.", feature_slug="FEAT-01"
        )
        assert result["status"] == "completed"

    def test_session_summary_error_returns_error_dict(self, db):
        from forge_memory.server import forge_mem_session_summary
        from unittest.mock import patch

        with patch("forge_memory.server._session_summary", side_effect=RuntimeError("boom")):
            result = forge_mem_session_summary(project="proj", summary="Summary.")
        assert result["error"] is True
