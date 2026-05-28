#!/usr/bin/env bash
# install.sh — download the latest linkedin-mcp release and register it with Claude Code
#
# Usage:
#   ./scripts/install.sh                        # installs to ~/linkedin-mcp
#   ./scripts/install.sh /path/to/install/dir   # installs to a custom directory
#   LINKEDIN_MCP_VERSION=v1.0.11 ./scripts/install.sh  # pin a specific version
#
# What it does:
#   1. Downloads the .plugin zip from the latest (or pinned) GitHub release
#   2. Extracts it to the install directory
#   3. Creates a virtualenv and runs `uv sync` to install dependencies
#   4. Registers (or updates) the `linkedin-manager` MCP server in Claude's user config

set -euo pipefail

REPO="PassoNova/mcp-linkedin-manager"
INSTALL_DIR="${1:-$HOME/linkedin-mcp}"
VERSION="${LINKEDIN_MCP_VERSION:-}"
MCP_NAME="linkedin-manager"
TMP_PLUGIN="$(mktemp /tmp/linkedin-mcp-XXXXXX.plugin)"

# ── Helpers ────────────────────────────────────────────────────────────────────

info()  { printf '\033[1;34m→\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

require() {
    command -v "$1" &>/dev/null || err "'$1' is required but not found. Install it and retry."
}

# ── Preflight ──────────────────────────────────────────────────────────────────

require curl
require unzip
require uv
require claude

# ── Resolve version ────────────────────────────────────────────────────────────

if [ -z "$VERSION" ]; then
    info "Fetching latest release from GitHub…"
    VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
        | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
    [ -n "$VERSION" ] || err "Could not determine latest release version."
fi
ok "Version: $VERSION"

# ── Download ───────────────────────────────────────────────────────────────────

DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/linkedin-mcp.plugin"
info "Downloading ${VERSION} plugin…"
curl -fsSL "$DOWNLOAD_URL" -o "$TMP_PLUGIN" \
    || err "Download failed. Check the version tag and your internet connection."
ok "Downloaded to $TMP_PLUGIN"

# ── Install ────────────────────────────────────────────────────────────────────

info "Installing to $INSTALL_DIR…"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
unzip -q "$TMP_PLUGIN" -d "$INSTALL_DIR"
rm -f "$TMP_PLUGIN"
ok "Extracted plugin files"

info "Installing Python dependencies…"
(cd "$INSTALL_DIR/mcp" && uv sync --quiet)
ok "Dependencies installed"

# ── Register MCP server ────────────────────────────────────────────────────────

PYTHON="$INSTALL_DIR/mcp/.venv/bin/python"
SERVER="$INSTALL_DIR/mcp/server.py"

[ -f "$PYTHON" ] || err "Python venv not found at $PYTHON"
[ -f "$SERVER" ] || err "server.py not found at $SERVER"

info "Registering '$MCP_NAME' MCP server with Claude…"

# Remove existing registration (any scope) so we can replace it cleanly.
if claude mcp get "$MCP_NAME" &>/dev/null 2>&1; then
    SCOPE=$(claude mcp get "$MCP_NAME" 2>/dev/null | awk '/Scope:/{print $NF}' | tr '[:upper:]' '[:lower:]' | tr -d ')')
    case "$SCOPE" in
        user)    claude mcp remove "$MCP_NAME" -s user    &>/dev/null ;;
        project) claude mcp remove "$MCP_NAME" -s project &>/dev/null ;;
        local)   claude mcp remove "$MCP_NAME" -s local   &>/dev/null ;;
    esac
fi

# Re-add pointing at the freshly installed location.
CREDS_ARGS=()
if [ -n "${LINKEDIN_CLIENT_ID:-}" ] && [ -n "${LINKEDIN_CLIENT_SECRET:-}" ]; then
    CREDS_ARGS=(
        --env "LINKEDIN_CLIENT_ID=${LINKEDIN_CLIENT_ID}"
        --env "LINKEDIN_CLIENT_SECRET=${LINKEDIN_CLIENT_SECRET}"
    )
fi

claude mcp add "$MCP_NAME" -s user "${CREDS_ARGS[@]}" -- "$PYTHON" "$SERVER"
ok "Registered '$MCP_NAME' → $SERVER"

# ── Done ───────────────────────────────────────────────────────────────────────

printf '\n'
ok "linkedin-mcp ${VERSION} installed and registered."
printf '   Install dir : %s\n' "$INSTALL_DIR"
printf '   Next step   : restart Claude Code, then run authenticate('"'"'default'"'"') from Claude.\n'
printf '\n'
printf 'To pass app credentials at install time:\n'
printf '   LINKEDIN_CLIENT_ID=<id> LINKEDIN_CLIENT_SECRET=<secret> ./scripts/install.sh\n'
printf 'Or run the setup wizard after restart:\n'
printf '   python -m linkedin_mcp setup\n'
