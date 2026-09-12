# Runbook: Authentication & Token Lifecycle

**Audience:** Users who have completed first-time setup and need to manage tokens, sessions, or multiple accounts.

---

## How authentication works

The server uses a two-layer auth model:

### Layer 1 — OAuth access token (OAUTH tier)

Obtained via `authenticate(alias)`. Valid for approximately 60 days. Stored in the OS keychain under `oauth_token:<alias>`. Grants:
- `openid`, `profile`, `email` — read identity
- `w_member_social` — create and delete posts

The token is sent as `Authorization: Bearer <token>` on every LinkedIn REST API call.

### Layer 2 — Browser session cookies (VOYAGER tier)

Captured automatically during `authenticate`: the login happens in a Playwright browser window on the per-alias profile (`~/.linkedin_mcp_browser_<alias>/`) and the cookies are read from that profile once you approve the app. If Playwright's Chromium cannot reach the network, the flow falls back to your system browser and Chrome's cookie store. Stored under `session:<alias>`. Consists of two cookies:
- `li_at` — LinkedIn session token (primary auth for Voyager API)
- `JSESSIONID` — CSRF protection token (required alongside `li_at`)

The Voyager tier enables: `get_full_profile`, `get_notifications`, `get_conversations`, `get_recent_activity`, `update_headline` (without partner-program approval).

### Tier check

Call `check_auth` at any time to see the active account's current state:
```
✅ Authenticated
  active_user: work
  tier: VOYAGER           ← both layers active
  token_expires: 2026-08-24
  scopes: email, openid, profile, w_member_social
  keychain: credentials ✓  session ✓
```

---

## Refreshing the web session

The browser profile keeps the LinkedIn login. If `check_auth` shows `tier: OAUTH` although you logged in inside the window, or a Voyager tool starts failing after LinkedIn rotated `JSESSIONID`:

```
"Refresh my LinkedIn web session"
```

This runs `refresh_web_session`, which re-reads the cookies from the profile headlessly — no browser interaction. The server also does this on its own the first time a Voyager tool is used with a profile but no stored session. Without the server running, `python scripts/recover_voyager_session.py <alias>` does the same.

Force a specific login path with `LINKEDIN_AUTH_MODE=playwright` (always the window) or `LINKEDIN_AUTH_MODE=browser` (always the system browser) in the server's environment.

---

## Re-authenticating (token expired)

LinkedIn tokens expire after ~60 days. When `check_auth` shows `token_expires` in the past or tools return 401:

```
"Authenticate with LinkedIn using the alias 'work'"
```

This re-runs the full OAuth flow and updates the token in the keychain. The existing browser session is preserved unless the cookies have also expired.

---

## Multi-account management

### Register a second account

```
"Authenticate with LinkedIn using the alias 'personal'"
```

Each alias has its own token and session stored separately. App credentials (Client ID + Secret) are shared across all aliases.

### Switch the active account

```
switch_user("personal")
```

All tools (`get_profile`, `create_post`, etc.) now operate on the `personal` alias.

### List all accounts

```
list_users
```

Returns a table of every registered alias with its tier and token expiry:
```
alias     tier      expires
work      VOYAGER   2026-08-24
personal  OAUTH     2026-07-01
```

### Remove an account

```
logout("personal")
```

Removes the OAuth token and web session for that alias from the keychain **and deletes the alias's persistent browser profile** (`~/.linkedin_mcp_browser_<alias>/`, which held the logged-in LinkedIn session). The alias is de-registered. App credentials are not affected.

---

## Manually setting the browser session (Voyager tier)

If Chrome was not used for OAuth (e.g. the default browser is Firefox), cookies are not captured automatically. To unlock Voyager tier manually:

1. Open https://www.linkedin.com in Chrome and log in
2. Open DevTools → Application → Cookies → `https://www.linkedin.com`
3. Copy the values for `li_at` and `JSESSIONID`
4. Tell Claude:

   > "Set my LinkedIn web session: li_at is <value>, jsessionid is <value>"

Claude calls `set_web_session(li_at=..., jsessionid=...)` for the active alias.

To clear the session without revoking the OAuth token:
```
clear_web_session()
```

---

## Clearing app credentials

If you need to replace the Client ID and Secret (e.g. rotating credentials):

```
clear_credentials
```

Then re-run `uv run python -m linkedin_mcp setup` with the new values.

> **Note:** This only removes the shared app credentials, not per-user tokens. All authenticated aliases continue to work until their tokens expire.

---

## Credential storage locations

| Item | Keychain key | File fallback |
|---|---|---|
| App Client ID + Secret | `linkedin-mcp / credentials` | none — `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` env vars or a `0600` `.env` (no file is ever written by the server) |
| Per-alias OAuth token | `linkedin-mcp / oauth_token:<alias>` | `~/.linkedin_mcp_token_<alias>.json` |
| Per-alias browser session | `linkedin-mcp / session:<alias>` | `~/.linkedin_mcp_session_<alias>.json` |
| Active alias pointer | `linkedin-mcp / active_user` | `~/.linkedin_mcp_active_user.json` |

On macOS, open **Keychain Access** and search for `linkedin-mcp` to inspect stored items. On Linux, use `secret-tool lookup service linkedin-mcp`.

---

## Diagnostics

```bash
# Full system check
python scripts/diagnose.py

# Live log (all tool calls, auth events, errors)
tail -f ~/.linkedin_mcp.log

# Filter for auth events only
grep "auth\|token\|session" ~/.linkedin_mcp.log

# Check what's in keychain (macOS)
security find-generic-password -s linkedin-mcp -l credentials -w
```
