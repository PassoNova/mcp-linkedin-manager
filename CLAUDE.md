# LinkedIn MCP — Architecture & Development Guide

## Overview

This is a Model Context Protocol (MCP) server that exposes LinkedIn functionality to Claude. It operates in two tiers:

| Tier | Requires | Capabilities |
|------|----------|-------------|
| **OAUTH** | Valid access token | get_profile (basic), create_post, get_posts, delete_post |
| **VOYAGER** | Token + browser session | All OAUTH tools + get_full_profile, get_notifications, get_conversations, get_recent_activity, update_headline |

---

## Key Architecture Decisions

### 1. OAuth uses system browser — never Playwright

**Decision:** The OAuth flow always opens the system browser (Chrome preferred) with a local HTTP callback server at `localhost:8919`. Playwright is never used for the auth dance.

**Why:** Playwright's bundled Chromium runs as a subprocess. On macOS, the Application Firewall or security tools (e.g., Little Snitch) can block the Playwright binary from making outbound network connections, causing `ERR_CONNECTION_REFUSED` even though the same URLs are reachable from Chrome. Since the user must interact with the browser anyway (to log in), using the system browser is both more reliable and more natural.

**What Playwright IS used for:**
- Optional headless profile initialization after OAuth (injecting cookies, best-effort)
- VoyagerClient: all web scraping operations (these use an already-authenticated session, so LinkedIn's fingerprinting is satisfied by the valid cookies)

### 2. Voyager session is captured automatically from Chrome cookies

After the OAuth token is obtained, `auth._capture_chrome_linkedin_cookies()` reads `li_at` and `JSESSIONID` from Chrome's cookie store using `browser_cookie3`. This happens automatically when Chrome was used for the OAuth flow.

If Chrome was not used (e.g., default browser is Firefox), the user must run `set_web_session` manually.

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
    ├── test_auth_flow.py        # OAuth flow: system browser path, cookie capture
    ├── test_cache.py            # SimpleCache TTL logic
    ├── test_client_version.py   # LinkedIn-Version header
    ├── test_connection_pool.py  # httpx connection reuse
    ├── test_keyring.py          # Keychain + file fallback for all credential types
    ├── test_playwright_pool.py  # VoyagerClient singleton + Playwright lifecycle
    ├── test_retry.py            # HTTP 429/503 retry with backoff
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
4. Playwright Chromium launch
5. App credentials (keychain / env vars)
6. Auth state for all registered aliases
7. Log file location

### Common Issues

**`authenticate` opens browser but times out**
- The local callback server at `localhost:8919` must be reachable from the browser
- Check that no other process is using port 8919: `lsof -i :8919`
- LinkedIn may have changed the OAuth redirect flow; check `~/.linkedin_mcp.log` for details

**`authenticate` returns "Chrome not found"**
- Install Chrome at `/Applications/Google Chrome.app`
- Or set the web session manually after OAuth: `set_web_session(li_at=..., jsessionid=...)`

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
