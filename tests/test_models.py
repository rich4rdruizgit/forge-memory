"""Tests for forge_memory.models — enums, dataclasses, and exception hierarchy."""

from datetime import datetime

import pytest

from forge_memory.models import (
    DIRECTIONAL_RELATIONS,
    SYMMETRIC_RELATIONS,
    ConfigError,
    DatabaseError,
    ForgeMemoryConfig,
    ForgeMemoryError,
    KnowledgeCandidate,
    NotFoundError,
    Observation,
    ObservationType,
    Relation,
    RelationSuggestion,
    RelationType,
    Scope,
    SearchResult,
    Session,
    Synonym,
    Tag,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestObservationType:
    """ObservationType enum values and behavior."""

    def test_all_values_present(self):
        expected = {
            "pattern",
            "decision",
            "contract",
            "component",
            "error",
            "lesson",
            "module",
            "preference",
            "discovery",
            "architecture",
            "bugfix",
            "config",
        }
        assert {member.value for member in ObservationType} == expected

    def test_is_str_enum(self):
        assert isinstance(ObservationType.PATTERN, str)
        assert ObservationType.PATTERN == "pattern"

    def test_lookup_by_value(self):
        assert ObservationType("decision") is ObservationType.DECISION

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ObservationType("nonexistent")


class TestScope:
    """Scope enum values and behavior."""

    def test_values(self):
        assert Scope.PROJECT.value == "project"
        assert Scope.PERSONAL.value == "personal"

    def test_is_str_enum(self):
        assert isinstance(Scope.PROJECT, str)
        assert Scope.PROJECT == "project"

    def test_only_two_members(self):
        assert len(Scope) == 2


class TestRelationType:
    """RelationType enum values and behavior."""

    def test_all_values_present(self):
        expected = {"extends", "contradicts", "replaces", "related", "depends_on"}
        assert {member.value for member in RelationType} == expected

    def test_is_str_enum(self):
        assert isinstance(RelationType.EXTENDS, str)
        assert RelationType.EXTENDS == "extends"

    def test_lookup_by_value(self):
        assert RelationType("extends") is RelationType.EXTENDS
        assert RelationType("contradicts") is RelationType.CONTRADICTS
        assert RelationType("replaces") is RelationType.REPLACES
        assert RelationType("related") is RelationType.RELATED
        assert RelationType("depends_on") is RelationType.DEPENDS_ON

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            RelationType("invented")

    def test_symmetric_relations_set(self):
        assert SYMMETRIC_RELATIONS == frozenset(
            {RelationType.RELATED, RelationType.CONTRADICTS}
        )

    def test_directional_relations_set(self):
        assert DIRECTIONAL_RELATIONS == frozenset(
            {RelationType.EXTENDS, RelationType.REPLACES, RelationType.DEPENDS_ON}
        )

    def test_symmetric_and_directional_cover_all(self):
        """SYMMETRIC + DIRECTIONAL should cover all RelationType members."""
        assert SYMMETRIC_RELATIONS | DIRECTIONAL_RELATIONS == set(RelationType)

    def test_symmetric_and_directional_disjoint(self):
        """SYMMETRIC and DIRECTIONAL should not overlap."""
        assert SYMMETRIC_RELATIONS & DIRECTIONAL_RELATIONS == set()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestObservation:
    """Observation dataclass creation and defaults."""

    def test_creation_all_fields(self):
        now = datetime.now()
        obs = Observation(
            id=1,
            title="Test observation",
            content="Some content",
            type=ObservationType.DECISION,
            scope=Scope.PROJECT,
            project="forge-memory",
            topic_key="arch/db",
            tags=["sql", "design"],
            tags_text="sql design",
            created_at=now,
            updated_at=now,
            feature_slug="feat-1",
            quality_score=0.95,
            is_active=False,
        )
        assert obs.id == 1
        assert obs.title == "Test observation"
        assert obs.content == "Some content"
        assert obs.type is ObservationType.DECISION
        assert obs.scope is Scope.PROJECT
        assert obs.project == "forge-memory"
        assert obs.topic_key == "arch/db"
        assert obs.tags == ["sql", "design"]
        assert obs.tags_text == "sql design"
        assert obs.created_at is now
        assert obs.updated_at is now
        assert obs.feature_slug == "feat-1"
        assert obs.quality_score == 0.95
        assert obs.is_active is False

    def test_creation_minimal_fields_defaults(self):
        obs = Observation(
            id=2,
            title="Minimal",
            content="body",
            type=ObservationType.BUGFIX,
            scope=Scope.PERSONAL,
            project="test",
        )
        assert obs.topic_key is None
        assert obs.tags == []
        assert obs.tags_text == ""
        assert obs.created_at is None
        assert obs.updated_at is None
        assert obs.feature_slug is None
        assert obs.quality_score is None
        assert obs.is_active is True

    def test_tags_default_is_independent(self):
        """Each instance gets its own list — no shared mutable default."""
        a = Observation(
            id=1, title="a", content="a",
            type=ObservationType.LESSON, scope=Scope.PROJECT, project="p",
        )
        b = Observation(
            id=2, title="b", content="b",
            type=ObservationType.LESSON, scope=Scope.PROJECT, project="p",
        )
        a.tags.append("x")
        assert b.tags == []


class TestSession:
    """Session dataclass creation and defaults."""

    def test_creation_all_fields(self):
        now = datetime.now()
        s = Session(
            id=1, project="proj", started_at=now,
            ended_at=now, summary="done", feature_slug="f-1",
        )
        assert s.id == 1
        assert s.project == "proj"
        assert s.started_at is now
        assert s.ended_at is now
        assert s.summary == "done"
        assert s.feature_slug == "f-1"

    def test_creation_minimal_defaults(self):
        s = Session(id=5, project="test")
        assert s.started_at is None
        assert s.ended_at is None
        assert s.summary is None
        assert s.feature_slug is None


class TestTag:
    """Tag dataclass creation."""

    def test_creation(self):
        t = Tag(id=1, observation_id=10, tag="architecture")
        assert t.id == 1
        assert t.observation_id == 10
        assert t.tag == "architecture"


class TestSearchResult:
    """SearchResult dataclass creation and defaults."""

    def test_creation_all_fields(self):
        now = datetime.now()
        sr = SearchResult(
            id=1,
            title="Found it",
            content_preview="preview text",
            type=ObservationType.PATTERN,
            score=1.5,
            tags=["a", "b"],
            project="proj",
            topic_key="key/1",
            updated_at=now,
        )
        assert sr.id == 1
        assert sr.title == "Found it"
        assert sr.content_preview == "preview text"
        assert sr.type is ObservationType.PATTERN
        assert sr.score == 1.5
        assert sr.tags == ["a", "b"]
        assert sr.project == "proj"
        assert sr.topic_key == "key/1"
        assert sr.updated_at is now

    def test_creation_minimal_defaults(self):
        sr = SearchResult(
            id=2,
            title="T",
            content_preview="C",
            type=ObservationType.ERROR,
            score=0.5,
            tags=[],
            project="p",
        )
        assert sr.topic_key is None
        assert sr.updated_at is None


class TestRelation:
    """Relation dataclass creation and defaults."""

    def test_creation_all_fields(self):
        now = datetime.now()
        rel = Relation(
            id=1,
            source_id=10,
            target_id=20,
            relation_type=RelationType.EXTENDS,
            created_at=now,
        )
        assert rel.id == 1
        assert rel.source_id == 10
        assert rel.target_id == 20
        assert rel.relation_type is RelationType.EXTENDS
        assert rel.created_at is now

    def test_creation_minimal_defaults(self):
        rel = Relation(
            id=2,
            source_id=5,
            target_id=6,
            relation_type=RelationType.RELATED,
        )
        assert rel.created_at is None

    def test_all_relation_types_accepted(self):
        """Relation dataclass accepts all RelationType values."""
        for rt in RelationType:
            rel = Relation(id=1, source_id=1, target_id=2, relation_type=rt)
            assert rel.relation_type is rt


class TestSynonym:
    """Synonym dataclass creation and defaults."""

    def test_creation_all_fields(self):
        syn = Synonym(id=1, term="auth", synonym="autenticación", language="es")
        assert syn.id == 1
        assert syn.term == "auth"
        assert syn.synonym == "autenticación"
        assert syn.language == "es"

    def test_creation_default_language(self):
        syn = Synonym(id=2, term="bug", synonym="error")
        assert syn.language == "es"


class TestRelationSuggestion:
    """RelationSuggestion dataclass creation."""

    def test_creation(self):
        sug = RelationSuggestion(
            existing_id=42,
            existing_title="Related observation",
            score=0.85,
            suggested_type=RelationType.RELATED,
        )
        assert sug.existing_id == 42
        assert sug.existing_title == "Related observation"
        assert sug.score == 0.85
        assert sug.suggested_type is RelationType.RELATED

    def test_creation_with_different_types(self):
        for rt in RelationType:
            sug = RelationSuggestion(
                existing_id=1, existing_title="T", score=0.5, suggested_type=rt
            )
            assert sug.suggested_type is rt


class TestKnowledgeCandidate:
    """KnowledgeCandidate dataclass creation and defaults."""

    def test_creation_all_fields(self):
        kc = KnowledgeCandidate(
            title="Use JWT",
            content="We decided to use JWT.",
            type="decision",
            tags=["auth", "jwt"],
            confidence=0.85,
            source_section="Decision: Use JWT",
            source_file="/path/to/spec.md",
        )
        assert kc.title == "Use JWT"
        assert kc.content == "We decided to use JWT."
        assert kc.type == "decision"
        assert kc.tags == ["auth", "jwt"]
        assert kc.confidence == 0.85
        assert kc.source_section == "Decision: Use JWT"
        assert kc.source_file == "/path/to/spec.md"

    def test_creation_minimal_defaults(self):
        kc = KnowledgeCandidate(
            title="Minimal",
            content="body",
            type="discovery",
        )
        assert kc.tags == []
        assert kc.confidence == 0.5
        assert kc.source_section == ""
        assert kc.source_file == ""

    def test_tags_default_is_independent(self):
        a = KnowledgeCandidate(title="a", content="a", type="pattern")
        b = KnowledgeCandidate(title="b", content="b", type="pattern")
        a.tags.append("x")
        assert b.tags == []


class TestForgeMemoryConfig:
    """ForgeMemoryConfig dataclass defaults."""

    def test_defaults(self):
        cfg = ForgeMemoryConfig()
        assert cfg.db_path == "~/.forge-memory/forge.db"
        assert cfg.search_level == 1
        assert cfg.config_path == "~/.forge-memory/config.yaml"
        assert cfg.resolved_db_path == ""


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptions:
    """Exception classes instantiate correctly and have proper hierarchy."""

    def test_base_exception(self):
        err = ForgeMemoryError("boom")
        assert str(err) == "boom"
        assert isinstance(err, Exception)

    def test_database_error(self):
        err = DatabaseError("connection failed")
        assert str(err) == "connection failed"
        assert isinstance(err, ForgeMemoryError)

    def test_config_error(self):
        err = ConfigError("missing field")
        assert str(err) == "missing field"
        assert isinstance(err, ForgeMemoryError)

    def test_not_found_error(self):
        err = NotFoundError("Observation", 42)
        assert err.resource == "Observation"
        assert err.id == 42
        assert str(err) == "Observation with id=42 not found"
        assert isinstance(err, ForgeMemoryError)

    def test_validation_error(self):
        err = ValidationError("title", "cannot be empty")
        assert err.field == "title"
        assert str(err) == "Validation error on 'title': cannot be empty"
        assert isinstance(err, ForgeMemoryError)
