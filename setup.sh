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

# ── 5. Read credentials ───────────────────────────────────────────────────────
echo ""
# Prefer ~/.linkedin_mcp.env (plugin standard location)
if [ -f "$HOME/.linkedin_mcp.env" ]; then
    ENV_FILE="$HOME/.linkedin_mcp.env"
    echo "✅ Using credentials from ~/.linkedin_mcp.env"
elif [ -f "$PROJECT_DIR/.env" ]; then
    ENV_FILE="$PROJECT_DIR/.env"
    echo "✅ Using credentials from .env"
else
    echo "⚠️  No credentials file found."
    echo "   Create ~/.linkedin_mcp.env with:"
    echo "     LINKEDIN_CLIENT_ID=your_id"
    echo "     LINKEDIN_CLIENT_SECRET=your_secret"
    echo "   Then re-run this script."
    exit 1
fi

CLIENT_ID=$(grep -E '^LINKEDIN_CLIENT_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d ' "')
CLIENT_SECRET=$(grep -E '^LINKEDIN_CLIENT_SECRET=' "$ENV_FILE" | cut -d= -f2- | tr -d ' "')

if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "your_client_id_here" ]; then
    echo "❌ LINKEDIN_CLIENT_ID is not set. Please fill it in and re-run."
    exit 1
fi
if [ -z "$CLIENT_SECRET" ] || [ "$CLIENT_SECRET" = "your_client_secret_here" ]; then
    echo "❌ LINKEDIN_CLIENT_SECRET is not set. Please fill it in and re-run."
    exit 1
fi

# ── 6. Register with Claude Code CLI ─────────────────────────────────────────
VENV_PYTHON="$MCP_DIR/.venv/bin/python"

echo ""
echo "▶ Registering linkedin-manager with Claude Code CLI..."
claude mcp remove linkedin-manager 2>/dev/null && echo "   (removed previous registration)" || true

claude mcp add linkedin-manager \
    --scope user \
    -e LINKEDIN_CLIENT_ID="$CLIENT_ID" \
    -e LINKEDIN_CLIENT_SECRET="$CLIENT_SECRET" \
    -- "$VENV_PYTHON" "$MCP_DIR/server.py"

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
