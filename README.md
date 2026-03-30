# forge-memory

Persistent memory MCP server for AI agents — stores observations, decisions, and session history across coding sessions using SQLite with FTS5 full-text search.

## What it is

forge-memory is a Model Context Protocol (MCP) server that gives AI agents durable, searchable memory. Agents call tools to save observations (decisions, bug fixes, patterns, discoveries) and retrieve them in future sessions via full-text search. Storage is a local SQLite database at `~/.forge-memory/forge.db` with FTS5 + BM25 ranking and synonym expansion for query-time matching.

## Installation

```bash
git clone https://github.com/your-org/forge-memory.git
cd forge-memory
uv pip install -e .
bash scripts/install.sh
# Restart Claude Code
```

The install script creates `~/.forge-memory/`, writes the MCP server entry to `~/.claude/settings.json`, and verifies the `forge-memory` command is on PATH.

## Configuration

### MCP server entry (auto-written by install.sh)

`~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "forge-memory": {
      "command": "forge-memory",
      "args": ["serve"],
      "env": {
        "FORGE_MEMORY_DB": "~/.forge-memory/forge.db",
        "FORGE_MEMORY_LEVEL": "1"
      }
    }
  }
}
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FORGE_MEMORY_DB` | `~/.forge-memory/forge.db` | Path to the SQLite database |
| `FORGE_MEMORY_LEVEL` | `1` | Search level: `1` = FTS5 only |
| `FORGE_MEMORY_CONFIG` | — | Path to a `config.yaml` override file |

## Tools reference

### Core

| Tool | Description |
|---|---|
| `forge_mem_save` | Create or upsert an observation; pass `topic_key` for upsert semantics |
| `forge_mem_search` | FTS5 full-text search with BM25 + recency ranking |
| `forge_mem_get` | Fetch a single observation by ID (full content, not truncated) |
| `forge_mem_update` | Patch specific fields on an existing observation |
| `forge_mem_delete` | Soft-delete an observation (hidden from search, not physically removed) |
| `forge_mem_context` | Return the most recent observations for a project |
| `forge_mem_synonym_add` | Register a synonym pair for search query expansion |

### Sessions

| Tool | Description |
|---|---|
| `forge_mem_session_start` | Open a session record for a project |
| `forge_mem_session_end` | Close a session with a summary |
| `forge_mem_session_summary` | Open and immediately close a session in one call |

### Relations

| Tool | Description |
|---|---|
| `forge_mem_relate` | Create a typed relation between two observations |
| `forge_mem_related` | Traverse relations from an observation up to depth 3 |

### Forge-specific

| Tool | Description |
|---|---|
| `forge_mem_knowledge_extract` | Parse Forge spec/verify markdown files into typed knowledge candidates |
| `forge_mem_knowledge_search` | Search and bucket results by Forge types (decisions, patterns, contracts, lessons) |
| `forge_mem_feature_context` | Aggregate all observations, sessions, and relations for a feature slug |

**Observation types:** `bugfix`, `decision`, `architecture`, `discovery`, `pattern`, `config`, `preference`

**Relation types:** `extends`, `contradicts`, `replaces`, `related`, `depends_on`

## Usage examples

**Save a decision with upsert:**

```json
{
  "tool": "forge_mem_save",
  "arguments": {
    "title": "Chose SQLite over PostgreSQL for local storage",
    "content": "What: selected SQLite as the storage backend.\nWhy: zero infra requirement, ships with Python, sufficient for single-agent use.\nWhere: db.py, config.py",
    "type": "decision",
    "project": "forge-memory",
    "topic_key": "architecture/storage-backend",
    "tags": ["sqlite", "storage", "architecture"]
  }
}
```

**Search by keyword:**

```json
{
  "tool": "forge_mem_search",
  "arguments": {
    "query": "authentication token expiry",
    "project": "my-app",
    "type": "bugfix",
    "limit": 5
  }
}
```

**Recover context at session start:**

```json
{
  "tool": "forge_mem_context",
  "arguments": {
    "project": "my-app",
    "limit": 20
  }
}
```

**Record a session summary (no prior session_start needed):**

```json
{
  "tool": "forge_mem_session_summary",
  "arguments": {
    "project": "my-app",
    "summary": "Fixed N+1 query in UserList. Root cause: missing select_related on profile FK. Updated UserListView and added regression test.",
    "feature_slug": "FEAT-042"
  }
}
```

**Get full content for a truncated search result:**

```json
{
  "tool": "forge_mem_get",
  "arguments": {
    "id": 17
  }
}
```

## Development

```bash
# Install with dev dependencies
uv pip install -e ".[dev]"

# Run all tests
pytest

# Run a single module
pytest tests/test_db.py

# Run a single test
pytest -k "test_upsert_by_topic_key"

# Lint
ruff check .

# Format
ruff format .
```

## License

MIT
