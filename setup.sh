#!/usr/bin/env bash
# setup.sh — uv environment + Claude Code CLI MCP registration
# Run from the repo root: bash setup.sh
#
# For Cowork (desktop app): install the .plugin file instead.
# Build it with: cd repo-root && zip -r linkedin-mcp.plugin . -x "*.DS_Store" -x "*/.venv/*" -x "*/__pycache__/*"

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$PROJECT_DIR/mcp"
ENV_FILE="$MCP_DIR/.env"          # what load_dotenv() reads (cwd is mcp/)
LEGACY_ENV_FILE="$PROJECT_DIR/.env"  # older layout; the server no longer reads it

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

# ── 2. Create the virtual environment and install locked dependencies ────────
# `uv sync` (not `uv pip install`) so mcp/uv.lock and the
# [tool.uv] constraint-dependencies floors in pyproject.toml are honoured.
echo ""
echo "▶ Installing dependencies from uv.lock into mcp/.venv..."
cd "$MCP_DIR"
uv sync
echo "✅ Dependencies installed at $MCP_DIR/.venv"

# ── 3. (merged into step 2) ───────────────────────────────────────────────────

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
if [ -n "${LINKEDIN_CLIENT_ID:-}" ] || [ -n "${LINKEDIN_CLIENT_SECRET:-}" ]; then
    echo "⚠️  LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET are set in this shell but are NOT"
    echo "   persisted or registered by this script (that would store the secret in"
    echo "   plaintext in Claude's config). Store them in the keychain instead:"
    echo "     cd $MCP_DIR && uv run python -m linkedin_mcp setup"
fi
if [ -f "$LEGACY_ENV_FILE" ] && grep -qE '^LINKEDIN_CLIENT_SECRET=.+' "$LEGACY_ENV_FILE"; then
    echo "⚠️  Found $LEGACY_ENV_FILE with a client secret, but the server reads mcp/.env, not the repo root."
    echo "    Move it to the keychain with: python -m linkedin_mcp setup   (then delete the root .env)"
fi
if [ -f "$ENV_FILE" ] && grep -qE '^LINKEDIN_CLIENT_SECRET=.+' "$ENV_FILE"; then
    echo "ℹ️  Found $ENV_FILE — the server reads it as a fallback; run the setup"
    echo "   wizard below to move the credentials into the keychain, then delete the file."
fi
if [ -t 0 ]; then
    echo "▶ Storing LinkedIn app credentials in the OS keychain..."
    if ! uv run python -m linkedin_mcp setup; then
        echo "⚠️  Could not store the credentials in an OS keychain (no usable backend, or"
        echo "   the wizard was cancelled). The server is registered; use the fallback:"
        echo "   export LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET in the environment that"
        echo "   launches Claude, or keep a 'chmod 600' .env next to mcp/server.py."
    fi
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
