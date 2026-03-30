#!/usr/bin/env bash
set -euo pipefail

# forge-memory install script
# Registers the MCP server in Claude Code settings and prepares the data directory.

FORGE_DIR="$HOME/.forge-memory"
SETTINGS_FILE="$HOME/.claude/settings.json"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; }

# --- Step 1: Create data directory ---
if [ ! -d "$FORGE_DIR" ]; then
    mkdir -p "$FORGE_DIR"
    info "Created $FORGE_DIR"
else
    info "$FORGE_DIR already exists"
fi

# --- Step 2: Check forge-memory is installed ---
if command -v forge-memory &>/dev/null; then
    info "forge-memory command found: $(command -v forge-memory)"
elif python3 -m forge_memory --help &>/dev/null 2>&1; then
    warn "forge-memory not on PATH but importable via python3 -m forge_memory"
    warn "Consider installing with: pip install -e . or uv pip install -e ."
else
    error "forge-memory is not installed."
    echo "  Install it first:"
    echo "    pip install forge-memory"
    echo "    # or"
    echo "    uv pip install forge-memory"
    echo "    # or from source:"
    echo "    uv pip install -e \".[dev]\""
    exit 1
fi

# --- Step 3: Register MCP server in Claude Code settings ---
# Using python3 for reliable JSON manipulation (jq not guaranteed on all systems)
python3 << 'PYEOF'
import json
import os
import sys

settings_path = os.path.expanduser("~/.claude/settings.json")
claude_dir = os.path.dirname(settings_path)

# MCP server entry to add
mcp_entry = {
    "command": "forge-memory",
    "args": ["serve"],
    "env": {
        "FORGE_MEMORY_DB": "~/.forge-memory/forge.db",
        "FORGE_MEMORY_LEVEL": "1"
    }
}

# Load existing settings or start fresh
settings = {}
if os.path.isfile(settings_path):
    try:
        with open(settings_path, "r") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"\033[0;31m[✗]\033[0m Failed to parse {settings_path}: {e}", file=sys.stderr)
        sys.exit(1)

# Ensure mcpServers key exists
if "mcpServers" not in settings:
    settings["mcpServers"] = {}

# Check if already registered
if "forge-memory" in settings["mcpServers"]:
    existing = settings["mcpServers"]["forge-memory"]
    if existing == mcp_entry:
        print(f"\033[0;32m[✓]\033[0m forge-memory already registered in {settings_path}")
        sys.exit(0)
    else:
        print(f"\033[1;33m[!]\033[0m Updating existing forge-memory entry in {settings_path}")

# Write merged settings
settings["mcpServers"]["forge-memory"] = mcp_entry

# Ensure ~/.claude/ directory exists
os.makedirs(claude_dir, exist_ok=True)

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"\033[0;32m[✓]\033[0m Registered forge-memory MCP server in {settings_path}")
PYEOF

# --- Step 4: Success ---
echo ""
echo "============================================"
echo "  forge-memory installed successfully!"
echo "============================================"
echo ""
echo "  Data dir:  $FORGE_DIR"
echo "  Settings:  $SETTINGS_FILE"
echo ""
echo "  Next steps:"
echo "    1. Restart Claude Code to pick up the new MCP server"
echo ""
echo "  To verify: claude and then ask the agent to use mem_save"
echo ""
