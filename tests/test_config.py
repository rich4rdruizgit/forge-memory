"""Tests for forge_memory.config — three-layer configuration loading."""

from pathlib import Path

import pytest
import yaml

from forge_memory.config import Config, _coerce_env, _extract_yaml_value, _MISSING, load_config


# ---------------------------------------------------------------------------
# Config dataclass defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """Config dataclass has correct default values."""

    def test_defaults(self):
        cfg = Config()
        assert cfg.db_path.endswith(".forge-memory/forge.db")
        assert cfg.search_level == 1
        assert cfg.similarity_threshold == 0.7
        assert cfg.auto_suggest_relations is True
        assert cfg.max_relation_depth == 3
        assert cfg.default_project == "auto"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestExtractYamlValue:
    """_extract_yaml_value walks nested dicts correctly."""

    def test_single_level(self):
        assert _extract_yaml_value({"a": 1}, ("a",)) == 1

    def test_nested(self):
        data = {"storage": {"db_path": "/tmp/db"}}
        assert _extract_yaml_value(data, ("storage", "db_path")) == "/tmp/db"

    def test_missing_key_returns_sentinel(self):
        assert _extract_yaml_value({"a": 1}, ("b",)) is _MISSING

    def test_intermediate_not_dict_returns_sentinel(self):
        assert _extract_yaml_value({"a": "not_a_dict"}, ("a", "b")) is _MISSING

    def test_deeply_nested(self):
        data = {"search": {"fts5": {"similarity_threshold": 0.8}}}
        assert _extract_yaml_value(data, ("search", "fts5", "similarity_threshold")) == 0.8


class TestCoerceEnv:
    """_coerce_env converts raw strings to the right types."""

    def test_search_level_coercion(self):
        assert _coerce_env("search_level", "2") == 2
        assert isinstance(_coerce_env("search_level", "2"), int)

    def test_string_passthrough(self):
        assert _coerce_env("db_path", "/some/path") == "/some/path"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfigDefaults:
    """load_config returns sensible defaults when no YAML and no env vars."""

    def test_defaults_no_yaml_no_env(self, tmp_path, monkeypatch):
        # Point config to a non-existent yaml so it uses defaults
        fake_config = str(tmp_path / "nope.yaml")
        # Clear any env vars that could interfere
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        cfg = load_config(config_path=fake_config)

        assert cfg.search_level == 1
        assert cfg.similarity_threshold == 0.7
        # db_path should be resolved (no ~ left)
        assert "~" not in cfg.db_path


class TestLoadConfigYaml:
    """load_config reads values from a YAML file."""

    def test_yaml_overrides_defaults(self, tmp_path, monkeypatch):
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({
            "storage": {"db_path": str(tmp_path / "custom.db")},
            "search": {"level": 2, "fts5": {"similarity_threshold": 0.9}},
            "relations": {"auto_suggest": False, "max_depth": 5},
            "projects": {"default": "my-proj"},
        }))

        cfg = load_config(config_path=str(yaml_file))

        assert cfg.db_path == str((tmp_path / "custom.db").resolve())
        assert cfg.search_level == 2
        assert cfg.similarity_threshold == 0.9
        assert cfg.auto_suggest_relations is False
        assert cfg.max_relation_depth == 5
        assert cfg.default_project == "my-proj"

    def test_partial_yaml(self, tmp_path, monkeypatch):
        """YAML with only some keys — rest stay as defaults."""
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({"search": {"level": 2}}))

        cfg = load_config(config_path=str(yaml_file))

        assert cfg.search_level == 2
        assert cfg.similarity_threshold == 0.7  # default preserved

    def test_empty_yaml_file(self, tmp_path, monkeypatch):
        """Empty YAML file — all defaults."""
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("")

        cfg = load_config(config_path=str(yaml_file))

        assert cfg.search_level == 1


class TestLoadConfigEnvVars:
    """Environment variables override YAML and defaults."""

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({
            "storage": {"db_path": str(tmp_path / "yaml.db")},
            "search": {"level": 2},
        }))

        env_db = str(tmp_path / "env.db")
        monkeypatch.setenv("FORGE_MEMORY_DB", env_db)
        monkeypatch.setenv("FORGE_MEMORY_LEVEL", "3")
        monkeypatch.delenv("FORGE_MEMORY_CONFIG", raising=False)

        cfg = load_config(config_path=str(yaml_file))

        assert cfg.db_path == str(Path(env_db).resolve())
        assert cfg.search_level == 3

    def test_env_overrides_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORGE_MEMORY_LEVEL", "5")
        monkeypatch.delenv("FORGE_MEMORY_DB", raising=False)
        monkeypatch.delenv("FORGE_MEMORY_CONFIG", raising=False)

        cfg = load_config(config_path=str(tmp_path / "nope.yaml"))

        assert cfg.search_level == 5


class TestLoadConfigMissingYaml:
    """Missing config.yaml doesn't raise — uses defaults silently."""

    def test_no_error_on_missing_yaml(self, tmp_path, monkeypatch):
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        # This file does not exist
        cfg = load_config(config_path=str(tmp_path / "nonexistent.yaml"))
        # Should succeed and return defaults
        assert cfg.search_level == 1


class TestLoadConfigPathResolution:
    """Path expansion and directory creation."""

    def test_tilde_is_expanded(self, tmp_path, monkeypatch):
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        cfg = load_config(config_path=str(tmp_path / "nope.yaml"))
        assert "~" not in cfg.db_path

    def test_db_directory_created(self, tmp_path, monkeypatch):
        _env_vars = (
            "FORGE_MEMORY_DB",
            "FORGE_MEMORY_LEVEL",
            "FORGE_MEMORY_CONFIG",
        )
        for var in _env_vars:
            monkeypatch.delenv(var, raising=False)

        nested = tmp_path / "deep" / "nested"
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({"storage": {"db_path": str(nested / "forge.db")}}))

        load_config(config_path=str(yaml_file))

        assert nested.is_dir()

    def test_forge_memory_config_env_var(self, tmp_path, monkeypatch):
        """FORGE_MEMORY_CONFIG env var is used when no explicit config_path."""
        monkeypatch.delenv("FORGE_MEMORY_DB", raising=False)
        monkeypatch.delenv("FORGE_MEMORY_LEVEL", raising=False)

        yaml_file = tmp_path / "custom-config.yaml"
        yaml_file.write_text(yaml.dump({"search": {"level": 3}}))
        monkeypatch.setenv("FORGE_MEMORY_CONFIG", str(yaml_file))

        cfg = load_config()  # No explicit config_path — should pick up env var
        assert cfg.search_level == 3
