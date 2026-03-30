# Forge Memory — MCP Server Design

> Documento de diseño técnico para `forge-memory`: un servidor MCP que persiste conocimiento cifrado entre sesiones de agentes AI.
> Proyecto separado de Forge. Valor standalone.

---

## 1. Visión

El problema es simple: los agentes AI arrancan cada sesión de cero. Todo lo que aprendieron — patrones del proyecto, decisiones de arquitectura, errores resueltos, contratos entre módulos — se pierde cuando se cierra la ventana.

`forge-memory` es un servidor MCP local que:

- **Persiste conocimiento** entre sesiones (observaciones, decisiones, patrones, lecciones)
- **Cifra todo por defecto** — pensado para fintech donde datos sensibles no pueden estar en texto plano
- **Integra con Forge** como consumer principal, pero funciona standalone con cualquier agente MCP
- **Búsqueda progresiva** — arranca con FTS5 (resuelve el 90% de los casos), escala a embeddings locales, y opcionalmente a backends vectoriales externos

Un solo archivo `.db`, un solo proceso, cero servicios externos. Así de simple.

---

## 2. Arquitectura

### Stack

| Componente | Tecnología | Justificación |
|-----------|-----------|---------------|
| Lenguaje | Python 3.11+ | MCP SDK maduro, ecosystem SQLite fuerte |
| Storage | SQLite + SQLCipher | Cifrado transparente AES-256, un solo archivo |
| Search L1 | FTS5 + porter stemmer + sinónimos + tags | Cubre el 90% sin dependencias extras |
| Search L2 (futuro) | sqlite-vec + ONNX `all-MiniLM-L6-v2` | Embeddings 100% locales, ~80MB |
| Search L3 (futuro) | Qdrant / pgvector | Opt-in, nunca default |
| Protocolo | MCP tools over stdio | Estándar, compatible con Claude Code y cualquier cliente MCP |

### Principios de Diseño

1. **Un archivo, un proceso, cero servicios externos** — `forge.db` es todo lo que necesitás
2. **El 90% nunca pasa de Nivel 1** — FTS5 bien tuneado resuelve casi todo
3. **Cada nivel se activa solo cuando el anterior no alcanza** — progresión explícita, no automática
4. **Cifrado por defecto, no opt-in** — si tenés que acordarte de activarlo, ya perdiste
5. **El agente es el orquestador** — el MCP provee storage y búsqueda, el agente decide qué guardar

### Diagrama de Componentes

```
┌─────────────────────────────────────────────┐
│  Claude Code / Forge / Cualquier cliente MCP │
└────────────────────┬────────────────────────┘
                     │ stdio (JSON-RPC)
                     ▼
┌─────────────────────────────────────────────┐
│              forge-memory server             │
│  ┌─────────┐ ┌──────────┐ ┌──────────────┐ │
│  │  Tools   │ │  Search  │ │   Relations  │ │
│  │  (MCP)   │ │  Engine  │ │   (Graph)    │ │
│  └────┬─────┘ └────┬─────┘ └──────┬───────┘ │
│       └─────┬──────┘───────────────┘         │
│             ▼                                │
│  ┌─────────────────────┐  ┌──────────────┐  │
│  │   SQLite + FTS5     │  │   Crypto     │  │
│  │   (SQLCipher)       │  │   (Keychain) │  │
│  └─────────────────────┘  └──────────────┘  │
└─────────────────────────────────────────────┘
                     │
                     ▼
            ~/.forge-memory/forge.db
```

---

## 3. Modelo de Datos

### Tabla: `observations`

La unidad central de conocimiento. Todo lo que el agente aprende se guarda acá.

```sql
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN (
        'pattern', 'decision', 'contract', 'component',
        'error', 'lesson', 'module', 'preference', 'discovery'
    )),
    scope TEXT DEFAULT 'project' CHECK (scope IN ('project', 'personal')),
    project TEXT NOT NULL,
    topic_key TEXT,              -- clave estable para upsert (ej: 'auth/repository-pattern')
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    feature_slug TEXT,           -- qué feature de Forge creó esto
    quality_score REAL,          -- score de relevancia (0.0 - 1.0)
    is_active BOOLEAN DEFAULT 1  -- soft delete
);

CREATE INDEX idx_observations_project ON observations(project);
CREATE INDEX idx_observations_topic ON observations(topic_key);
CREATE INDEX idx_observations_type ON observations(type);
CREATE INDEX idx_observations_feature ON observations(feature_slug);
CREATE UNIQUE INDEX idx_observations_upsert ON observations(project, topic_key)
    WHERE topic_key IS NOT NULL;
```

**Upsert semántico**: si `topic_key` ya existe para el mismo `project`, se actualiza en lugar de crear duplicado. Esto permite que temas que evolucionan (ej: decisiones de arquitectura) se mantengan actualizados sin crear basura.

### Tabla: `tags`

Tags semánticos para mejorar búsqueda en Nivel 1. El agente genera 3-5 tags al guardar.

```sql
CREATE TABLE tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    tag TEXT NOT NULL
);

CREATE INDEX idx_tags_tag ON tags(tag);
CREATE INDEX idx_tags_observation ON tags(observation_id);
```

### Tabla: `relations`

Knowledge graph ligero. Conexiones explícitas entre observaciones.

```sql
CREATE TABLE relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'extends', 'contradicts', 'replaces', 'related', 'depends_on'
    )),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE INDEX idx_relations_source ON relations(source_id);
CREATE INDEX idx_relations_target ON relations(target_id);
```

### Tabla: `sessions`

Tracking de sesiones de trabajo para contexto temporal.

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    summary TEXT,
    feature_slug TEXT
);

CREATE INDEX idx_sessions_project ON sessions(project);
```

### Tabla: `synonyms` (Nivel 1)

Tabla de sinónimos para expandir queries de búsqueda.

```sql
CREATE TABLE synonyms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    synonym TEXT NOT NULL,
    language TEXT DEFAULT 'es'
);

CREATE INDEX idx_synonyms_term ON synonyms(term);
```

### FTS5 Virtual Table

```sql
CREATE VIRTUAL TABLE observations_fts USING fts5(
    title,
    content,
    tags_text,
    content='observations',
    content_rowid='id',
    tokenize='porter unicode61'
);
```

**Sincronización FTS5** — triggers automáticos:

```sql
CREATE TRIGGER observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, title, content, tags_text)
    VALUES (new.id, new.title, new.content, '');
END;

CREATE TRIGGER observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, title, content, tags_text)
    VALUES ('delete', old.id, old.title, old.content, '');
END;

CREATE TRIGGER observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, title, content, tags_text)
    VALUES ('delete', old.id, old.title, old.content, '');
    INSERT INTO observations_fts(rowid, title, content, tags_text)
    VALUES (new.id, new.title, new.content, '');
END;
```

**Nota sobre `tags_text`**: al insertar/actualizar tags, se actualiza un campo `tags_text` denormalizado en la FTS con los tags concatenados (ej: `"auth repository pattern solid"`). Esto permite que FTS5 busque sobre tags sin joins.

---

## 4. MCP Tools

### Core Tools (v0.1)

| Tool | Descripción | Parámetros |
|------|-------------|------------|
| `forge_mem_save` | Guardar observación (insert o upsert si topic_key existe) | `title`, `content`, `type`, `project`, `topic_key?`, `tags[]?`, `feature_slug?`, `quality_score?` |
| `forge_mem_search` | Búsqueda full-text con ranking | `query`, `project`, `type?`, `limit?` (default 10) |
| `forge_mem_get` | Obtener observación completa por ID | `id` |
| `forge_mem_update` | Actualizar observación existente | `id`, `content?`, `title?`, `type?`, `tags[]?` |
| `forge_mem_delete` | Soft delete (is_active = 0) | `id` |
| `forge_mem_context` | Observaciones recientes de un proyecto | `project`, `limit?` (default 20) |

### Relation Tools (v0.2)

| Tool | Descripción | Parámetros |
|------|-------------|------------|
| `forge_mem_relate` | Crear relación entre observaciones | `source_id`, `target_id`, `relation_type` |
| `forge_mem_related` | Obtener observaciones relacionadas | `id`, `relation_type?`, `depth?` (default 1) |

### Session Tools (v0.1)

| Tool | Descripción | Parámetros |
|------|-------------|------------|
| `forge_mem_session_start` | Iniciar sesión de trabajo | `project`, `feature_slug?` |
| `forge_mem_session_end` | Cerrar sesión con summary | `session_id`, `summary` |
| `forge_mem_session_summary` | Guardar resumen de sesión | `project`, `summary`, `feature_slug?` |

### Forge-specific Tools (v0.3)

| Tool | Descripción | Parámetros |
|------|-------------|------------|
| `forge_mem_knowledge_extract` | Extraer candidatos de conocimiento de una feature | `project`, `feature_slug`, `spec_path?`, `verify_path?` |
| `forge_mem_knowledge_search` | Buscar conocimiento con contexto Forge | `project`, `query`, `types[]?` |
| `forge_mem_feature_context` | Todo lo relacionado a un feature slug | `project`, `feature_slug` |

### Ejemplo de flujo `forge_mem_save`

```python
# Input del agente:
forge_mem_save(
    title="Repository pattern para acceso a datos",
    content="Usamos Repository pattern con interfaces...",
    type="pattern",
    project="mi-app-fintech",
    topic_key="data-access/repository-pattern",
    tags=["repository", "data-access", "clean-architecture", "solid"],
    feature_slug="FEAT-042-user-management"
)

# Respuesta:
{
    "id": 42,
    "status": "created",  # o "updated" si topic_key existía
    "similar": [           # candidatos para relaciones
        {"id": 15, "title": "Decidimos usar interfaces para repos", "score": 0.82}
    ]
}
```

---

## 5. Cifrado

### Estrategia: SQLCipher

SQLCipher es la extensión estándar de la industria para cifrado transparente de SQLite. Cifra TODO: datos, índices, FTS5, journal, WAL. AES-256 en modo CBC/HMAC-SHA512.

### Flujo de Key Management

```
Primera ejecución:
1. Generar passphrase aleatoria (32 bytes, base64)
2. Guardar en keychain del sistema:
   - macOS: Keychain Access (via `security` CLI o `keyring` library)
   - Linux: Secret Service API (GNOME Keyring / KDE Wallet)
3. Abrir DB con PRAGMA key = '<passphrase>'

Ejecuciones siguientes:
1. Leer passphrase del keychain
2. Abrir DB con PRAGMA key = '<passphrase>'
3. Si falla → keychain fue limpiado → error claro con instrucciones
```

### Implementación con `pysqlcipher3`

```python
from pysqlcipher3 import dbapi2 as sqlcipher

def open_db(db_path: str, passphrase: str) -> sqlcipher.Connection:
    conn = sqlcipher.connect(db_path)
    conn.execute(f"PRAGMA key = '{passphrase}'")
    conn.execute("PRAGMA cipher_memory_security = ON")
    # Verificar que la key es correcta
    conn.execute("SELECT count(*) FROM sqlite_master")
    return conn
```

### Keychain Integration

```python
import keyring

SERVICE_NAME = "forge-memory"
ACCOUNT_NAME = "db-passphrase"

def get_or_create_passphrase() -> str:
    passphrase = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    if passphrase is None:
        passphrase = secrets.token_urlsafe(32)
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, passphrase)
    return passphrase
```

### Fallback sin SQLCipher

Si `pysqlcipher3` no está disponible (ej: CI, desarrollo):

1. Log warning claro: `"SQLCipher not available. Database will NOT be encrypted."`
2. Usar `sqlite3` estándar
3. Flag en config: `encryption: false`
4. NUNCA silenciar el warning

---

## 6. Busqueda Progresiva

### Nivel 1 (v0.1-v1.0) — FTS5 Potenciado

El 90% de las búsquedas se resuelven acá. La clave es hacer que FTS5 sea lo más inteligente posible antes de meter embeddings.

#### Componentes

**Porter stemmer** (built-in FTS5): `searching` matchea `search`, `searched`, etc.

**Tags semánticos**: el agente genera 3-5 tags al guardar. Se indexan en FTS5 como texto adicional. Esto amplía el vocabulario de match sin embeddings.

**Sinónimos**: tabla que expande queries antes de ejecutar contra FTS5.

```python
# Ejemplo de expansión
query = "users"
expanded = "users OR clientes OR usuarios"  # via tabla synonyms

query = "performance"
expanded = "performance OR rendimiento OR optimización OR speed"
```

**Ranking compuesto**:

```python
def compute_rank(fts_score: float, obs: Observation) -> float:
    """Ranking = BM25 + recency boost + tag bonus + quality score."""
    recency_days = (now() - obs.updated_at).days
    recency_boost = max(0, 1.0 - (recency_days / 365))  # decay lineal en 1 año

    tag_bonus = 0.1 * tag_match_count  # cada tag que matchea suma 0.1

    quality = obs.quality_score or 0.5  # default neutro

    return (fts_score * 0.5) + (recency_boost * 0.2) + (tag_bonus * 0.15) + (quality * 0.15)
```

#### Flujo de búsqueda L1

```
1. Recibir query del agente
2. Expandir con sinónimos
3. Ejecutar FTS5 con BM25
4. Aplicar ranking compuesto
5. Filtrar por project, type si aplica
6. Retornar top N resultados con score
```

### Nivel 2 (v2.0) — Embeddings Locales

Se activa SOLO cuando L1 retorna < 3 resultados o score < threshold.

- **sqlite-vec**: extensión SQLite para columnas vectoriales
- **all-MiniLM-L6-v2**: modelo ONNX (~80MB), corre 100% local, no requiere GPU
- **Hybrid search**: `final_score = 0.6 * cosine_similarity + 0.4 * fts5_score`

```sql
-- Tabla adicional para embeddings
CREATE TABLE observation_embeddings (
    observation_id INTEGER PRIMARY KEY REFERENCES observations(id),
    embedding BLOB NOT NULL  -- 384-dim float32 vector
);
```

### Nivel 3 (v3.0) — Backend Externo

Opt-in completo. Para equipos con volúmenes grandes o necesidad de búsqueda cross-proyecto.

- Backend configurable: Qdrant (recomendado), pgvector
- API configurable para modelo de embeddings (OpenAI, Cohere, local)
- Sync bidireccional con SQLite local como cache

---

## 7. Knowledge Graph Ligero

Las relaciones entre observaciones permiten navegación contextual sin la complejidad de embeddings.

### Tipos de Relación

| Tipo | Semántica | Ejemplo |
|------|-----------|---------|
| `extends` | A amplía/detalla B | "Auth con biometrics" extends "Auth design" |
| `contradicts` | A contradice B | "Decidimos NO usar Redux" contradicts "Evaluar Redux" |
| `replaces` | A reemplaza B (B queda obsoleto) | "Auth v2 con OAuth" replaces "Auth v1 con JWT" |
| `related` | Relación genérica | "User model" related "Auth service" |
| `depends_on` | A depende de B | "Payment flow" depends_on "Auth module" |

### Auto-sugerencia de Relaciones

Cuando se guarda una observación nueva:

```python
def suggest_relations(new_obs: Observation) -> list[RelationSuggestion]:
    """Busca observaciones similares y sugiere relaciones."""
    similar = fts5_search(new_obs.title + " " + new_obs.content, project=new_obs.project, limit=5)

    suggestions = []
    for obs in similar:
        if obs.score > SIMILARITY_THRESHOLD:  # 0.7
            suggestions.append(RelationSuggestion(
                existing_id=obs.id,
                existing_title=obs.title,
                score=obs.score,
                suggested_type=infer_relation_type(new_obs, obs)
            ))
    return suggestions
```

La respuesta de `forge_mem_save` incluye estas sugerencias. El agente decide si crear la relación o no.

### Traversal

`forge_mem_related` soporta `depth` para seguir el grafo:

```python
# depth=1: relaciones directas
# depth=2: relaciones de relaciones
# Máximo depth=3 para evitar explosión
```

---

## 8. Integración con Forge

### En `forge spec` (Step 0 — Contexto)

Antes de generar la spec, el agente busca conocimiento previo:

```
Agente → forge_mem_knowledge_search(project="mi-app", query="login biométrico")

← Respuesta:
  patterns: [{id: 12, title: "Auth pattern con biometrics", score: 0.9}]
  decisions: [{id: 8, title: "Decidimos LocalAuthentication sobre WebAuthn", score: 0.85}]
  contracts: [{id: 22, title: "Contrato AuthService", score: 0.7}]
  lessons: [{id: 31, title: "Gotcha: FaceID falla silenciosamente en simulator", score: 0.6}]

Agente → presenta al dev + incorpora en la spec
```

### En `forge close` (Knowledge Extraction)

Cuando se cierra una feature, el agente extrae conocimiento:

```
Agente → forge_mem_knowledge_extract(
    project="mi-app",
    feature_slug="FEAT-042-biometric-login",
    spec_path=".forge/features/FEAT-042/SPEC.md",
    verify_path=".forge/features/FEAT-042/VERIFY.md"
)

← Respuesta:
  candidates: [
    {title: "Patrón: BiometricAuthManager wrapper", type: "pattern", content: "...", tags: [...]},
    {title: "Lección: LAContext evalúa policy, no ejecuta auth", type: "lesson", content: "..."},
    {title: "Contrato: AuthService.authenticate() -> Result<Token>", type: "contract", content: "..."}
  ]

Agente → presenta candidatos al dev para aprobación
Dev → aprueba/rechaza/edita cada uno
Agente → forge_mem_save(...) por cada aprobado
```

### Detección y Fallback

```python
# En Forge (consumer side)
def get_memory_backend() -> MemoryBackend:
    """Detecta si forge-memory MCP está disponible."""
    available_tools = list_mcp_tools()
    forge_tools = [t for t in available_tools if t.startswith("forge_mem_")]

    if forge_tools:
        return MCPMemoryBackend()
    else:
        logger.info("forge-memory MCP not found, falling back to KNOWLEDGE.md")
        return FileMemoryBackend()  # lee/escribe KNOWLEDGE.md
```

---

## 9. Estructura del Proyecto

```
forge-memory/
├── pyproject.toml                 # packaging con uv/pip
├── README.md
├── LICENSE
│
├── forge_memory/
│   ├── __init__.py                # version, exports
│   ├── __main__.py                # CLI entrypoint: `python -m forge_memory serve`
│   ├── server.py                  # MCP server setup + tool registration
│   ├── db.py                      # conexión SQLite/SQLCipher + migrations
│   ├── models.py                  # dataclasses: Observation, Session, Relation, Tag
│   ├── search.py                  # FTS5 search + ranking + sinónimos
│   ├── crypto.py                  # keychain read/write + passphrase generation
│   ├── config.py                  # carga config.yaml + defaults + env vars
│   │
│   ├── tools/
│   │   ├── __init__.py            # registra todos los tools en el server
│   │   ├── core.py                # save, search, get, update, delete, context
│   │   ├── sessions.py            # session_start, session_end, session_summary
│   │   ├── relations.py           # relate, related
│   │   └── forge.py               # knowledge_extract, knowledge_search, feature_context
│   │
│   └── migrations/
│       ├── __init__.py
│       └── v001_initial.py        # schema creation
│
├── tests/
│   ├── conftest.py                # fixtures: in-memory DB, sample data
│   ├── test_db.py                 # conexión, migrations, cifrado
│   ├── test_search.py             # FTS5, ranking, sinónimos
│   ├── test_crypto.py             # keychain mock, passphrase generation
│   ├── test_tools_core.py         # save, search, get, update, delete
│   ├── test_tools_sessions.py     # session lifecycle
│   ├── test_tools_relations.py    # relaciones + traversal
│   └── test_tools_forge.py        # knowledge_extract, knowledge_search
│
└── scripts/
    └── install.sh                 # instala en Claude Code settings.json
```

---

## 10. Instalación y Configuración

### Instalación

```bash
# Opción 1: pip
pip install forge-memory

# Opción 2: uv (recomendado)
uv pip install forge-memory

# Opción 3: desde source
git clone https://github.com/user/forge-memory.git
cd forge-memory
uv pip install -e ".[dev]"
```

### Setup automático

```bash
# Registra el MCP server en Claude Code + genera encryption key
forge-memory install

# Esto hace:
# 1. Crea ~/.forge-memory/ si no existe
# 2. Genera passphrase y la guarda en keychain
# 3. Crea config.yaml con defaults
# 4. Agrega entry en ~/.claude/settings.json
```

### Claude Code `settings.json`

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

### Configuración (`~/.forge-memory/config.yaml`)

```yaml
storage:
  db_path: ~/.forge-memory/forge.db
  encryption: true                    # SQLCipher (default: true)

search:
  level: 1                            # 1=FTS5, 2=+embeddings, 3=+external
  fts5:
    stemmer: porter
    synonyms: true
    similarity_threshold: 0.7         # para auto-sugerencia de relaciones
  # Nivel 2 (cuando se activa):
  embeddings:
    model: all-MiniLM-L6-v2
    model_path: ~/.forge-memory/models/
  # Nivel 3 (cuando se activa):
  vector:
    backend: qdrant
    url: localhost:6333

relations:
  auto_suggest: true                  # sugerir relaciones en save
  max_depth: 3                        # máximo depth en traversal

projects:
  default: auto                       # auto-detect desde git remote o dirname
```

### Variables de entorno (override config.yaml)

| Variable | Descripción | Default |
|----------|-------------|---------|
| `FORGE_MEMORY_DB` | Path a la base de datos | `~/.forge-memory/forge.db` |
| `FORGE_MEMORY_LEVEL` | Nivel de búsqueda (1, 2, 3) | `1` |
| `FORGE_MEMORY_ENCRYPTION` | Habilitar cifrado (`true`/`false`) | `true` |
| `FORGE_MEMORY_CONFIG` | Path a config.yaml | `~/.forge-memory/config.yaml` |

---

## 11. Testing

### Estrategia

- **Unit tests**: cada módulo por separado con DB in-memory (sin cifrado para velocidad)
- **Integration tests**: flujos completos con DB cifrada temporal
- **No mocks de SQLite**: usar DB real in-memory, es más rápido que mockear

### Fixtures clave

```python
# conftest.py
@pytest.fixture
def db():
    """In-memory SQLite (sin cifrado) para tests rápidos."""
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    yield conn
    conn.close()

@pytest.fixture
def db_encrypted(tmp_path):
    """SQLCipher DB temporal para integration tests."""
    db_path = tmp_path / "test.db"
    conn = open_db(str(db_path), passphrase="test-passphrase")
    run_migrations(conn)
    yield conn
    conn.close()

@pytest.fixture
def sample_observations(db):
    """Carga 20 observaciones de ejemplo para tests de búsqueda."""
    # ... insert sample data
    yield db
```

### Tests críticos para v0.1

1. **Cifrado funciona**: DB creada con SQLCipher no se puede abrir sin key
2. **Upsert por topic_key**: save con topic_key existente actualiza, no duplica
3. **FTS5 busca bien**: búsqueda por título, contenido, y tags
4. **Ranking prioriza reciente**: observación de hoy rankea arriba de una de hace 6 meses
5. **Soft delete**: observaciones eliminadas no aparecen en búsqueda
6. **Sesiones**: start → guardar observaciones → end con summary

---

## 12. Roadmap

| Versión | Scope | Features |
|---------|-------|----------|
| **v0.1** | MVP funcional | Core tools (save, search, get, update, delete, context), SQLite + FTS5, cifrado SQLCipher, sessions, keychain integration, install script |
| **v0.2** | Búsqueda mejorada | Tags semánticos, sinónimos, ranking compuesto, relaciones entre observaciones, auto-sugerencia |
| **v0.3** | Integración Forge | `knowledge_extract`, `knowledge_search`, `feature_context`, fallback detection en Forge |
| **v1.0** | Release estable | Nivel 1 completo, documentación, publicación PyPI, tests > 90% coverage |
| **v2.0** | Embeddings locales | sqlite-vec + ONNX `all-MiniLM-L6-v2`, hybrid search, auto-escalada L1 → L2 |
| **v3.0** | Backend externo | Qdrant/pgvector opt-in, sync bidireccional, embeddings remotos |

### v0.1 — Checklist de implementación

- [ ] `pyproject.toml` con dependencias: `mcp`, `pysqlcipher3`, `keyring`
- [ ] `db.py`: conexión SQLCipher, migrations, schema creation
- [ ] `crypto.py`: keychain read/write, passphrase generation, fallback
- [ ] `models.py`: dataclasses para Observation, Session
- [ ] `search.py`: FTS5 search con porter stemmer, ranking básico
- [ ] `tools/core.py`: save (con upsert), search, get, update, delete, context
- [ ] `tools/sessions.py`: session_start, session_end, session_summary
- [ ] `server.py`: MCP server con todos los tools registrados
- [ ] `__main__.py`: CLI con `serve` command
- [ ] `scripts/install.sh`: registro en Claude Code settings.json
- [ ] Tests: db, search, crypto, tools
- [ ] `config.py`: carga config.yaml + env vars + defaults

---

## 13. Decisiones de Diseño

| Decisión | Alternativa descartada | Por qué |
|----------|----------------------|---------|
| **Python** | Go, Rust, TypeScript | MCP SDK maduro en Python, ecosystem SQLite/crypto fuerte, curva de aprendizaje baja |
| **SQLCipher** | Fernet column-level encryption | Cifra TODO incluyendo índices FTS5 y WAL. Estándar de la industria para SQLite cifrado |
| **FTS5 primero** | Arrancar con embeddings | El 90% de los casos se resuelven sin embeddings. Menos complejidad, menos dependencias, arranque más rápido |
| **Relaciones explícitas** | Solo búsqueda por similaridad | El grafo de conocimiento da contexto navegable sin la complejidad de embeddings |
| **Keychain del sistema** | `.env` file con la key | Fintech: la key no puede estar en un archivo de texto. Keychain es el estándar |
| **Proyecto separado** | Dentro del repo de Forge | Ciclos de vida distintos, valor standalone, el setup.sh de Forge ya es complejo |
| **Upsert por topic_key** | Solo insert | Evita duplicación de conocimiento que evoluciona. Un topic = una observación actualizada |
| **Tags generados por agente** | Tags automáticos por NLP | El agente tiene contexto semántico que NLP local no tiene. Más preciso, menos dependencias |
| **Soft delete** | Hard delete | Permite recovery y auditoría. El espacio en SQLite es despreciable |
| **Sinónimos manuales** | WordNet automático | Más control, menos ruido. Los sinónimos relevantes son específicos del dominio |

---

## 14. Riesgos y Mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| `pysqlcipher3` difícil de instalar en algunos OS | Alto | Fallback a SQLite sin cifrar + warning claro. Documentar instalación por OS |
| FTS5 no encuentra lo que necesita | Medio | Tags semánticos + sinónimos compensan. L2 como escape hatch |
| Keychain no disponible (CI, containers) | Medio | Env var `FORGE_MEMORY_KEY` como fallback. Warning en logs |
| DB crece mucho con muchas observaciones | Bajo | SQLite maneja millones de filas. Agregar `VACUUM` periódico si necesario |
| Agente guarda basura (observaciones de baja calidad) | Medio | `quality_score` para filtrar. `forge close` como curación humana |

---

## 15. Notas Finales

Este diseño prioriza **simplicidad operativa** sobre features. Un archivo, un proceso, cero servicios externos. Si necesitás más potencia, cada nivel de búsqueda se activa explícitamente.

La integración con Forge es el caso de uso principal pero no el único. Cualquier agente MCP puede usar `forge-memory` como su memoria persistente cifrada.

El nombre `forge-memory` es provisional. Si se publica como herramienta general, considerar `mcp-memory-vault` o similar.

Para arrancar a codear v0.1, seguí el checklist de la sección 12 en orden. Cada item es un PR separado, testeable de forma independiente.
