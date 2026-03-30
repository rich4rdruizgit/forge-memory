"""CLI entrypoint for forge-memory MCP server.

Run with::

    python -m forge_memory
    python -m forge_memory serve
    python -m forge_memory clean-uninstall
    python -m forge_memory backup
    python -m forge_memory backup --format json
    # or via the installed console script:
    forge-memory
    forge-memory clean-uninstall --force
    forge-memory backup --output /tmp/my-backup.db

The server communicates over stdio using the MCP JSON-RPC protocol.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_BASE_DIR = Path.home() / ".forge-memory"
_DB_PATH = _BASE_DIR / "forge.db"
_CONFIG_PATH = _BASE_DIR / "config.yaml"
_MCP_CONFIG_PATH = Path.home() / ".claude" / "mcp" / "forge-memory.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_WRAPPER_SCRIPT = Path.home() / "bin" / "forge-memory-mcp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_size(size_bytes: int) -> str:
    """Format byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _print_item(label: str, path: Path, *, exists: bool | None = None) -> None:
    """Print a line describing a file/dir that will be removed."""
    if exists is None:
        exists = path.exists()
    if exists and path.is_file():
        size = _human_size(path.stat().st_size)
        print(f"  • {label}: {path} ({size})")
    elif exists:
        print(f"  • {label}: {path}")
    else:
        print(f"  • {label}: {path} (not found — skip)")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _cmd_serve(_args: argparse.Namespace) -> None:
    """Start the MCP server on stdio."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stderr,
    )

    from forge_memory.server import mcp  # noqa: PLC0415

    mcp.run(transport="stdio")


def _cmd_clean_uninstall(args: argparse.Namespace) -> None:
    """Remove all forge-memory data files."""
    keep_mcp: bool = args.keep_mcp
    force: bool = args.force

    db_path: Path = args._db_path  # noqa: SLF001
    config_path: Path = args._config_path  # noqa: SLF001
    base_dir: Path = args._base_dir  # noqa: SLF001
    mcp_config_path: Path = args._mcp_config_path  # noqa: SLF001

    settings_path: Path = args._settings_path  # noqa: SLF001
    wrapper_script: Path = args._wrapper_script  # noqa: SLF001

    db_exists = db_path.is_file()
    config_exists = config_path.is_file()
    base_dir_exists = base_dir.is_dir()
    mcp_exists = mcp_config_path.is_file()
    wrapper_exists = wrapper_script.is_file()

    # Check if settings.json has a forge-memory entry
    settings_has_entry = False
    if settings_path.is_file():
        try:
            settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
            settings_has_entry = "forge-memory" in settings_data.get("mcpServers", {})
        except (json.JSONDecodeError, OSError):
            pass

    # --- Show what will be removed ---
    print("forge-memory clean-uninstall")
    print("============================")
    print()
    print("The following items will be removed:")
    _print_item("Database", db_path, exists=db_exists)
    _print_item("Config", config_path, exists=config_exists)
    _print_item("Base directory", base_dir, exists=base_dir_exists)
    if keep_mcp:
        print(f"  • MCP config: {mcp_config_path} (kept — --keep-mcp)")
        print(f"  • Settings entry: {settings_path} (kept — --keep-mcp)")
    else:
        _print_item("MCP config", mcp_config_path, exists=mcp_exists)
        if settings_has_entry:
            print(f"  • Settings entry: mcpServers.forge-memory in {settings_path}")
        else:
            print(f"  • Settings entry: {settings_path} (no forge-memory entry — skip)")
    _print_item("Wrapper script", wrapper_script, exists=wrapper_exists)
    print()

    # Nothing to remove?
    has_something = (
        db_exists
        or config_exists
        or base_dir_exists
        or (mcp_exists and not keep_mcp)
        or (settings_has_entry and not keep_mcp)
        or wrapper_exists
    )
    if not has_something:
        print("Nothing to remove. Already clean.")
        return

    # --- Confirmation ---
    if not force:
        try:
            answer = input("¿Continuar? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if answer not in ("y", "yes", "si", "sí"):
            print("Aborted.")
            return

    # --- Remove ---
    removed: list[str] = []

    if db_exists:
        db_path.unlink()
        removed.append(f"Database: {db_path}")

    if config_exists:
        config_path.unlink()
        removed.append(f"Config: {config_path}")

    if base_dir_exists:
        # Remove directory only if empty (or force removal of remaining files)
        try:
            base_dir.rmdir()
            removed.append(f"Base directory: {base_dir}")
        except OSError:
            # Directory not empty — there are extra files the user put there
            shutil.rmtree(base_dir)
            removed.append(f"Base directory: {base_dir} (removed with contents)")

    if mcp_exists and not keep_mcp:
        mcp_config_path.unlink()
        removed.append(f"MCP config: {mcp_config_path}")

    if settings_has_entry and not keep_mcp:
        try:
            settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
            del settings_data["mcpServers"]["forge-memory"]
            # Remove mcpServers key entirely if empty
            if not settings_data["mcpServers"]:
                del settings_data["mcpServers"]
            settings_path.write_text(
                json.dumps(settings_data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            removed.append(f"Settings entry: mcpServers.forge-memory in {settings_path}")
        except (json.JSONDecodeError, OSError, KeyError):
            pass  # Best-effort — don't fail the uninstall

    if wrapper_exists:
        wrapper_script.unlink()
        removed.append(f"Wrapper script: {wrapper_script}")

    # --- Uninstall package ---
    pip_ok = False
    for pip_cmd in (["uv", "pip", "uninstall", "forge-memory", "-y"],
                    ["pip", "uninstall", "forge-memory", "-y"]):
        try:
            result = subprocess.run(  # noqa: S603
                pip_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                pip_ok = True
                removed.append(f"Package: forge-memory ({pip_cmd[0]})")
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if not pip_ok:
        removed.append("Package: forge-memory (could not uninstall — run manually)")

    # --- Summary ---
    print()
    print("Removed:")
    for item in removed:
        print(f"  ✓ {item}")
    print()
    if not pip_ok:
        print("Warning: could not uninstall the package automatically.")
        print("Run manually: uv pip uninstall forge-memory")


def _cmd_backup(args: argparse.Namespace) -> None:
    """Create a backup of the forge-memory database."""
    fmt: str = args.format
    quiet: bool = args.quiet
    db_path: Path = args._db_path  # noqa: SLF001
    backup_dir: Path = args._backup_dir  # noqa: SLF001
    output: str | None = args.output

    # --- Validate source DB exists ---
    if not db_path.is_file():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # --- Determine output path ---
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    ext = "json" if fmt == "json" else "db"

    if output:
        dest = Path(output)
    else:
        backup_dir.mkdir(parents=True, exist_ok=True)
        dest = backup_dir / f"forge-{timestamp}.{ext}"

    # Ensure parent directory exists for custom paths too
    dest.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        _backup_json(db_path, dest, quiet=quiet)
    else:
        _backup_sqlite(db_path, dest, quiet=quiet)


def _backup_sqlite(db_path: Path, dest: Path, *, quiet: bool = False) -> None:
    """Create a SQLite backup using the online backup API."""
    src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    size = dest.stat().st_size

    if quiet:
        print(dest)
    else:
        print(f"Backup created: {dest} ({_human_size(size)})")


_EXPORT_TABLES = ("observations", "tags", "sessions", "synonyms", "relations")


def _backup_json(db_path: Path, dest: Path, *, quiet: bool = False) -> None:
    """Export all data to a portable JSON file."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        data: dict = {}
        counts: dict[str, int] = {}

        for table in _EXPORT_TABLES:
            # Check if table exists (synonyms/relations might not exist on v1 DBs)
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                data[table] = [dict(row) for row in rows]
                counts[table] = len(data[table])
            else:
                data[table] = []
                counts[table] = 0
    finally:
        conn.close()

    export = {
        "metadata": {
            "version": "1.0.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
        },
        "data": data,
    }

    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(export, fh, indent=2, ensure_ascii=False, default=str)

    size = dest.stat().st_size

    if quiet:
        print(dest)
    else:
        print(f"JSON export created: {dest} ({_human_size(size)})")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-memory",
        description="Persistent memory MCP server for AI agents.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- serve ---
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the MCP server on stdio (default)",
    )
    serve_parser.set_defaults(func=_cmd_serve)

    # --- clean-uninstall ---
    uninstall_parser = subparsers.add_parser(
        "clean-uninstall",
        help="Remove all forge-memory data and configuration",
    )
    uninstall_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    uninstall_parser.add_argument(
        "--keep-mcp",
        action="store_true",
        help="Don't remove the MCP configuration file",
    )
    # Hidden defaults for testability (overridden via monkeypatch in tests)
    uninstall_parser.set_defaults(
        func=_cmd_clean_uninstall,
        _db_path=_DB_PATH,
        _config_path=_CONFIG_PATH,
        _base_dir=_BASE_DIR,
        _mcp_config_path=_MCP_CONFIG_PATH,
        _settings_path=_SETTINGS_PATH,
        _wrapper_script=_WRAPPER_SCRIPT,
    )

    # --- backup ---
    backup_parser = subparsers.add_parser(
        "backup",
        help="Create a backup of the forge-memory database",
    )
    backup_parser.add_argument(
        "--format",
        choices=("sqlite", "json"),
        default="sqlite",
        help="Backup format (default: sqlite)",
    )
    backup_parser.add_argument(
        "--output",
        default=None,
        help="Custom output path (overrides default location)",
    )
    backup_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output — print only the backup path",
    )
    # Hidden defaults for testability
    backup_parser.set_defaults(
        func=_cmd_backup,
        _db_path=_DB_PATH,
        _backup_dir=_BASE_DIR / "backups",
    )

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        # No subcommand → default to serve (backwards compat)
        _cmd_serve(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
