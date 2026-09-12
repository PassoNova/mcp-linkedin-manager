#!/usr/bin/env bash
# install.sh — download the latest linkedin-mcp release and register it with Claude Code
#
# Usage:
#   ./scripts/install.sh                        # installs to ~/linkedin-mcp
#   ./scripts/install.sh /path/to/install/dir   # installs to a custom directory
#   LINKEDIN_MCP_VERSION=v1.0.11 ./scripts/install.sh  # pin a specific version
#
# What it does:
#   1. Downloads the .plugin zip from the latest (or pinned) GitHub release and
#      verifies its SHA256 against the published linkedin-mcp.plugin.sha256
#   2. Extracts it to the install directory (only a fresh or previous install dir)
#   3. Creates a virtualenv and runs `uv sync` to install dependencies
#   4. Registers (or updates) the `linkedin-manager` MCP server in Claude's user config
#
# App credentials are NOT taken from the environment or written into Claude's
# config. After installing, run `python -m linkedin_mcp setup` to store the
# Client ID / Secret in the OS keychain.

set -euo pipefail

REPO="PassoNova/mcp-linkedin-manager"
INSTALL_DIR="${1:-$HOME/linkedin-mcp}"
VERSION="${LINKEDIN_MCP_VERSION:-}"
MCP_NAME="linkedin-manager"
ASSET="linkedin-mcp.plugin"
TMP_PLUGIN="$(mktemp /tmp/linkedin-mcp-XXXXXX.plugin)"
TMP_SUM="${TMP_PLUGIN}.sha256"
trap 'rm -f "$TMP_PLUGIN" "$TMP_SUM"' EXIT

# ── Helpers ────────────────────────────────────────────────────────────────────

info()  { printf '\033[1;34m→\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

require() {
    command -v "$1" &>/dev/null || err "'$1' is required but not found. Install it and retry."
}

sha256_of() {
    if command -v sha256sum &>/dev/null; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum &>/dev/null; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        return 1
    fi
}

# ── Preflight ──────────────────────────────────────────────────────────────────

require curl
require unzip
require uv
require claude

if [ -n "${LINKEDIN_CLIENT_ID:-}" ] || [ -n "${LINKEDIN_CLIENT_SECRET:-}" ]; then
    warn "LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET in the environment are ignored:"
    warn "the installer no longer writes credentials into Claude's config."
    warn "Run 'python -m linkedin_mcp setup' after installing to store them in the OS keychain."
fi

# ── Validate install directory ─────────────────────────────────────────────────
# `rm -rf "$INSTALL_DIR"` below is only ever run against a directory that is
# new, empty, or a previous linkedin-mcp install (identified by mcp/server.py).

INSTALL_DIR="${INSTALL_DIR%/}"
[ -n "$INSTALL_DIR" ] || INSTALL_DIR="/"
RESOLVED_DIR="$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P || printf '%s' "$INSTALL_DIR")"
HOME_DIR="$(cd "$HOME" && pwd -P)"

case "$RESOLVED_DIR" in
    /|"$HOME_DIR")
        err "Refusing to install into '$RESOLVED_DIR' — the installer wipes the target directory. Pass a dedicated directory, e.g. ~/linkedin-mcp."
        ;;
esac
if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    err "'$INSTALL_DIR' exists and is not a directory."
fi
if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ] && [ ! -f "$INSTALL_DIR/mcp/server.py" ]; then
    err "'$INSTALL_DIR' is not empty and does not look like a previous linkedin-mcp install (no mcp/server.py). Refusing to delete it — choose another directory or clear it yourself."
fi

# ── Resolve version ────────────────────────────────────────────────────────────

if [ -z "$VERSION" ]; then
    info "Fetching latest release from GitHub…"
    VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
        | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
    [ -n "$VERSION" ] || err "Could not determine latest release version."
fi
ok "Version: $VERSION"

# ── Download ───────────────────────────────────────────────────────────────────

DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"
info "Downloading ${VERSION} plugin…"
curl -fsSL "$DOWNLOAD_URL" -o "$TMP_PLUGIN" \
    || err "Download failed. Check the version tag and your internet connection."
ok "Downloaded to $TMP_PLUGIN"

# ── Verify checksum ────────────────────────────────────────────────────────────
# Releases publish ${ASSET}.sha256 next to the archive. Verify it when present;
# older releases have none, in which case we warn and continue.

if curl -fsSL "${DOWNLOAD_URL}.sha256" -o "$TMP_SUM" 2>/dev/null; then
    EXPECTED="$(awk 'NR==1{print tolower($1)}' "$TMP_SUM" | tr -d '\r')"
    [[ "$EXPECTED" =~ ^[0-9a-f]{64}$ ]] || err "Published checksum file is malformed; refusing to install."
    ACTUAL="$(sha256_of "$TMP_PLUGIN")" || err "Neither 'sha256sum' nor 'shasum' is available; cannot verify the download."
    [ "$ACTUAL" = "$EXPECTED" ] \
        || err "SHA256 mismatch for ${ASSET} (${VERSION}): download is corrupt or tampered with. Aborting."
    ok "SHA256 verified"
else
    warn "No ${ASSET}.sha256 published for ${VERSION}; skipping checksum verification."
fi

# ── Install ────────────────────────────────────────────────────────────────────

info "Installing to $INSTALL_DIR…"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
unzip -q "$TMP_PLUGIN" -d "$INSTALL_DIR"
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

# Re-add pointing at the freshly installed location. No credentials are passed:
# the server reads them from the OS keychain (see `python -m linkedin_mcp setup`).
claude mcp add "$MCP_NAME" -s user -- "$PYTHON" "$SERVER"
ok "Registered '$MCP_NAME' → $SERVER"

# ── Done ───────────────────────────────────────────────────────────────────────

printf '\n'
ok "linkedin-mcp ${VERSION} installed and registered."
printf '   Install dir : %s\n' "$INSTALL_DIR"
printf '\n'
printf 'Next steps:\n'
printf '   1. Store your LinkedIn app credentials in the OS keychain:\n'
printf '        cd %s/mcp && uv run python -m linkedin_mcp setup\n' "$INSTALL_DIR"
printf '   2. Restart Claude Code, then run authenticate('"'"'default'"'"') from Claude.\n'
printf '\n'
