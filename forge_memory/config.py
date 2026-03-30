"""Configuration loading for forge-memory.

Three-layer config: defaults (dataclass) -> config.yaml -> env vars.
Environment variables always win.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_BASE_DIR = Path.home() / ".forge-memory"
_DEFAULT_DB_PATH = str(_DEFAULT_BASE_DIR / "forge.db")
_DEFAULT_CONFIG_PATH = str(_DEFAULT_BASE_DIR / "config.yaml")


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """forge-memory configuration.

    Fields map 1:1 to config.yaml keys (flattened from nested YAML structure).
    All paths support ``~`` expansion — resolved at load time.
    """

    db_path: str = _DEFAULT_DB_PATH
    search_level: int = 1  # Only 1 for v0.1
    similarity_threshold: float = 0.7
    auto_suggest_relations: bool = True
    max_relation_depth: int = 3
    default_project: str = "auto"


# ---------------------------------------------------------------------------
# YAML mapping (nested YAML key -> flat Config field)
# ---------------------------------------------------------------------------

_YAML_FIELD_MAP: dict[tuple[str, ...], str] = {
    ("storage", "db_path"): "db_path",
    ("search", "level"): "search_level",
    ("search", "fts5", "similarity_threshold"): "similarity_threshold",
    ("relations", "auto_suggest"): "auto_suggest_relations",
    ("relations", "max_depth"): "max_relation_depth",
    ("projects", "default"): "default_project",
}


def _extract_yaml_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Walk a nested dict following *keys*. Returns sentinel if any key is missing."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


_MISSING = object()


# ---------------------------------------------------------------------------
# Env-var mapping
# ---------------------------------------------------------------------------

_ENV_MAP: dict[str, str] = {
    "FORGE_MEMORY_DB": "db_path",
    "FORGE_MEMORY_LEVEL": "search_level",
}


def _coerce_env(field_name: str, raw: str) -> Any:
    """Convert a raw env-var string to the correct Python type for *field_name*."""
    if field_name == "search_level":
        return int(raw)
    return raw


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_path(path: str) -> str:
    """Expand ``~`` and make path absolute."""
    return str(Path(path).expanduser().resolve())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(config_path: str | None = None) -> Config:
    """Load configuration using the three-layer strategy.

    1. **Defaults** — hardcoded in :class:`Config`.
    2. **config.yaml** — located at *config_path*, ``FORGE_MEMORY_CONFIG``,
       or ``~/.forge-memory/config.yaml`` (in that priority order).
       Silently skipped when the file does not exist.
    3. **Environment variables** — ``FORGE_MEMORY_DB``, ``FORGE_MEMORY_LEVEL``
       override everything.

    Side effect: creates ``~/.forge-memory/`` if it doesn't exist.

    Returns a fully-resolved :class:`Config` instance (paths expanded).
    """

    cfg = Config()

    # --- Resolve which config.yaml to look for ---
    yaml_path_str = (
        config_path
        or os.environ.get("FORGE_MEMORY_CONFIG")
        or _DEFAULT_CONFIG_PATH
    )
    yaml_path = Path(yaml_path_str).expanduser().resolve()

    # --- Layer 2: YAML overlay ---
    if yaml_path.is_file():
        with open(yaml_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        if isinstance(data, dict):
            for keys, field_name in _YAML_FIELD_MAP.items():
                value = _extract_yaml_value(data, keys)
                if value is not _MISSING:
                    setattr(cfg, field_name, value)

    # --- Layer 3: env-var overrides ---
    for env_var, field_name in _ENV_MAP.items():
        raw = os.environ.get(env_var)
        if raw is not None:
            setattr(cfg, field_name, _coerce_env(field_name, raw))

    # --- Resolve paths (expand ~) ---
    cfg.db_path = _resolve_path(cfg.db_path)

    # --- Ensure base directory exists ---
    db_dir = Path(cfg.db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    return cfg
