# LinkedIn MCP — Architecture & Development Guide

## Overview

This is a Model Context Protocol (MCP) server that exposes LinkedIn functionality to Claude. It operates in two tiers:

| Tier | Requires | Capabilities |
|------|----------|-------------|
| **OAUTH** | Valid access token | get_profile (basic), create_post, get_posts, delete_post |
| **VOYAGER** | Token + browser session | All OAUTH tools + get_full_profile, get_notifications, get_conversations, get_recent_activity, update_headline, refresh_web_session |

---

## Key Architecture Decisions

### 1. The login happens inside a Playwright window on the per-alias profile

**Decision:** `authenticate` opens a *headed* Playwright Chromium window using the alias's persistent profile (`~/.linkedin_mcp_browser_<alias>/`) and navigates it to LinkedIn's authorization page. A local HTTP callback server at `localhost:8919` still receives the OAuth redirect. After the user approves, the Voyager cookies (`li_at`, `JSESSIONID`) are read straight from that profile.

**Why:** VoyagerClient reuses the exact same profile headlessly, so the cookies match the browser fingerprint LinkedIn saw at login (LinkedIn's Cloudflare protection rejects cookies moved into a different fingerprint). It also removes the two things that kept breaking session capture: reading Chrome's encrypted cookie store with `browser_cookie3`, and the macOS Keychain access that requires for *Chrome Safe Storage* (the "Unable to get key for cookie decryption" failure).

**Firewall guard:** before opening the window, `auth.probe_playwright_network()` loads linkedin.com headlessly. If the bundled Chromium cannot reach the network (macOS Application Firewall, Little Snitch, `ERR_CONNECTION_REFUSED`), the flow falls back to decision 2 automatically. The fallback happens *only* when the user never got to interact: a timeout, a closed window, or a denied consent is reported as an error rather than opening a second browser.

**Override:** `LINKEDIN_AUTH_MODE=auto|playwright|browser` (default `auto`). `LINKEDIN_AUTH_TIMEOUT` (default 300 s) bounds how long the window waits for the user; `LINKEDIN_PROBE_TIMEOUT_MS` (default 15000) bounds the probe and page loads.

**Threading:** all Playwright helpers in `auth.py` use the *sync* API and run on a worker thread (`asyncio.to_thread` from async tools, `auth._run_in_thread` from sync tools), because Playwright's sync API refuses to run on the server's asyncio thread.

### 2. Fallback: system browser + Chrome cookie-store capture

When the probe fails or Playwright is not installed, the OAuth URL opens in the system browser (Chrome preferred) and, after token exchange, `auth._capture_chrome_linkedin_cookies()` reads `li_at` / `JSESSIONID` from Chrome's cookie store via `browser_cookie3`, then seeds a headless profile with them (`auth._init_headless_profile`). This path needs Keychain access to Chrome Safe Storage on macOS; if that is refused the user is pointed at `refresh_web_session` / `set_web_session`.

### 2a. The profile is the source of truth for the session

- `server._get_voyager_client()` recovers a missing web session from the profile automatically (`auth.harvest_session_from_profile`) and persists it, so a login that happened inside the window is never lost.
- The `refresh_web_session` tool re-reads the profile on demand (JSESSIONID rotation, cleared keychain entry). `scripts/recover_voyager_session.py` does the same without the server.
- `VoyagerClient._ensure_context()` injects the stored cookies **only when the profile has no `li_at` of its own**; the login-redirect retry in `_browser_request()` re-injects if the profile's session turns out to be stale.
- Chromium locks a profile directory. Anything that opens it (`authenticate`, `refresh_web_session`, `set_web_session` validation, recovery) first calls `_invalidate_voyager(alias)` to close the live singleton.

### 3. Credentials are stored in the OS keychain

All secrets (client ID/secret, access tokens, web session cookies) are stored in the OS keychain via `keyring`. Files in `~/.linkedin_mcp_*.json` are a fallback for systems without a keychain backend.

The `keychain → file` fallback is intentional and tested. Never store credentials in `.env` for production use.

### 4. VoyagerClient is a per-alias singleton

`server.py` maintains `_voyager_singletons: dict[alias, VoyagerClient]`. The singleton is invalidated and recreated only when the session cookies change. This avoids launching a new Playwright browser context for every tool call.

---

## Module Structure

```
mcp/
├── auth.py           # OAuth flow, credential storage (keyring + file fallback)
├── client.py         # LinkedInClient (REST API) + VoyagerClient (Playwright)
├── server.py         # MCP server, tool definitions, client lifecycle
├── cache.py          # Simple in-memory TTL cache for VoyagerClient responses
├── log_config.py     # Logging setup (rotating file at ~/.linkedin_mcp.log)
├── linkedin_mcp/
│   ├── __init__.py
│   └── __main__.py   # CLI entry point: `python -m linkedin_mcp setup`
└── tests/
    ├── conftest.py              # File isolation fixtures
    ├── test_auth_csrf.py        # OAuth CSRF state validation
    ├── test_auth_flow.py        # OAuth flow: strategy selection, Playwright login, harvest, callback server
    ├── test_cache.py            # SimpleCache TTL logic
    ├── test_client_version.py   # LinkedIn-Version header
    ├── test_connection_pool.py  # httpx connection reuse
    ├── test_keyring.py          # Keychain + file fallback for all credential types
    ├── test_playwright_pool.py  # VoyagerClient singleton + Playwright lifecycle
    ├── test_retry.py            # HTTP 429/503 retry with backoff
    ├── test_session_recovery.py # Session recovery from the profile, refresh_web_session
    └── test_users.py            # User registry (alias management)
```

**Note:** Files at `mcp/*.py` use bare imports (`from auth import ...`). This works because the server is launched from within the `mcp/` directory, which puts it on `sys.path`. The `linkedin_mcp` package adds `mcp/` to `sys.path` via `__main__.py` for the CLI.

---

## Logs

All tool calls are logged to `~/.linkedin_mcp.log` with:
- Timestamp (ISO 8601)
- Log level
- Logger name (e.g., `linkedin_mcp.server`, `linkedin_mcp.auth`, `linkedin_mcp.client`)
- Message

```bash
# Live tail during development/testing
tail -f ~/.linkedin_mcp.log

# Filter for tool calls only
grep "TOOL " ~/.linkedin_mcp.log

# Filter for errors
grep "ERROR\|FAIL" ~/.linkedin_mcp.log
```

Environment variables:
- `LINKEDIN_MCP_LOG` — override log file path (default: `~/.linkedin_mcp.log`)
- `LINKEDIN_MCP_LOG_LEVEL` — `DEBUG`, `INFO` (default), `WARNING`, `ERROR`
- `LINKEDIN_MCP_DEBUG=1` — also log to stderr

---

## Running Tests

```bash
cd mcp
.venv/bin/python -m pytest tests/ -v
```

Tests never touch real credentials. The `_isolate_files` autouse fixture in `conftest.py` redirects all file paths to `tmp_path`.

---

## Troubleshooting

Run the diagnostics script to check all components:

```bash
python scripts/diagnose.py
```

This checks:
1. Python version
2. Required packages (httpx, mcp, keyring, playwright, browser_cookie3)
3. Chrome availability
4. Playwright Chromium launch **and whether it can reach linkedin.com** (which decides the auth path)
5. App credentials (keychain / env vars)
6. Auth state for all registered aliases
7. Log file location

### Common Issues

**`authenticate` opens browser but times out**
- The local callback server at `localhost:8919` must be reachable from the browser
- Check that no other process is using port 8919: `lsof -i :8919`
- LinkedIn may have changed the OAuth redirect flow; check `~/.linkedin_mcp.log` for details

**`authenticate` says the web session was not captured**
- If a Playwright window opened and you logged in: run `refresh_web_session` (reads the profile again).
- If no window opened: `python scripts/diagnose.py` shows whether Playwright Chromium can reach linkedin.com. Install it with `playwright install chromium`, or check the firewall. `LINKEDIN_AUTH_MODE=playwright` forces the window and surfaces the launch error.
- On the system-browser fallback, "Unable to get key for cookie decryption" means macOS refused Keychain access to Chrome Safe Storage. Prefer fixing the Playwright path; `set_web_session` is the last resort.

**Voyager tools fail with "No persistent browser profile"**
- Re-run `authenticate` to create the Playwright profile
- Or check that `~/.linkedin_mcp_browser_<alias>/` exists and is non-empty

**Token expired**
- Run `authenticate('<alias>')` again; LinkedIn tokens last ~60 days

**403 on `update_headline` or `get_posts`**
- The standard LinkedIn Consumer API does not grant `rw_me` or `r_member_social` scopes
- Voyager (web session) handles these without partner-level OAuth scopes

---

## Adding New Tools

1. Implement the business logic in `client.py` (LinkedInClient or VoyagerClient method)
2. Add the MCP tool in `server.py` using `@mcp.tool()`, wrap with `_tool_log()`
3. Add tests in `tests/`

Pattern for a Voyager-only tool:
```python
@mcp.tool()
def my_voyager_tool(...) -> str:
    with _tool_log("my_voyager_tool", param=value):
        try:
            voyager = _get_voyager_client()
            if not voyager:
                return "❌ Web session required. Run `authenticate` first."
            result = voyager.my_method(...)
            return json.dumps(result, indent=2)
        except Exception as exc:
            return _format_error(exc)
```
