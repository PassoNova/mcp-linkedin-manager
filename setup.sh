#!/usr/bin/env bash
# setup.sh — uv environment + Claude Code CLI MCP registration
# Run from the repo root: bash setup.sh
#
# For Cowork (desktop app): install the .plugin file instead.
# Build it with: cd repo-root && zip -r linkedin-mcp.plugin . -x "*.DS_Store" -x "*/.venv/*" -x "*/__pycache__/*"

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$PROJECT_DIR/mcp"
ENV_FILE="$PROJECT_DIR/.env"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      LinkedIn MCP — Setup Script          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Ensure uv is available ─────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "▶ uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    echo "✅ uv installed: $(uv --version)"
else
    echo "✅ uv already installed: $(uv --version)"
fi

# ── 2. Create virtual environment in mcp/ ─────────────────────────────────────
echo ""
echo "▶ Creating virtual environment in mcp/..."
cd "$MCP_DIR"
uv venv --python 3.11 2>/dev/null || uv venv
echo "✅ Virtual environment ready at $MCP_DIR/.venv"

# ── 3. Install dependencies ───────────────────────────────────────────────────
echo ""
echo "▶ Installing dependencies..."
uv pip install -e .
echo "✅ Dependencies installed"

# ── 4. Verify server syntax ───────────────────────────────────────────────────
echo ""
echo "▶ Verifying server syntax..."
uv run python -m py_compile server.py auth.py client.py
echo "✅ Server syntax OK"

# ── 5. Register with Claude Code CLI ─────────────────────────────────────────
# No credentials are passed on the command line or stored in Claude's config:
# `claude mcp add --env` would persist the client secret in plaintext. The
# server reads them from the OS keychain (`python -m linkedin_mcp setup`), or
# from LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET in its own environment.
VENV_PYTHON="$MCP_DIR/.venv/bin/python"

echo ""
echo "▶ Registering linkedin-manager with Claude Code CLI..."
claude mcp remove linkedin-manager 2>/dev/null && echo "   (removed previous registration)" || true

claude mcp add linkedin-manager \
    --scope user \
    -- "$VENV_PYTHON" "$MCP_DIR/server.py"

# ── 6. Store app credentials in the OS keychain ──────────────────────────────
echo ""
if [ -f "$ENV_FILE" ] && grep -qE '^LINKEDIN_CLIENT_SECRET=.+' "$ENV_FILE"; then
    echo "ℹ️  Found $ENV_FILE — the server reads it as a fallback; run the setup"
    echo "   wizard below to move the credentials into the keychain, then delete the file."
fi
if [ -t 0 ]; then
    echo "▶ Storing LinkedIn app credentials in the OS keychain..."
    uv run python -m linkedin_mcp setup
else
    echo "⚠️  Non-interactive shell — skipping credential setup. Run this later:"
    echo "     cd $MCP_DIR && uv run python -m linkedin_mcp setup"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║            ✅ Setup complete!             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Open a new Claude Code session:  claude"
echo "  2. Say: 'Authenticate with LinkedIn'"
echo "  3. Claude will open your browser for OAuth authorization"
echo ""
echo "Verify registration:  claude mcp list"
echo ""
