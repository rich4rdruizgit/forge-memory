"""Data models, enums, and exception hierarchy for forge-memory.

This module is the foundation layer (L1) — it has ZERO internal imports.
Every other module in forge_memory may import from here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ObservationType(str, Enum):
    """Valid observation types. Maps to CHECK constraint in observations table."""

    PATTERN = "pattern"
    DECISION = "decision"
    CONTRACT = "contract"
    COMPONENT = "component"
    ERROR = "error"
    LESSON = "lesson"
    MODULE = "module"
    PREFERENCE = "preference"
    DISCOVERY = "discovery"
    ARCHITECTURE = "architecture"
    BUGFIX = "bugfix"
    CONFIG = "config"


class Scope(str, Enum):
    """Observation scope — project-level or personal (cross-project)."""

    PROJECT = "project"
    PERSONAL = "personal"


class RelationType(str, Enum):
    """Valid relation types between observations."""

    EXTENDS = "extends"
    CONTRADICTS = "contradicts"
    REPLACES = "replaces"
    RELATED = "related"
    DEPENDS_ON = "depends_on"


# Class-level constants for query logic
SYMMETRIC_RELATIONS: frozenset[RelationType] = frozenset(
    {RelationType.RELATED, RelationType.CONTRADICTS}
)
DIRECTIONAL_RELATIONS: frozenset[RelationType] = frozenset(
    {RelationType.EXTENDS, RelationType.REPLACES, RelationType.DEPENDS_ON}
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Observation:
    """Central knowledge unit. Everything the agent learns is stored as one of these.

    Fields match the `observations` table 1:1 except `tags` which is denormalized
    from the `tags` table for convenience.
    """

    id: int
    title: str
    content: str
    type: ObservationType
    scope: Scope
    project: str
    topic_key: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    tags_text: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    feature_slug: Optional[str] = None
    quality_score: Optional[float] = None
    is_active: bool = True


@dataclass
class Session:
    """Working session for temporal context tracking.

    Fields match the `sessions` table 1:1.
    """

    id: int
    project: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    summary: Optional[str] = None
    feature_slug: Optional[str] = None


@dataclass
class Tag:
    """Single tag associated with an observation.

    Fields match the `tags` table 1:1.
    """

    id: int
    observation_id: int
    tag: str


@dataclass
class SearchResult:
    """A single result from full-text search with ranking score.

    Returned by `search.execute_search()`. Content is truncated to a preview.
    """

    id: int
    title: str
    content_preview: str  # first 300 chars
    type: ObservationType
    score: float
    tags: list[str]
    project: str
    topic_key: Optional[str] = None
    updated_at: Optional[datetime] = None


@dataclass
class Relation:
    """A directed edge between two observations."""

    id: int
    source_id: int
    target_id: int
    relation_type: RelationType
    created_at: Optional[datetime] = None


@dataclass
class Synonym:
    """A synonym pair for search query expansion."""

    id: int
    term: str
    synonym: str
    language: str = "es"


@dataclass
class RelationSuggestion:
    """A suggested relation between observations based on search similarity."""

    existing_id: int
    existing_title: str
    score: float
    suggested_type: RelationType


@dataclass
class KnowledgeCandidate:
    """A candidate knowledge item extracted from a Forge artifact.

    Returned by forge_mem_knowledge_extract. The agent presents these
    to the user for approval before saving via forge_mem_save.
    """

    title: str
    content: str
    type: str  # ObservationType value string, not enum (for JSON serialization)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5  # 0.0-1.0, heuristic confidence
    source_section: str = ""  # heading text of the source markdown section
    source_file: str = ""  # path to the source file


class ForgeMemoryConfig:
    """Server configuration. Three-layer loading: defaults -> YAML -> env vars.

    `resolved_db_path` is computed at runtime after ~ expansion.
    """

    db_path: str = "~/.forge-memory/forge.db"
    search_level: int = 1
    config_path: str = "~/.forge-memory/config.yaml"
    resolved_db_path: str = ""  # populated after path resolution


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class ForgeMemoryError(Exception):
    """Base exception for forge-memory."""


class DatabaseError(ForgeMemoryError):
    """Database connection or query error."""


class ConfigError(ForgeMemoryError):
    """Configuration loading or validation error."""


class NotFoundError(ForgeMemoryError):
    """Requested resource not found."""

    def __init__(self, resource: str, id: int) -> None:
        self.resource = resource
        self.id = id
        super().__init__(f"{resource} with id={id} not found")


class ValidationError(ForgeMemoryError):
    """Input validation failed."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"Validation error on '{field}': {message}")
