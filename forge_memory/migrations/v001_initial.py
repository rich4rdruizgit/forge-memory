"""v001 — Initial schema for forge-memory v0.1.

Creates: observations, tags, sessions, observations_fts (FTS5),
         FTS5 sync triggers, and all indexes.

Does NOT create: synonyms (v0.2), relations (v0.2).
"""

import sqlite3

VERSION = 1


def migrate(conn: sqlite3.Connection) -> None:
    """Create the v0.1 schema."""
    conn.executescript("""
        -- ---------------------------------------------------------------
        -- observations — central knowledge unit
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN (
                'pattern', 'decision', 'contract', 'component',
                'error', 'lesson', 'module', 'preference', 'discovery',
                'architecture', 'bugfix', 'config'
            )),
            scope TEXT DEFAULT 'project' CHECK (scope IN ('project', 'personal')),
            project TEXT NOT NULL,
            topic_key TEXT,
            tags_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            feature_slug TEXT,
            quality_score REAL,
            is_active BOOLEAN DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_observations_project
            ON observations(project);
        CREATE INDEX IF NOT EXISTS idx_observations_topic
            ON observations(topic_key);
        CREATE INDEX IF NOT EXISTS idx_observations_type
            ON observations(type);
        CREATE INDEX IF NOT EXISTS idx_observations_feature
            ON observations(feature_slug);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_upsert
            ON observations(project, topic_key) WHERE topic_key IS NOT NULL;

        -- ---------------------------------------------------------------
        -- tags — semantic tags per observation
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL
                REFERENCES observations(id) ON DELETE CASCADE,
            tag TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
        CREATE INDEX IF NOT EXISTS idx_tags_observation ON tags(observation_id);

        -- ---------------------------------------------------------------
        -- sessions — working session tracking
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            summary TEXT,
            feature_slug TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);

        -- ---------------------------------------------------------------
        -- FTS5 virtual table — full-text search on observations
        -- ---------------------------------------------------------------
        CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
            title,
            content,
            tags_text,
            content='observations',
            content_rowid='id',
            tokenize='porter unicode61'
        );

        -- ---------------------------------------------------------------
        -- FTS5 sync triggers
        -- ---------------------------------------------------------------
        CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations
        BEGIN
            INSERT INTO observations_fts(rowid, title, content, tags_text)
            VALUES (new.id, new.title, new.content, new.tags_text);
        END;

        CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations
        BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, title, content, tags_text)
            VALUES ('delete', old.id, old.title, old.content, old.tags_text);
        END;

        CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations
        BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, title, content, tags_text)
            VALUES ('delete', old.id, old.title, old.content, old.tags_text);
            INSERT INTO observations_fts(rowid, title, content, tags_text)
            VALUES (new.id, new.title, new.content, new.tags_text);
        END;
    """)
