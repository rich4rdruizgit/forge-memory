"""Tests for the CLI entrypoint (``forge_memory.__main__``)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_memory.__main__ import (
    _build_parser,
    _cmd_backup,
    _cmd_clean_uninstall,
    _cmd_serve,
    _human_size,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(path: Path, content: str = "data", size: int | None = None) -> None:
    """Create a file at *path*, optionally with a specific byte size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if size is not None:
        path.write_bytes(b"x" * size)
    else:
        path.write_text(content)


def _uninstall_args(
    tmp_path: Path,
    *,
    force: bool = False,
    keep_mcp: bool = False,
) -> argparse.Namespace:
    """Build a Namespace that points all paths into *tmp_path*."""
    base_dir = tmp_path / ".forge-memory"
    return argparse.Namespace(
        command="clean-uninstall",
        force=force,
        keep_mcp=keep_mcp,
        _db_path=base_dir / "forge.db",
        _config_path=base_dir / "config.yaml",
        _base_dir=base_dir,
        _mcp_config_path=tmp_path / ".claude" / "mcp" / "forge-memory.json",
        _settings_path=tmp_path / ".claude" / "settings.json",
        _wrapper_script=tmp_path / "bin" / "forge-memory-mcp",
    )


# ---------------------------------------------------------------------------
# _human_size
# ---------------------------------------------------------------------------


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(512) == "512 B"

    def test_kilobytes(self):
        assert _human_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _human_size(5 * 1024 * 1024) == "5.0 MB"


# ---------------------------------------------------------------------------
# serve subcommand
# ---------------------------------------------------------------------------


class TestServe:
    def test_serve_is_default_subcommand(self):
        """Running with no args should default to serve."""
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None  # triggers default serve path

    def test_serve_explicit(self):
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"

    def test_main_no_args_calls_serve(self):
        """``python -m forge_memory`` (no args) starts the server."""
        with patch("forge_memory.__main__._cmd_serve") as mock_serve:
            with patch("sys.argv", ["forge-memory"]):
                main()
            mock_serve.assert_called_once()

    def test_main_serve_calls_serve(self):
        """``python -m forge_memory serve`` starts the server."""
        with patch("forge_memory.__main__._cmd_serve") as mock_serve:
            with patch("sys.argv", ["forge-memory", "serve"]):
                main()
            mock_serve.assert_called_once()

    def test_cmd_serve_runs_mcp(self):
        """_cmd_serve imports and runs mcp.run(transport='stdio')."""
        mock_mcp = MagicMock()
        with patch.dict("sys.modules", {"forge_memory.server": MagicMock(mcp=mock_mcp)}):
            _cmd_serve(argparse.Namespace())
        mock_mcp.run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# clean-uninstall subcommand
# ---------------------------------------------------------------------------


class TestCleanUninstall:
    @pytest.fixture(autouse=True)
    def _mock_subprocess(self):
        """Prevent real pip/uv calls in all clean-uninstall tests by default."""
        with patch(
            "forge_memory.__main__.subprocess.run",
            return_value=MagicMock(returncode=0),
        ):
            yield

    def test_removes_db_and_config_dir(self, tmp_path, capsys):
        """With --force, removes DB, config, and base directory."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=4096)
        _make_file(args._config_path, content="storage:\n  db_path: test\n")

        _cmd_clean_uninstall(args)

        assert not args._db_path.exists()
        assert not args._config_path.exists()
        assert not args._base_dir.exists()

        output = capsys.readouterr().out
        assert "Removed:" in output
        assert "Database" in output

    def test_removes_mcp_config(self, tmp_path, capsys):
        """MCP config is removed by default."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=1024)
        _make_file(args._mcp_config_path, content='{"command": "forge-memory"}')

        _cmd_clean_uninstall(args)

        assert not args._mcp_config_path.exists()
        output = capsys.readouterr().out
        assert "MCP config" in output

    def test_keep_mcp_preserves_mcp_config(self, tmp_path, capsys):
        """--keep-mcp flag prevents MCP config removal."""
        args = _uninstall_args(tmp_path, force=True, keep_mcp=True)
        _make_file(args._db_path, size=1024)
        _make_file(args._mcp_config_path, content='{"command": "forge-memory"}')

        _cmd_clean_uninstall(args)

        assert not args._db_path.exists()
        assert args._mcp_config_path.exists()  # preserved!
        output = capsys.readouterr().out
        assert "kept — --keep-mcp" in output

    def test_force_skips_confirmation(self, tmp_path, monkeypatch, capsys):
        """--force never calls input()."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)

        # If input() is called, this will blow up
        monkeypatch.setattr("builtins.input", lambda _: pytest.fail("input() should not be called"))

        _cmd_clean_uninstall(args)

        assert not args._db_path.exists()

    def test_prompts_for_confirmation_yes(self, tmp_path, monkeypatch, capsys):
        """Without --force, prompts and proceeds on 'y'."""
        args = _uninstall_args(tmp_path, force=False)
        _make_file(args._db_path, size=512)

        monkeypatch.setattr("builtins.input", lambda _: "y")

        _cmd_clean_uninstall(args)

        assert not args._db_path.exists()

    def test_prompts_for_confirmation_no(self, tmp_path, monkeypatch, capsys):
        """Without --force, aborts on 'n'."""
        args = _uninstall_args(tmp_path, force=False)
        _make_file(args._db_path, size=512)

        monkeypatch.setattr("builtins.input", lambda _: "n")

        _cmd_clean_uninstall(args)

        assert args._db_path.exists()  # NOT removed
        output = capsys.readouterr().out
        assert "Aborted" in output

    def test_prompts_for_confirmation_empty(self, tmp_path, monkeypatch, capsys):
        """Empty input (just Enter) defaults to abort."""
        args = _uninstall_args(tmp_path, force=False)
        _make_file(args._db_path, size=512)

        monkeypatch.setattr("builtins.input", lambda _: "")

        _cmd_clean_uninstall(args)

        assert args._db_path.exists()
        output = capsys.readouterr().out
        assert "Aborted" in output

    def test_no_files_exist(self, tmp_path, capsys):
        """When nothing exists, prints clean message without errors."""
        args = _uninstall_args(tmp_path, force=True)

        _cmd_clean_uninstall(args)

        output = capsys.readouterr().out
        assert "Nothing to remove" in output
        assert "Already clean" in output

    def test_shows_file_sizes(self, tmp_path, capsys):
        """Preview shows file sizes for existing files."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=2048)

        _cmd_clean_uninstall(args)

        output = capsys.readouterr().out
        assert "2.0 KB" in output

    def test_shows_not_found_for_missing(self, tmp_path, capsys):
        """Preview shows 'not found' for missing files."""
        args = _uninstall_args(tmp_path, force=True)
        # Only create DB, not config or MCP
        _make_file(args._db_path, size=512)

        _cmd_clean_uninstall(args)

        output = capsys.readouterr().out
        assert "not found" in output

    def test_uninstalls_package_via_uv(self, tmp_path, capsys):
        """Runs uv pip uninstall as part of clean-uninstall."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)

        mock_result = MagicMock(returncode=0)
        with patch("forge_memory.__main__.subprocess.run", return_value=mock_result) as mock_run:
            _cmd_clean_uninstall(args)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["uv", "pip", "uninstall", "forge-memory", "-y"]

        output = capsys.readouterr().out
        assert "Package: forge-memory" in output

    def test_falls_back_to_pip_when_uv_missing(self, tmp_path, capsys):
        """Falls back to pip when uv is not found."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)

        def side_effect(cmd, **kwargs):
            if cmd[0] == "uv":
                raise FileNotFoundError("uv not found")
            return MagicMock(returncode=0)

        with patch("forge_memory.__main__.subprocess.run", side_effect=side_effect) as mock_run:
            _cmd_clean_uninstall(args)

        assert mock_run.call_count == 2
        assert mock_run.call_args_list[1][0][0] == ["pip", "uninstall", "forge-memory", "-y"]

        output = capsys.readouterr().out
        assert "Package: forge-memory (pip)" in output

    def test_warns_when_package_uninstall_fails(self, tmp_path, capsys):
        """Shows warning when neither uv nor pip can uninstall."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)

        with patch("forge_memory.__main__.subprocess.run", side_effect=FileNotFoundError):
            _cmd_clean_uninstall(args)

        output = capsys.readouterr().out
        assert "could not uninstall" in output

    def test_removes_wrapper_script(self, tmp_path, capsys):
        """Removes ~/bin/forge-memory-mcp wrapper script."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        _make_file(args._wrapper_script, content="#!/bin/bash\nexec python -m forge_memory")

        _cmd_clean_uninstall(args)

        assert not args._wrapper_script.exists()
        output = capsys.readouterr().out
        assert "Wrapper script" in output

    def test_wrapper_script_missing_is_fine(self, tmp_path, capsys):
        """No error when wrapper script doesn't exist."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)

        _cmd_clean_uninstall(args)

        output = capsys.readouterr().out
        assert "Wrapper script" in output
        assert "not found" in output

    def test_removes_base_dir_with_extra_files(self, tmp_path, capsys):
        """Base dir with extra files is still removed via shutil.rmtree."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        # Create an extra file the user put there
        extra = args._base_dir / "notes.txt"
        _make_file(extra, content="my notes")

        _cmd_clean_uninstall(args)

        assert not args._base_dir.exists()
        output = capsys.readouterr().out
        assert "removed with contents" in output

    def test_keyboard_interrupt_aborts(self, tmp_path, monkeypatch, capsys):
        """Ctrl+C during prompt aborts gracefully."""
        args = _uninstall_args(tmp_path, force=False)
        _make_file(args._db_path, size=512)

        def raise_interrupt(_):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_interrupt)

        _cmd_clean_uninstall(args)

        assert args._db_path.exists()

    def test_eof_aborts(self, tmp_path, monkeypatch, capsys):
        """EOF on stdin aborts gracefully (piped input)."""
        args = _uninstall_args(tmp_path, force=False)
        _make_file(args._db_path, size=512)

        def raise_eof(_):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)

        _cmd_clean_uninstall(args)

        assert args._db_path.exists()

    def test_removes_settings_json_entry(self, tmp_path, capsys):
        """settings.json forge-memory entry is removed during clean uninstall."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        settings = {
            "mcpServers": {
                "forge-memory": {"command": "/usr/bin/forge-memory-mcp"},
                "other-server": {"command": "/usr/bin/other"},
            }
        }
        _make_file(args._settings_path, content=json.dumps(settings, indent=2))

        _cmd_clean_uninstall(args)

        result = json.loads(args._settings_path.read_text(encoding="utf-8"))
        assert "forge-memory" not in result["mcpServers"]
        assert "other-server" in result["mcpServers"]
        output = capsys.readouterr().out
        assert "mcpServers.forge-memory" in output

    def test_settings_json_preserves_other_entries(self, tmp_path, capsys):
        """Other mcpServers entries are NOT removed from settings.json."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        settings = {
            "mcpServers": {
                "forge-memory": {"command": "/usr/bin/forge-memory-mcp"},
                "alpha": {"command": "/usr/bin/alpha"},
                "beta": {"command": "/usr/bin/beta"},
            },
            "otherKey": "preserved",
        }
        _make_file(args._settings_path, content=json.dumps(settings, indent=2))

        _cmd_clean_uninstall(args)

        result = json.loads(args._settings_path.read_text(encoding="utf-8"))
        assert "forge-memory" not in result["mcpServers"]
        assert result["mcpServers"]["alpha"]["command"] == "/usr/bin/alpha"
        assert result["mcpServers"]["beta"]["command"] == "/usr/bin/beta"
        assert result["otherKey"] == "preserved"

    def test_settings_json_kept_with_keep_mcp(self, tmp_path, capsys):
        """--keep-mcp preserves the settings.json forge-memory entry."""
        args = _uninstall_args(tmp_path, force=True, keep_mcp=True)
        _make_file(args._db_path, size=512)
        settings = {
            "mcpServers": {
                "forge-memory": {"command": "/usr/bin/forge-memory-mcp"},
            }
        }
        _make_file(args._settings_path, content=json.dumps(settings, indent=2))

        _cmd_clean_uninstall(args)

        result = json.loads(args._settings_path.read_text(encoding="utf-8"))
        assert "forge-memory" in result["mcpServers"]
        output = capsys.readouterr().out
        assert "kept — --keep-mcp" in output

    def test_settings_json_without_forge_memory_entry(self, tmp_path, capsys):
        """settings.json without forge-memory entry is handled gracefully."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        settings = {
            "mcpServers": {
                "other-server": {"command": "/usr/bin/other"},
            }
        }
        _make_file(args._settings_path, content=json.dumps(settings, indent=2))

        _cmd_clean_uninstall(args)

        # File should be untouched
        result = json.loads(args._settings_path.read_text(encoding="utf-8"))
        assert result["mcpServers"]["other-server"]["command"] == "/usr/bin/other"
        output = capsys.readouterr().out
        assert "no forge-memory entry" in output

    def test_settings_json_missing_file(self, tmp_path, capsys):
        """Missing settings.json is handled gracefully."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        # Don't create settings.json

        _cmd_clean_uninstall(args)

        # Should not crash, should show skip message
        output = capsys.readouterr().out
        assert "no forge-memory entry" in output

    def test_settings_json_removes_empty_mcp_servers(self, tmp_path, capsys):
        """mcpServers key is removed if forge-memory was the only entry."""
        args = _uninstall_args(tmp_path, force=True)
        _make_file(args._db_path, size=512)
        settings = {
            "mcpServers": {
                "forge-memory": {"command": "/usr/bin/forge-memory-mcp"},
            },
            "otherKey": True,
        }
        _make_file(args._settings_path, content=json.dumps(settings, indent=2))

        _cmd_clean_uninstall(args)

        result = json.loads(args._settings_path.read_text(encoding="utf-8"))
        assert "mcpServers" not in result
        assert result["otherKey"] is True


# ---------------------------------------------------------------------------
# Parser structure
# ---------------------------------------------------------------------------


class TestParser:
    def test_clean_uninstall_parsed(self):
        parser = _build_parser()
        args = parser.parse_args(["clean-uninstall", "--force", "--keep-mcp"])
        assert args.command == "clean-uninstall"
        assert args.force is True
        assert args.keep_mcp is True

    def test_clean_uninstall_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["clean-uninstall"])
        assert args.force is False
        assert args.keep_mcp is False

    def test_backup_parsed(self):
        parser = _build_parser()
        args = parser.parse_args(["backup", "--format", "json", "--quiet"])
        assert args.command == "backup"
        assert args.format == "json"
        assert args.quiet is True

    def test_backup_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["backup"])
        assert args.format == "sqlite"
        assert args.quiet is False
        assert args.output is None


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------


def _create_test_db(db_path: Path) -> None:
    """Create a minimal forge-memory DB with sample data for backup tests."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Minimal schema (enough for backup testing — no FTS5 triggers)
    conn.executescript("""
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL,
            scope TEXT DEFAULT 'project',
            project TEXT NOT NULL,
            topic_key TEXT,
            tags_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            feature_slug TEXT,
            quality_score REAL,
            is_active BOOLEAN DEFAULT 1
        );
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL,
            tag TEXT NOT NULL
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            summary TEXT,
            feature_slug TEXT
        );
        CREATE TABLE synonyms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            synonym TEXT NOT NULL,
            language TEXT DEFAULT 'es'
        );
        CREATE TABLE relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Insert sample data
    conn.execute(
        "INSERT INTO observations (title, content, type, project, topic_key) "
        "VALUES ('Test decision', 'We chose X over Y', 'decision', 'test-proj', 'arch/db')"
    )
    conn.execute(
        "INSERT INTO observations (title, content, type, project) "
        "VALUES ('Bug found', 'Off by one in loop', 'bugfix', 'test-proj')"
    )
    conn.execute("INSERT INTO tags (observation_id, tag) VALUES (1, 'architecture')")
    conn.execute("INSERT INTO tags (observation_id, tag) VALUES (1, 'database')")
    conn.execute(
        "INSERT INTO sessions (project, summary) VALUES ('test-proj', 'Initial setup')"
    )
    conn.execute(
        "INSERT INTO synonyms (term, synonym, language) VALUES ('db', 'database', 'en')"
    )
    conn.execute(
        "INSERT INTO relations (source_id, target_id, relation_type) VALUES (1, 2, 'related')"
    )
    conn.commit()
    conn.close()


def _backup_args(
    tmp_path: Path,
    *,
    fmt: str = "sqlite",
    output: str | None = None,
    quiet: bool = False,
) -> argparse.Namespace:
    """Build a Namespace for the backup subcommand pointing into *tmp_path*."""
    base_dir = tmp_path / ".forge-memory"
    return argparse.Namespace(
        command="backup",
        format=fmt,
        output=output,
        quiet=quiet,
        _db_path=base_dir / "forge.db",
        _backup_dir=base_dir / "backups",
    )


# ---------------------------------------------------------------------------
# backup subcommand
# ---------------------------------------------------------------------------


class TestBackup:
    def test_sqlite_backup_creates_valid_copy(self, tmp_path, capsys):
        """Default sqlite backup produces a readable DB with correct data."""
        args = _backup_args(tmp_path)
        _create_test_db(args._db_path)

        _cmd_backup(args)

        output = capsys.readouterr().out
        assert "Backup created:" in output

        # Find the created backup file
        backup_dir = args._backup_dir
        backups = list(backup_dir.glob("forge-*.db"))
        assert len(backups) == 1

        # Verify it's a valid SQLite DB with correct data
        conn = sqlite3.connect(str(backups[0]))
        rows = conn.execute("SELECT title FROM observations").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "Test decision"
        conn.close()

    def test_json_export_structure(self, tmp_path, capsys):
        """JSON export contains all tables with correct structure."""
        args = _backup_args(tmp_path, fmt="json")
        _create_test_db(args._db_path)

        _cmd_backup(args)

        output = capsys.readouterr().out
        assert "JSON export created:" in output

        backups = list(args._backup_dir.glob("forge-*.json"))
        assert len(backups) == 1

        with open(backups[0], encoding="utf-8") as fh:
            export = json.load(fh)

        # Top-level keys
        assert "metadata" in export
        assert "data" in export

        # All tables present
        for table in ("observations", "tags", "sessions", "synonyms", "relations"):
            assert table in export["data"]

        # Correct row counts
        assert len(export["data"]["observations"]) == 2
        assert len(export["data"]["tags"]) == 2
        assert len(export["data"]["sessions"]) == 1
        assert len(export["data"]["synonyms"]) == 1
        assert len(export["data"]["relations"]) == 1

    def test_json_metadata_fields(self, tmp_path, capsys):
        """JSON metadata has version, timestamp, and counts."""
        args = _backup_args(tmp_path, fmt="json")
        _create_test_db(args._db_path)

        _cmd_backup(args)

        backups = list(args._backup_dir.glob("forge-*.json"))
        with open(backups[0], encoding="utf-8") as fh:
            export = json.load(fh)

        meta = export["metadata"]
        assert meta["version"] == "1.0.0"
        assert "exported_at" in meta
        # ISO 8601 timestamp should contain 'T'
        assert "T" in meta["exported_at"]
        assert meta["counts"]["observations"] == 2
        assert meta["counts"]["tags"] == 2
        assert meta["counts"]["sessions"] == 1
        assert meta["counts"]["synonyms"] == 1
        assert meta["counts"]["relations"] == 1

    def test_output_flag_custom_path(self, tmp_path, capsys):
        """--output writes to the specified custom path."""
        custom = tmp_path / "custom" / "my-backup.db"
        args = _backup_args(tmp_path, output=str(custom))
        _create_test_db(args._db_path)

        _cmd_backup(args)

        assert custom.is_file()
        # Verify the DB is valid
        conn = sqlite3.connect(str(custom))
        count = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
        assert count == 2
        conn.close()

    def test_output_flag_json(self, tmp_path, capsys):
        """--output with --format json writes JSON to custom path."""
        custom = tmp_path / "export.json"
        args = _backup_args(tmp_path, fmt="json", output=str(custom))
        _create_test_db(args._db_path)

        _cmd_backup(args)

        assert custom.is_file()
        with open(custom, encoding="utf-8") as fh:
            export = json.load(fh)
        assert export["metadata"]["version"] == "1.0.0"

    def test_quiet_flag_sqlite(self, tmp_path, capsys):
        """--quiet shows only the backup path."""
        args = _backup_args(tmp_path, quiet=True)
        _create_test_db(args._db_path)

        _cmd_backup(args)

        output = capsys.readouterr().out.strip()
        # Should be just a path, no extra text
        assert output.endswith(".db")
        assert "Backup created" not in output
        assert Path(output).is_file()

    def test_quiet_flag_json(self, tmp_path, capsys):
        """--quiet with JSON shows only the export path."""
        args = _backup_args(tmp_path, fmt="json", quiet=True)
        _create_test_db(args._db_path)

        _cmd_backup(args)

        output = capsys.readouterr().out.strip()
        assert output.endswith(".json")
        assert "JSON export" not in output

    def test_missing_db_shows_error(self, tmp_path, capsys):
        """When the DB doesn't exist, exits with a clear error."""
        args = _backup_args(tmp_path)
        # Do NOT create the DB

        with pytest.raises(SystemExit) as exc_info:
            _cmd_backup(args)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "database not found" in err

    def test_backups_directory_created_automatically(self, tmp_path, capsys):
        """The backups/ subdirectory is created if it doesn't exist."""
        args = _backup_args(tmp_path)
        _create_test_db(args._db_path)
        assert not args._backup_dir.exists()

        _cmd_backup(args)

        assert args._backup_dir.is_dir()
        backups = list(args._backup_dir.glob("forge-*"))
        assert len(backups) == 1

    def test_sqlite_backup_shows_size(self, tmp_path, capsys):
        """Normal output includes the backup file size."""
        args = _backup_args(tmp_path)
        _create_test_db(args._db_path)

        _cmd_backup(args)

        output = capsys.readouterr().out
        # Should show a size like "12.0 KB" or "8192 B"
        assert "B" in output or "KB" in output

    def test_json_export_missing_tables_graceful(self, tmp_path, capsys):
        """JSON export handles DBs missing optional tables (e.g., v1 without synonyms)."""
        db_path = tmp_path / ".forge-memory" / "forge.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        # Only create observations — skip synonyms and relations
        conn.executescript("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY, title TEXT, content TEXT,
                type TEXT, project TEXT
            );
            CREATE TABLE tags (id INTEGER PRIMARY KEY, observation_id INTEGER, tag TEXT);
            CREATE TABLE sessions (id INTEGER PRIMARY KEY, project TEXT);
            INSERT INTO observations VALUES (1, 'test', 'content', 'decision', 'proj');
        """)
        conn.commit()
        conn.close()

        args = _backup_args(tmp_path, fmt="json")

        _cmd_backup(args)

        backups = list(args._backup_dir.glob("forge-*.json"))
        with open(backups[0], encoding="utf-8") as fh:
            export = json.load(fh)

        assert export["data"]["observations"] == [
            {"id": 1, "title": "test", "content": "content", "type": "decision", "project": "proj"}
        ]
        # Missing tables should appear as empty lists
        assert export["data"]["synonyms"] == []
        assert export["data"]["relations"] == []
        assert export["metadata"]["counts"]["synonyms"] == 0
