# LinkedIn MCP — OAuth Authentication Flow

Follow these steps precisely.

## Pre-flight check

Call `check_auth` and `list_users` in parallel.

- `check_auth` returns `"authenticated": true` and `"expired": false` → tell the
  user they are already authenticated as `active_user` and ask if they want to
  re-authenticate or add a different account.
- expired or not authenticated → proceed immediately.

## Determine the alias

Ask the user for a short alias for the account being authenticated:
- Allowed: letters, digits, hyphens, underscores (max 32 chars)
- Examples: `work`, `personal`, `client-acme`
- Re-authenticating an existing alias replaces its tokens; other aliases are unaffected

## Run the OAuth flow

Call `authenticate(alias)`.

What happens behind the scenes:
1. The tool opens the user's default browser to LinkedIn's authorization page
2. A Playwright-managed Chromium window handles the flow; a per-alias browser
   profile is stored at `~/.linkedin_mcp_browser_<alias>/` for consistent identity
3. The OAuth callback is intercepted inside Playwright — no separate local server
4. The user approves → LinkedIn redirects with an auth code
5. The code is exchanged for an access token (valid ~60 days), saved to the OS
   keychain as `oauth_token:<alias>` (fallback: `~/.linkedin_mcp_token_<alias>.json`)
6. Browser session cookies (`li_at`, `JSESSIONID`) are harvested from the Playwright
   context and saved as `session:<alias>` in the keychain → Voyager tier unlocked
7. The alias is registered in `~/.linkedin_mcp_users.json` and set as active

If Playwright is unavailable, the flow falls back to `webbrowser.open()` + a
local HTTP server on port 8919; Voyager cookies are not captured in this path
and must be set manually with `set_web_session`.

## Confirm success

After `authenticate` returns:
1. Call `check_auth` — check `active_user` (should match the alias) and `tier`
2. Call `get_profile` to verify the token works
3. Present the user's name and headline in a friendly confirmation message
4. If `tier` is `VOYAGER`: mention full profile sections, notifications, and
   messaging are now accessible
5. If `tier` is `OAUTH`: mention Voyager was not captured and offer `set_web_session`

## Multi-account management

After authentication, the user can:
- `list_users` — see all registered aliases with tier and auth status
- `switch_user(alias)` — change the active account; all tools switch immediately
- `logout(alias)` — remove one account's credentials (defaults to active)
- `authenticate(alias)` again — re-authenticates and replaces tokens for that alias

## Handle errors

| Error message | What to tell the user |
|---|---|
| "LinkedIn app credentials not found" | Run `python -m linkedin_mcp setup` in the terminal first |
| "Invalid alias" | Alias must be letters, digits, hyphens, or underscores (max 32 chars) |
| "The requested permission scope is not valid" | Go to LinkedIn Developer App → Products tab and add both "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn" |
| "Bummer" / redirect to localhost | Verify `http://localhost:8919/callback` is listed under Auth → Authorized redirect URLs |
| Port 8919 in use | Run `lsof -i :8919 \| grep LISTEN` in the terminal and kill the process, then retry |
| Timeout (no browser opened) | Check that a default browser is configured on the system |
