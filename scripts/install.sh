#!/usr/bin/env bash
set -euo pipefail

# forge-memory install script
# Registers the MCP server in Claude Code settings and prepares the data directory.

FORGE_DIR="$HOME/.forge-memory"
MCP_DIR="$HOME/.claude/mcp"
MCP_FILE="$MCP_DIR/forge-memory.json"

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

# --- Step 3: Register MCP server in ~/.claude/mcp/forge-memory.json ---
mkdir -p "$MCP_DIR"

MCP_CONTENT='{
  "command": "forge-memory",
  "args": ["serve"],
  "env": {
    "FORGE_MEMORY_DB": "~/.forge-memory/forge.db",
    "FORGE_MEMORY_LEVEL": "1"
  }
}'

if [ -f "$MCP_FILE" ]; then
    EXISTING=$(cat "$MCP_FILE")
    if [ "$EXISTING" = "$MCP_CONTENT" ]; then
        info "forge-memory already registered in $MCP_FILE"
    else
        echo "$MCP_CONTENT" > "$MCP_FILE"
        warn "Updated existing $MCP_FILE"
    fi
else
    echo "$MCP_CONTENT" > "$MCP_FILE"
    info "Registered forge-memory MCP server in $MCP_FILE"
fi

# --- Step 4: Success ---
echo ""
echo "============================================"
echo "  forge-memory installed successfully!"
echo "============================================"
echo ""
echo "  Data dir:  $FORGE_DIR"
echo "  MCP config: $MCP_FILE"
echo ""
echo "  Next steps:"
echo "    1. Restart Claude Code to pick up the new MCP server"
echo ""
echo "  To verify: claude and then ask the agent to use mem_save"
echo ""
