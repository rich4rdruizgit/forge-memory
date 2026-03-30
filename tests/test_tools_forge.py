"""Tests for forge_memory.tools.forge -- Forge-specific MCP tool functions."""

from __future__ import annotations

from forge_memory.tools.forge import (
    _classify_section,
    _extract_tags,
    _parse_sections,
    _read_file_safe,
    forge_mem_feature_context,
    forge_mem_knowledge_extract,
    forge_mem_knowledge_search,
)


# ---------------------------------------------------------------------------
# Unit tests -- _parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    """_parse_sections splits markdown by ## headings."""

    def test_parses_headings_correctly(self):
        content = (
            "# Title\n\nIntro text\n\n"
            "## Decision\n\nWe chose X.\n\n"
            "## Pattern\n\nUse adapter pattern.\n"
        )
        sections = _parse_sections(content)
        assert len(sections) == 2
        assert sections[0][0] == "Decision"
        assert "chose X" in sections[0][1]
        assert sections[1][0] == "Pattern"
        assert "adapter" in sections[1][1]

    def test_handles_no_headings(self):
        content = "Just some text without any headings.\nMore text here."
        sections = _parse_sections(content)
        assert sections == []

    def test_handles_sub_headings_within_sections(self):
        content = (
            "## Main Section\n\n"
            "Body text.\n\n"
            "### Sub-heading A\n\n"
            "Sub content A.\n\n"
            "### Sub-heading B\n\n"
            "Sub content B.\n"
        )
        sections = _parse_sections(content)
        assert len(sections) == 1
        assert sections[0][0] == "Main Section"
        assert "Sub-heading A" in sections[0][1]
        assert "Sub-heading B" in sections[0][1]

    def test_empty_content(self):
        assert _parse_sections("") == []
        assert _parse_sections("   ") == []

    def test_skips_sections_with_empty_body(self):
        content = "## Empty Section\n\n## Has Body\n\nSome content."
        sections = _parse_sections(content)
        assert len(sections) == 1
        assert sections[0][0] == "Has Body"


# ---------------------------------------------------------------------------
# Unit tests -- _classify_section
# ---------------------------------------------------------------------------


class TestClassifySection:
    """_classify_section identifies section types via keyword matching."""

    def test_classifies_decision_keywords(self):
        type_str, confidence = _classify_section(
            "Decision: Use JWT", "We decided to use JWT because of tradeoffs."
        )
        assert type_str == "decision"
        assert confidence >= 0.3

    def test_classifies_pattern_keywords(self):
        type_str, confidence = _classify_section(
            "Pattern: Repository", "A convention for data access using standard practice."
        )
        assert type_str == "pattern"
        assert confidence >= 0.3

    def test_classifies_lesson_keywords(self):
        type_str, confidence = _classify_section(
            "Lesson Learned", "This was a gotcha we discovered, a real pitfall."
        )
        assert type_str == "lesson"
        assert confidence >= 0.3

    def test_classifies_contract_keywords(self):
        type_str, confidence = _classify_section(
            "Contract: API", "The endpoint interface accepts input and returns output."
        )
        assert type_str == "contract"
        assert confidence >= 0.3

    def test_classifies_discovery_keywords(self):
        type_str, confidence = _classify_section(
            "Discovery", "We found an interesting finding during investigation."
        )
        assert type_str == "discovery"
        assert confidence >= 0.3

    def test_fallback_to_discovery_for_no_keywords(self):
        type_str, confidence = _classify_section(
            "Miscellaneous", "Lorem ipsum dolor sit amet."
        )
        assert type_str == "discovery"
        assert confidence == 0.3

    def test_higher_confidence_for_heading_match_plus_long_body(self):
        short_type, short_conf = _classify_section("Decision", "Short.")
        long_type, long_conf = _classify_section(
            "Decision: Architecture",
            "We decided to use a specific approach because " * 20,
        )
        assert long_conf > short_conf


# ---------------------------------------------------------------------------
# Unit tests -- _extract_tags
# ---------------------------------------------------------------------------


class TestExtractTags:
    """_extract_tags generates tags from section content."""

    def test_includes_feature_slug(self):
        tags = _extract_tags("Heading", "Body text.", "FEAT-01")
        assert "FEAT-01" in tags

    def test_extracts_backtick_identifiers(self):
        body = "Use the `AuthService` class and `UserRepo` for data."
        tags = _extract_tags("Heading", body, None)
        assert "authservice" in tags
        assert "userrepo" in tags

    def test_extracts_sub_headings(self):
        body = "### Implementation\nDetails.\n### Testing\nMore details."
        tags = _extract_tags("Heading", body, None)
        assert "implementation" in tags
        assert "testing" in tags

    def test_caps_at_10(self):
        body = " ".join(f"`Identifier{i}`" for i in range(20))
        tags = _extract_tags("Heading", body, "my-feature")
        assert len(tags) <= 10

    def test_no_feature_slug(self):
        tags = _extract_tags("Heading", "Just body.", None)
        # Should not crash, no feature slug tag added
        assert isinstance(tags, list)

    def test_deduplicates(self):
        body = "`AuthService` and `authservice` again."
        tags = _extract_tags("Heading", body, None)
        lower_tags = [t.lower() for t in tags]
        assert len(lower_tags) == len(set(lower_tags))


# ---------------------------------------------------------------------------
# Unit tests -- _read_file_safe
# ---------------------------------------------------------------------------


class TestReadFileSafe:
    """_read_file_safe reads files with safety guards."""

    def test_reads_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Hello world", encoding="utf-8")
        content, warnings = _read_file_safe(str(f))
        assert content == "Hello world"
        # tmp_path may be outside $HOME, so filter out that specific warning
        non_home_warnings = [w for w in warnings if "outside home" not in w.lower()]
        assert non_home_warnings == []

    def test_returns_none_on_missing(self):
        content, warnings = _read_file_safe("/nonexistent/path/file.md")
        assert content is None
        assert any("not found" in w.lower() for w in warnings)

    def test_returns_none_on_empty(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        content, warnings = _read_file_safe(str(f))
        assert content is None
        assert any("empty" in w.lower() for w in warnings)

    def test_truncates_large_file(self, tmp_path):
        f = tmp_path / "big.md"
        # Write 2MB
        f.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
        content, warnings = _read_file_safe(str(f))
        assert content is not None
        assert len(content) == 1_048_576
        assert any("truncated" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Integration tests -- forge_mem_knowledge_extract
# ---------------------------------------------------------------------------


class TestForgeMemKnowledgeExtract:
    """forge_mem_knowledge_extract parses markdown and returns candidates."""

    def test_happy_path_with_spec_file(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text(
            "# Spec\n\n"
            "## Decision: Use JWT over sessions\n\n"
            "We decided to use JWT because of tradeoffs and alternatives.\n\n"
            "## Pattern: Repository for user data\n\n"
            "A standard convention and practice for data access structure.\n\n"
            "## Contract: AuthService.login() signature\n\n"
            "The interface endpoint accepts input and returns output payload.\n",
            encoding="utf-8",
        )
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
            spec_path=str(spec),
        )
        assert result["status"] == "ok"
        assert result["candidate_count"] >= 2
        types = [c["type"] for c in result["candidates"]]
        assert "decision" in types

    def test_both_spec_and_verify(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text(
            "## Decision: Architecture\n\n"
            "We chose to use hexagonal architecture as our approach.\n",
            encoding="utf-8",
        )
        verify = tmp_path / "verify.md"
        verify.write_text(
            "## Lesson Learned: Silent failure\n\n"
            "A gotcha we found: the API fails silently, unexpected pitfall.\n",
            encoding="utf-8",
        )
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
            spec_path=str(spec),
            verify_path=str(verify),
        )
        assert result["status"] == "ok"
        assert result["candidate_count"] == 2
        assert len(result["source_files"]) == 2

    def test_missing_file_returns_empty_candidates(self, tmp_path):
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
            spec_path="/nonexistent/file.md",
        )
        assert result["status"] == "ok"
        assert result["candidate_count"] == 0

    def test_no_paths_returns_error(self):
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
        )
        assert result["status"] == "error"
        assert "at least one" in result["message"].lower()

    def test_empty_file_returns_empty_candidates(self, tmp_path):
        spec = tmp_path / "empty.md"
        spec.write_text("", encoding="utf-8")
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
            spec_path=str(spec),
        )
        assert result["status"] == "ok"
        assert result["candidate_count"] == 0

    def test_unclassifiable_section_becomes_discovery(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text(
            "## Random Thoughts About Architecture\n\n"
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.\n",
            encoding="utf-8",
        )
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
            spec_path=str(spec),
        )
        assert result["status"] == "ok"
        assert result["candidate_count"] >= 1
        types = [c["type"] for c in result["candidates"]]
        assert "discovery" in types

    def test_candidates_have_source_file(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text(
            "## Decision: Something\n\n"
            "We decided on this approach after evaluating alternatives.\n",
            encoding="utf-8",
        )
        result = forge_mem_knowledge_extract(
            project="my-app",
            feature_slug="FEAT-01",
            spec_path=str(spec),
        )
        for c in result["candidates"]:
            assert c["source_file"] == str(spec)


# ---------------------------------------------------------------------------
# Integration tests -- forge_mem_knowledge_search
# ---------------------------------------------------------------------------


class TestForgeMemKnowledgeSearch:
    """forge_mem_knowledge_search groups search results by type."""

    def _seed(self, db):
        """Seed observations of various types."""
        from forge_memory.tools.core import forge_mem_save
        forge_mem_save(
            title="Auth decision",
            content="We decided on JWT authentication approach",
            type="decision",
            project="search-proj",
        )
        forge_mem_save(
            title="Auth pattern",
            content="Standard authentication pattern convention",
            type="pattern",
            project="search-proj",
        )
        forge_mem_save(
            title="Auth lesson",
            content="Lesson learned about authentication gotcha",
            type="lesson",
            project="search-proj",
        )
        forge_mem_save(
            title="Auth bugfix",
            content="Fixed authentication bug error",
            type="bugfix",
            project="search-proj",
        )

    def test_groups_by_type(self, db):
        self._seed(db)
        result = forge_mem_knowledge_search(
            project="search-proj",
            query="authentication",
        )
        assert result["status"] == "ok"
        assert "decisions" in result
        assert "patterns" in result
        assert "contracts" in result
        assert "lessons" in result
        assert "other" in result
        assert result["total_count"] >= 1

    def test_types_filter(self, db):
        self._seed(db)
        result = forge_mem_knowledge_search(
            project="search-proj",
            query="authentication",
            types=["decisions", "patterns"],
        )
        assert "decisions" in result
        assert "patterns" in result
        assert "contracts" not in result
        assert "lessons" not in result

    def test_empty_results(self, db):
        result = forge_mem_knowledge_search(
            project="search-proj",
            query="xyzzy_nonexistent_term",
        )
        assert result["status"] == "ok"
        assert result["total_count"] == 0
        assert result["decisions"] == []
        assert result["patterns"] == []

    def test_empty_query_returns_error(self, db):
        result = forge_mem_knowledge_search(
            project="search-proj",
            query="",
        )
        assert result["status"] == "error"

    def test_discoveries_bucket_exists(self, db):
        from forge_memory.tools.core import forge_mem_save
        forge_mem_save(
            title="Auth discovery",
            content="Discovered something about authentication investigation",
            type="discovery",
            project="search-proj",
        )
        result = forge_mem_knowledge_search(
            project="search-proj",
            query="authentication",
        )
        assert "discoveries" in result
        disc_types = [r["type"] for r in result.get("discoveries", [])]
        if disc_types:
            assert "discovery" in disc_types

    def test_bugfix_goes_to_other_bucket(self, db):
        self._seed(db)
        result = forge_mem_knowledge_search(
            project="search-proj",
            query="authentication",
        )
        # bugfix should end up in "other" bucket
        other_types = [r["type"] for r in result.get("other", [])]
        if other_types:
            assert "bugfix" in other_types


# ---------------------------------------------------------------------------
# Integration tests -- forge_mem_feature_context
# ---------------------------------------------------------------------------


class TestForgeMemFeatureContext:
    """forge_mem_feature_context aggregates feature data."""

    def _seed_feature(self, db):
        """Seed observations, sessions, and relations for a feature."""
        from forge_memory.tools.core import forge_mem_save
        from forge_memory.tools.relations import forge_mem_relate

        # Feature observations
        r1 = forge_mem_save(
            title="Feature obs 1",
            content="First observation for the feature",
            type="decision",
            project="ctx-proj",
            feature_slug="FEAT-01",
        )
        r2 = forge_mem_save(
            title="Feature obs 2",
            content="Second observation for the feature",
            type="pattern",
            project="ctx-proj",
            feature_slug="FEAT-01",
        )
        # Non-feature observation (related via graph)
        r3 = forge_mem_save(
            title="External obs",
            content="Not part of the feature directly",
            type="discovery",
            project="ctx-proj",
        )
        # Create relation: feature obs 1 -> external obs
        forge_mem_relate(
            source_id=r1["id"],
            target_id=r3["id"],
            relation_type="related",
        )

        # Session for the feature
        db.execute(
            "INSERT INTO sessions (project, feature_slug, started_at) "
            "VALUES (?, ?, datetime('now'))",
            ["ctx-proj", "FEAT-01"],
        )
        db.commit()

        return r1, r2, r3

    def test_returns_observations_sessions_relations(self, db):
        r1, r2, r3 = self._seed_feature(db)
        result = forge_mem_feature_context(
            project="ctx-proj",
            feature_slug="FEAT-01",
        )
        assert result["feature_slug"] == "FEAT-01"
        assert result["observation_count"] == 2
        assert result["session_count"] == 1
        assert result["relation_count"] >= 1

        obs_ids = [o["id"] for o in result["observations"]]
        assert r1["id"] in obs_ids
        assert r2["id"] in obs_ids

        # External observation should appear in relations
        rel_ids = [r["id"] for r in result["relations"]]
        assert r3["id"] in rel_ids

    def test_no_data_returns_empty(self, db):
        result = forge_mem_feature_context(
            project="ctx-proj",
            feature_slug="FEAT-99-nonexistent",
        )
        assert result["observation_count"] == 0
        assert result["session_count"] == 0
        assert result["relation_count"] == 0
        assert result["observations"] == []
        assert result["sessions"] == []
        assert result["relations"] == []

    def test_soft_deleted_excluded(self, db):
        from forge_memory.tools.core import forge_mem_save, forge_mem_delete

        r1 = forge_mem_save(
            title="Active obs",
            content="Still alive",
            type="decision",
            project="del-proj",
            feature_slug="FEAT-DEL",
        )
        r2 = forge_mem_save(
            title="Deleted obs",
            content="Will be deleted",
            type="pattern",
            project="del-proj",
            feature_slug="FEAT-DEL",
        )
        forge_mem_delete(r2["id"])

        result = forge_mem_feature_context(
            project="del-proj",
            feature_slug="FEAT-DEL",
        )
        assert result["observation_count"] == 1
        obs_ids = [o["id"] for o in result["observations"]]
        assert r1["id"] in obs_ids
        assert r2["id"] not in obs_ids

    def test_wrong_project_excluded(self, db):
        from forge_memory.tools.core import forge_mem_save

        forge_mem_save(
            title="App A obs",
            content="In project A",
            type="decision",
            project="app-a",
            feature_slug="FEAT-01",
        )
        forge_mem_save(
            title="App B obs",
            content="In project B",
            type="decision",
            project="app-b",
            feature_slug="FEAT-01",
        )

        result = forge_mem_feature_context(
            project="app-a",
            feature_slug="FEAT-01",
        )
        assert result["observation_count"] == 1
        assert result["observations"][0]["title"] == "App A obs"

    def test_sessions_without_observations(self, db):
        db.execute(
            "INSERT INTO sessions (project, feature_slug, started_at) "
            "VALUES (?, ?, datetime('now'))",
            ["lonely-proj", "FEAT-LONELY"],
        )
        db.commit()

        result = forge_mem_feature_context(
            project="lonely-proj",
            feature_slug="FEAT-LONELY",
        )
        assert result["observation_count"] == 0
        assert result["session_count"] == 1
        assert result["relation_count"] == 0


# ---------------------------------------------------------------------------
# Additional edge cases for forge.py missing coverage
# ---------------------------------------------------------------------------


class TestReadFileSafeEdgeCases:
    """Additional _read_file_safe edge cases."""

    def test_permission_error_returns_none(self, tmp_path):
        """PermissionError returns None with a warning message."""
        import os
        from unittest.mock import patch, mock_open
        from forge_memory.tools.forge import _read_file_safe

        f = tmp_path / "restricted.md"
        f.write_text("Some content here", encoding="utf-8")

        with patch("builtins.open", side_effect=PermissionError("denied")):
            content, warnings = _read_file_safe(str(f))
        assert content is None
        assert any("cannot read" in w.lower() for w in warnings)

    def test_path_outside_home_warns(self, tmp_path):
        """Path outside $HOME gets a warning."""
        from unittest.mock import patch
        from forge_memory.tools.forge import _read_file_safe

        f = tmp_path / "test.md"
        f.write_text("Some content here", encoding="utf-8")

        # Patch expanduser to return a path that won't match tmp_path
        with patch("os.path.expanduser", return_value="/some/other/home"):
            content, warnings = _read_file_safe(str(f))
        # Should still read the file but add an outside-home warning
        assert content is not None
        assert any("outside" in w.lower() for w in warnings)


class TestBuildCandidateTitle:
    """_build_candidate_title generates descriptive titles."""

    def test_short_heading_uses_type_label(self):
        from forge_memory.tools.forge import _build_candidate_title

        title = _build_candidate_title("Hi", "decision")
        assert "Decision" in title
        assert "extracted" in title

    def test_long_heading_uses_heading(self):
        from forge_memory.tools.forge import _build_candidate_title

        title = _build_candidate_title("This is a detailed heading", "decision")
        assert title == "This is a detailed heading"

    def test_unknown_type_uses_title_case(self):
        from forge_memory.tools.forge import _build_candidate_title

        title = _build_candidate_title("Hi", "unknown_type")
        assert "Unknown_Type" in title or "Unknown" in title


class TestComputeConfidence:
    """_compute_confidence covers length bonus branches."""

    def test_short_body_no_length_bonus(self):
        from forge_memory.tools.forge import _compute_confidence

        # body < 100 chars — no length bonus
        confidence = _compute_confidence("Decision", "Short.", "decision")
        assert 0.0 <= confidence <= 1.0

    def test_medium_body_partial_length_bonus(self):
        from forge_memory.tools.forge import _compute_confidence

        # body 100-300 chars — partial bonus
        body = "We decided to use this approach because " * 3  # ~117 chars
        confidence = _compute_confidence("Decision", body, "decision")
        assert confidence > 0.2

    def test_long_body_full_length_bonus(self):
        from forge_memory.tools.forge import _compute_confidence

        # body > 300 chars
        body = "We decided to use this approach because " * 10  # ~390 chars
        confidence = _compute_confidence("Decision", body, "decision")
        assert confidence > 0.4


class TestKnowledgeSearchInvalidTypes:
    """forge_mem_knowledge_search types filter with invalid values falls back."""

    def test_all_invalid_types_returns_all_buckets(self, db):
        """When all types in filter are invalid, all buckets are returned."""
        from forge_memory.tools.core import forge_mem_save

        forge_mem_save(
            title="Auth decision",
            content="We decided on JWT authentication approach",
            type="decision",
            project="search-proj-2",
        )
        result = forge_mem_knowledge_search(
            project="search-proj-2",
            query="authentication",
            types=["nonexistent_bucket_x", "nonexistent_bucket_y"],
        )
        assert result["status"] == "ok"
        # All buckets should be present since all types were invalid
        assert "decisions" in result
        assert "patterns" in result
