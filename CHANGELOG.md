# Changelog

All notable changes to the LinkedIn MCP Server are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Security
- **`.claude/settings.local.json` untracked.** The file had been committed since the first commit. It contained only local machine paths and pre-approved Claude Code command rules — never any credential values — but it is machine-specific and does not belong in a public repo. It is now ignored (`.claude/*.local.json`); history is unchanged.
- **Playwright profile directory is owner-only.** `~/.linkedin_mcp_browser_<alias>/` holds the live LinkedIn session cookie and was created with default permissions. It is now created `0700` and re-`chmod`ed on every open (login, headless init, `harvest_session_from_profile`).
- **Token / session fallback files are `0600` from creation.** They were opened with `open(path, "w")` and `chmod`ed afterwards, leaving a window at the default umask. They are now created via `os.open(..., 0o600)`.
- **Log file is `0600`.** `~/.linkedin_mcp.log` can carry aliases, profile paths and API error bodies; the rotating handler creates every file (including post-rollover ones) with `0600`.
- **`clear_web_session` now also deletes the per-alias browser profile.** Deleting only the stored cookies left a logged-in profile behind, from which the server transparently re-harvested the session on the next call — so "clear" did not actually switch Voyager off. The profile is removed first (a failure leaves the stored session untouched rather than a recoverable profile), the alias is re-validated and symlinked paths are refused before `rmtree`, the stored session's removal is verified before success is reported, and profile removal / Voyager singleton lifecycle are serialised with a lock. `logout` removes the browser profile too.
- **Token/session fallback files are tightened to `0600` when read**, not only when written — on every load, including keychain hits — so files left at `0644` by older versions are fixed on the next load; a file that cannot be tightened, or is a symlink, is refused. `logout` uses the same strict, verified deletion as `clear_web_session` and only deregisters the alias once the token and session are confirmed gone. A per-alias lifecycle generation makes a clear that lands while `authenticate` or `set_web_session` is still running win (the late save is discarded — and `authenticate` then persists neither token nor alias), profile recovery is refused for a cleared alias until a session is stored again on purpose, lifecycle locks are per alias, and a closed `VoyagerClient` never reopens the profile. Symlinks are refused for the profile directory, the fallback files and the log file (`O_NOFOLLOW` where available).
- **`setup.sh` installs with `uv sync`** instead of `uv pip install -e .`, so source installs honour `uv.lock` and the dependency floors.
- **OAuth CSRF check** compares the `state` parameter in constant time (`secrets.compare_digest`) and no longer echoes the expected value in the error message.
- **Installers no longer persist the client secret.** `scripts/install.sh` and `setup.sh` passed `LINKEDIN_CLIENT_SECRET` to `claude mcp add --env`, which stores it in plaintext in Claude's config. They now register the server without credentials and direct users to `python -m linkedin_mcp setup` (OS keychain).
- **`scripts/install.sh` hardening.** Verifies the downloaded archive against the release's `linkedin-mcp.plugin.sha256` (skips only on a confirmed 404 — an older release without one — and aborts on any other fetch failure), refuses to `rm -rf` `$HOME`, `/`, any git checkout, or a non-empty directory without the `.linkedin-mcp-install` marker it now writes, and cleans up its temp files. Releases now publish the `.sha256` file.
- **Dependencies upgraded** past known advisories: `mcp>=1.28.1,<2` (2.x renames `FastMCP`; that migration is separate), `cryptography>=50`, `starlette>=1.3.1`, `pydantic-settings>=2.14.2`, `python-multipart>=0.0.31` (`pip-audit`: 13 findings → 0).
- **Workflows:** `ci.yml` runs with `permissions: contents: read`, and the `test` jobs of the release workflows are `contents: read` too; `actions/checkout`, `actions/upload-artifact`, `astral-sh/setup-uv` and `softprops/action-gh-release` are pinned to full commit SHAs.
- **Voyager disclosure** added to `README.md` and the new `SECURITY.md`: Voyager is LinkedIn's unofficial internal API, using it may violate LinkedIn's terms and can lead to account restrictions, and it stays off unless a web session exists.

### Changed
- **Login moves into a Playwright window on the per-alias profile.** `authenticate` now opens LinkedIn's authorization page in a headed Playwright Chromium using `~/.linkedin_mcp_browser_<alias>/` and reads `li_at` / `JSESSIONID` straight from that profile after approval. The Voyager client reuses the same profile, so cookies and fingerprint always match. The system-browser + `browser_cookie3` path is now the fallback, used only when a headless probe shows Playwright's Chromium cannot reach linkedin.com (`auth.probe_playwright_network`). `LINKEDIN_AUTH_MODE=auto|playwright|browser`, `LINKEDIN_AUTH_TIMEOUT`, and `LINKEDIN_PROBE_TIMEOUT_MS` control the behaviour.
- `run_oauth_flow` returns an `OAuthResult` (`token_data`, `li_at`, `jsessionid`, `session_error`, `method`) instead of a 4-tuple, and runs all Playwright work on a worker thread (sync API).
- `VoyagerClient` no longer overwrites a session that already lives in the profile; stored cookies are injected only when the profile has no `li_at` (the login-redirect retry still re-injects).

### Added
- **`refresh_web_session` tool** — re-reads the LinkedIn session from the persistent profile with no browser interaction (JSESSIONID rotation, cleared keychain entry, login done inside the window but not stored).
- Automatic recovery: `_get_voyager_client()` harvests a missing web session from the profile and persists it before giving up.
- `scripts/diagnose.py` now checks whether Playwright's Chromium can reach linkedin.com and prints which auth path `authenticate` will take. `scripts/recover_voyager_session.py` uses the shared `harvest_session_from_profile`.

### Fixed
- **Voyager session never captured on macOS** — the previous design read Chrome's encrypted cookie store, which fails with "Unable to get key for cookie decryption" whenever the server process is refused Keychain access to Chrome Safe Storage. The Playwright-profile login does not touch Chrome or the Keychain.
- `authenticate` / `set_web_session` / session recovery now close the live Voyager singleton before opening the profile (Chromium locks the directory); the `set_web_session` validation client is closed after use.
- **`get_posts` 403 / `NO_VERSION` error** — migrated from deprecated `GET /v2/ugcPosts?q=authors` to the versioned `GET /rest/posts?q=author` endpoint. Added `REST_BASE = "https://api.linkedin.com/rest"` and pinned `LinkedIn-Version: 202506` header on all REST calls.
- **Voyager error leaked into `get_profile` headline** — `except Exception as ve: headline = f"Voyager error: {ve}"` replaced with `except Exception: pass` so the OAuth fallback path runs cleanly.
- **OAuth 403 shown as headline text** — `GET /v2/me` requires partner-level `r_liteprofile` scope unavailable on standard apps; catch now silently sets `headline = ""` instead of surfacing the raw exception.

---

## [1.0.6] — 2026-05-26

### Fixed
- Reliably capture `JSESSIONID` in addition to `li_at` after the OAuth callback, ensuring Voyager tier is unlocked automatically without a manual `set_web_session` call.

## [1.0.5] — 2026-05-26

### Fixed
- Explicitly open Google Chrome (not the system default browser) during OAuth so `browser_cookie3` can read `li_at` from the correct cookie store on macOS.

## [1.0.4] — 2026-05-26

### Added
- After OAuth completes, automatically read `li_at` from Chrome's on-disk cookie database via `browser_cookie3` and persist it to the OS keychain under `session:<alias>`.

## [1.0.3] — 2026-05-26

### Fixed
- Consolidated the CI auto-release into a single `release.yml` workflow triggered by `v*` tags, eliminating duplicate release runs.

## [1.0.2] — 2026-05-26

### Fixed
- Dispatch `release.yml` using a Personal Access Token instead of `GITHUB_TOKEN` to allow the workflow to trigger on auto-generated tags (GitHub blocks same-token recursive triggers).

## [1.0.1] — 2026-05-26

### Added
- **Auto-tagging**: pushing to `main` now automatically increments the patch version and creates a `v*` git tag, which triggers the release workflow.

### Fixed
- Release workflow plugin zip paths corrected so the `.claude-plugin/` structure is preserved inside the `.plugin` archive.

## [1.0.0] — 2026-05-26

### Added
- **Multi-account support** (`authenticate`, `switch_user`, `list_users`, `logout`) with per-alias tokens stored in the OS keychain under `oauth_token:<alias>`.
- **OS keychain storage** via `keyring`: credentials stored as `linkedin-mcp / credentials`, `linkedin-mcp / oauth_token:<alias>`, `linkedin-mcp / session:<alias>`. File fallback for systems without a keychain backend.
- **Capability tier system**: `BASE` (no auth) → `OAUTH` (access token) → `VOYAGER` (token + browser session cookies).
- **`get_api_capabilities`** — returns a structured description of what each tier can and cannot do so Claude can give accurate, tier-appropriate guidance.
- **`check_auth`** — returns active alias, token validity/expiry, scopes, tier, and keychain status in a single call.
- **`clear_credentials`** — removes shared app credentials (Client ID + Secret) from the keychain.
- **Skill documentation**: `linkedin-setup`, `linkedin-authenticate`, `linkedin-profile`, `linkedin-posts`, `linkedin-discover` skills shipped inside the `.plugin` archive.

---

## [0.x — Pre-release history]

| Version | Date | Highlights |
|---|---|---|
| pre-v1 #15 | 2026-05-28 | Structured logging (rotating `~/.linkedin_mcp.log`), system-browser OAuth path, VoyagerClient session reliability |
| pre-v1 #14 | 2026-05-28 | Reliable OAuth callback server; `scripts/recover.py` for broken sessions; structured error reporting |
| pre-v1 #13 | 2026-05-28 | Copy `README.md` into `mcp/` so `pyproject.toml` build succeeds inside the plugin zip |
| pre-v1 #12 | 2026-05-27 | Repo cleanup; `JSESSIONID` capture hardened |
| pre-v1 #9  | 2026-05-26 | Engaging OAuth callback landing page (HTML + CSS) instead of plain text |
| pre-v1 #2  | 2026-05-26 | **Voyager tier**: full profile read via Playwright + `li_at` browser session |
| pre-v1 #1  | 2026-05-26 | Initial LinkedIn MCP plugin — OAuth 2.0, `get_profile`, `create_post`, `get_posts`, `delete_post` |

[Unreleased]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.6...HEAD
[1.0.6]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/PassoNova/mcp-linkedin-manager/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/PassoNova/mcp-linkedin-manager/releases/tag/v1.0.0
