"""v002 — Search Enhanced: synonyms, relations, seed data.

Creates: synonyms table, relations table with indexes, seed synonym data.
Does NOT modify existing tables (observations, tags, sessions, observations_fts).
"""

import sqlite3

VERSION = 2


def migrate(conn: sqlite3.Connection) -> None:
    """Create v0.2 schema additions."""
    conn.executescript("""
        -- ---------------------------------------------------------------
        -- synonyms — query expansion pairs
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS synonyms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            synonym TEXT NOT NULL,
            language TEXT DEFAULT 'es',
            UNIQUE(term, synonym, language)
        );

        CREATE INDEX IF NOT EXISTS idx_synonyms_term
            ON synonyms(term);
        CREATE INDEX IF NOT EXISTS idx_synonyms_synonym
            ON synonyms(synonym);

        -- ---------------------------------------------------------------
        -- relations — knowledge graph edges
        -- ---------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL
                REFERENCES observations(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL
                REFERENCES observations(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL CHECK (relation_type IN (
                'extends', 'contradicts', 'replaces', 'related', 'depends_on'
            )),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, target_id, relation_type)
        );

        CREATE INDEX IF NOT EXISTS idx_relations_source
            ON relations(source_id);
        CREATE INDEX IF NOT EXISTS idx_relations_target
            ON relations(target_id);
    """)

    # ---------------------------------------------------------------
    # Seed synonym data — ES/EN domain synonyms (~50 pairs)
    # ---------------------------------------------------------------
    seed_synonyms = [
        # Software concepts — ES/EN
        ("performance", "rendimiento", "es"),
        ("performance", "optimización", "es"),
        ("performance", "speed", "en"),
        ("auth", "autenticación", "es"),
        ("auth", "authentication", "en"),
        ("auth", "login", "en"),
        ("database", "base de datos", "es"),
        ("database", "db", "en"),
        ("database", "storage", "en"),
        ("testing", "pruebas", "es"),
        ("testing", "tests", "en"),
        ("testing", "test", "en"),
        ("error", "bug", "en"),
        ("error", "fallo", "es"),
        ("error", "issue", "en"),
        ("pattern", "patrón", "es"),
        ("pattern", "diseño", "es"),
        ("component", "componente", "es"),
        ("component", "module", "en"),
        ("component", "módulo", "es"),
        ("security", "seguridad", "es"),
        ("deploy", "despliegue", "es"),
        ("deploy", "deployment", "en"),
        ("config", "configuración", "es"),
        ("config", "configuration", "en"),
        ("config", "settings", "en"),
        ("users", "usuarios", "es"),
        ("users", "clientes", "es"),
        ("feature", "funcionalidad", "es"),
        ("feature", "característica", "es"),
        ("refactor", "refactorización", "es"),
        ("architecture", "arquitectura", "es"),
        ("dependency", "dependencia", "es"),
        ("dependency", "deps", "en"),
        ("interface", "interfaz", "es"),
        ("service", "servicio", "es"),
        ("repository", "repositorio", "es"),
        ("repository", "repo", "en"),
        ("endpoint", "punto de acceso", "es"),
        ("migration", "migración", "es"),
        ("schema", "esquema", "es"),
        ("validation", "validación", "es"),
        ("encryption", "cifrado", "es"),
        ("cache", "caché", "es"),
        ("logging", "registro", "es"),
        ("session", "sesión", "es"),
        ("workflow", "flujo de trabajo", "es"),
        ("authorization", "autorización", "es"),
        ("query", "consulta", "es"),
        ("middleware", "capa intermedia", "es"),
        ("environment", "entorno", "es"),
        ("environment", "env", "en"),
        ("function", "función", "es"),
        ("implementation", "implementación", "es"),
        ("specification", "especificación", "es"),
        ("specification", "spec", "en"),
    ]

    for term, synonym, language in seed_synonyms:
        conn.execute(
            "INSERT OR IGNORE INTO synonyms (term, synonym, language) VALUES (?, ?, ?)",
            [term, synonym, language],
        )

    conn.commit()
