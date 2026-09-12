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
# Private (0700) scratch directory so no other local user can pre-create or
# symlink the paths curl writes to.
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/linkedin-mcp-XXXXXX")"
TMP_PLUGIN="$TMP_DIR/$ASSET"
TMP_SUM="$TMP_DIR/$ASSET.sha256"
trap 'rm -rf "$TMP_DIR"' EXIT

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
# new, empty, or a previous install made by this script: it must carry the
# $MARKER file this script writes after extraction — a regular file whose
# entire content is the single line "$MARKER_PREFIX <release-tag>" — AND a
# regular-file server entry point mcp/server.py. Nothing else is accepted;
# installs that predate the marker must be removed by hand once.

MARKER=".linkedin-mcp-install"
MARKER_PREFIX="linkedin-mcp.plugin installed-by scripts/install.sh"
INSTALL_DIR="${INSTALL_DIR%/}"
[ -n "$INSTALL_DIR" ] || INSTALL_DIR="/"
case "/$INSTALL_DIR/" in
    */../*|*/./*) err "Refusing INSTALL_DIR '$INSTALL_DIR': '.' or '..' path components are not allowed. Pass a plain absolute or ~-relative path." ;;
esac
# Canonicalise even when the target does not exist yet: resolve the nearest
# existing parent and re-append the final component, so the guards below
# see the real location.
if [ -d "$INSTALL_DIR" ]; then
    RESOLVED_DIR="$(cd "$INSTALL_DIR" && pwd -P)"
else
    PARENT_DIR="$(dirname "$INSTALL_DIR")"
    [ -d "$PARENT_DIR" ] || err "Parent directory '$PARENT_DIR' does not exist. Create it first."
    RESOLVED_DIR="$(cd "$PARENT_DIR" && pwd -P)/$(basename "$INSTALL_DIR")"
fi
HOME_DIR="$(cd "$HOME" && pwd -P)"
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P || true)"

case "$RESOLVED_DIR" in
    /|"$HOME_DIR")
        err "Refusing to install into '$RESOLVED_DIR' — the installer wipes the target directory. Pass a dedicated directory, e.g. ~/linkedin-mcp."
        ;;
esac
if [ -n "$SCRIPT_ROOT" ] && [ "$RESOLVED_DIR" = "$SCRIPT_ROOT" ]; then
    err "Refusing to install over the directory this script lives in ('$RESOLVED_DIR')."
fi
if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    err "'$INSTALL_DIR' exists and is not a directory."
fi
if [ -d "$INSTALL_DIR" ]; then
    # Fail closed: an unreadable directory is not an empty one.
    if ! LISTING="$(ls -A "$INSTALL_DIR" 2>/dev/null)"; then
        err "Cannot read '$INSTALL_DIR'. Refusing to touch it."
    fi
    if [ -n "$LISTING" ]; then
        if [ -e "$INSTALL_DIR/.git" ]; then
            err "'$INSTALL_DIR' is a git checkout. Refusing to delete it — install into a dedicated directory instead."
        fi
        if [ -f "$INSTALL_DIR/$MARKER" ] && [ ! -L "$INSTALL_DIR/$MARKER" ] \
           && [ -f "$INSTALL_DIR/mcp/server.py" ] && [ ! -L "$INSTALL_DIR/mcp/server.py" ] \
           && [ "$(wc -l < "$INSTALL_DIR/$MARKER")" -eq 1 ] \
           && grep -Exq "$MARKER_PREFIX v?[0-9]+\.[0-9]+\.[0-9]+" "$INSTALL_DIR/$MARKER"; then
            info "Found previous linkedin-mcp install ($(awk '{print $NF}' "$INSTALL_DIR/$MARKER")) — it will be replaced."
        elif [ -f "$INSTALL_DIR/mcp/server.py" ]; then
            err "'$INSTALL_DIR' looks like a linkedin-mcp install made before installs were marked. Refusing to delete it automatically — check its contents, then remove it yourself (rm -rf '$INSTALL_DIR') and re-run."
        else
            err "'$INSTALL_DIR' is not empty and is not a previous linkedin-mcp install. Refusing to delete it — choose another directory or clear it yourself."
        fi
    fi
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

# Only a confirmed 404 (asset not published) skips verification; any other
# failure (network, TLS, 5xx) aborts so a flaky fetch cannot downgrade the check.

SUM_STATUS="$(curl -sSL -o "$TMP_SUM" -w '%{http_code}' "${DOWNLOAD_URL}.sha256" 2>/dev/null || printf '000')"
case "$SUM_STATUS" in
    200)
        EXPECTED="$(awk 'NR==1{print tolower($1)}' "$TMP_SUM" | tr -d '\r')"
        [[ "$EXPECTED" =~ ^[0-9a-f]{64}$ ]] || err "Published checksum file is malformed; refusing to install."
        ACTUAL="$(sha256_of "$TMP_PLUGIN")" || err "Neither 'sha256sum' nor 'shasum' is available; cannot verify the download."
        [ "$ACTUAL" = "$EXPECTED" ] \
            || err "SHA256 mismatch for ${ASSET} (${VERSION}): download is corrupt or tampered with. Aborting."
        ok "SHA256 verified"
        ;;
    404)
        warn "No ${ASSET}.sha256 published for ${VERSION}; skipping checksum verification."
        ;;
    *)
        err "Could not fetch ${ASSET}.sha256 (HTTP ${SUM_STATUS}); refusing to install unverified. Retry, or pin a version with LINKEDIN_MCP_VERSION."
        ;;
esac

# ── Install ────────────────────────────────────────────────────────────────────

info "Installing to $INSTALL_DIR…"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
unzip -q "$TMP_PLUGIN" -d "$INSTALL_DIR"
printf '%s %s\n' "$MARKER_PREFIX" "$VERSION" > "$INSTALL_DIR/$MARKER"
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
