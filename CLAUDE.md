# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Implemented, v0.1.0.** The full MCP server is working. `FORGE-MEMORY-MCP-DESIGN.md` remains as reference documentation. 285 tests passing.

## What This Is

`forge-memory` is a Python MCP server that provides persistent memory storage for AI agents. It stores observations (decisions, bugs, patterns, lessons) between coding sessions using SQLite with FTS5 full-text search.

Key design decisions:
- **Semantic upsert**: `topic_key` field prevents duplicates — saves to existing observation instead of creating a new one
- **Progressive search**: Level 1 = FTS5 + Porter stemmer + synonyms; Level 2 = local ONNX embeddings (v2.0); Level 3 = Qdrant/pgvector (v3.0)
- **Soft delete**: `is_active = 0`, audit trail preserved, deleted items hidden from search
- **MCP over stdio**: FastMCP, JSON-RPC transport, compatible with Claude Code and any MCP client
- **Forge integration**: Forge-specific tools for knowledge extraction, grouped search, and feature context

## Architecture

```
forge_memory/
  server.py          → FastMCP instance, tool registration, lifespan (db open/close)
  __main__.py        → CLI entrypoint: python -m forge_memory serve
  db.py              → SQLite connection + migrations
  models.py          → Dataclasses: Observation, Session, Relation, Tag + domain exceptions
  search.py          → FTS5 search, BM25 + recency ranking, synonym expansion
  config.py          → YAML config + env vars
  tools/
    core.py          → forge_mem_save, forge_mem_search, forge_mem_get, forge_mem_update,
                       forge_mem_delete, forge_mem_context, forge_mem_synonym_add
    sessions.py      → forge_mem_session_start, forge_mem_session_end, forge_mem_session_summary
    relations.py     → forge_mem_relate, forge_mem_related
    forge.py         → forge_mem_knowledge_extract, forge_mem_knowledge_search,
                       forge_mem_feature_context
  migrations/        → Schema versioning
tests/
  conftest.py        → Shared fixtures (in-memory DB, tmp DB)
  test_db.py         → DB connection and migration tests
  test_models.py     → Dataclass and domain exception tests
  test_search.py     → FTS5, ranking, synonym expansion
  test_tools_core.py → mem_save upsert, mem_search, mem_get, mem_update, mem_delete, mem_context
  test_tools_sessions.py → Session lifecycle
  test_tools_forge.py    → knowledge_extract, knowledge_search, feature_context
  test_relations.py  → mem_relate, mem_related, graph traversal
  test_config.py     → Config loading and env var overrides
  test_integration.py    → End-to-end MCP server tests
  test_migrations.py → Migration versioning
```

Database stored at `~/.forge-memory/forge.db`.

## Commands

```bash
# Install (uv is the package manager)
uv pip install -e ".[dev]"

# Run server
python -m forge_memory serve

# Tests
pytest
pytest tests/test_db.py           # single module
pytest -k "test_upsert"           # single test

# Lint
ruff check .
ruff format .
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `FORGE_MEMORY_DB` | Path to database (default: `~/.forge-memory/forge.db`) |
| `FORGE_MEMORY_LEVEL` | Search level: 1 (FTS5), 2 (embeddings), 3 (external vector) |
| `FORGE_MEMORY_CONFIG` | Path to config.yaml |

## Core Data Model

`observations` table is the central unit: `id`, `title`, `content`, `type` (bugfix/decision/architecture/discovery/pattern/config/preference), `project`, `topic_key` (upsert key), `quality_score`, `deleted_at`.

Supporting tables: `tags`, `relations` (extends/contradicts/replaces/related/depends_on), `sessions`, `synonyms`, `observations_fts` (virtual FTS5 index).

## MCP Tool Surface

16 tools exposed (all prefixed `forge_mem_`):

**Core**
- `forge_mem_save` — create or upsert by `topic_key`; returns `{"id", "status", "suggestions"}`
- `forge_mem_search` — FTS5 + BM25 + recency ranking; filter by `type` and `scope`
- `forge_mem_get` — full content by ID (search results are truncated)
- `forge_mem_update` — partial update: title, content, type, tags
- `forge_mem_delete` — soft delete (`is_active = 0`)
- `forge_mem_context` — recent observations for a project, ordered by `updated_at`
- `forge_mem_synonym_add` — add synonym pair for query expansion

**Sessions**
- `forge_mem_session_start` — open a session record
- `forge_mem_session_end` — close session with summary
- `forge_mem_session_summary` — atomic open+close in one call (no prior start needed)

**Relations**
- `forge_mem_relate` — create a typed relation between two observations
- `forge_mem_related` — graph traversal up to depth 3

**Forge-specific**
- `forge_mem_knowledge_extract` — parse spec/verify markdown, return typed candidates (does NOT save)
- `forge_mem_knowledge_search` — search grouped by type: decisions, patterns, contracts, lessons, other
- `forge_mem_feature_context` — all observations + sessions + relations for a `feature_slug`

## Testing Strategy

285 tests across 11 test modules. Six critical paths verified:
1. Upsert by `topic_key` prevents duplicates
2. FTS5 search across title, content, and tags
3. Recency ranking (recent items rank higher)
4. Soft delete hides from search
5. Full session lifecycle (start → save → end with summary)
6. Graph traversal via `forge_mem_related` at depth 1–3

In-memory SQLite for unit tests (fast, no file I/O). Tmp path for integration tests.
