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
1. A short headless probe checks that Playwright's Chromium can reach linkedin.com
2. A headed Playwright Chromium window opens on LinkedIn's authorization page,
   using the per-alias profile at `~/.linkedin_mcp_browser_<alias>/` — the same
   profile the Voyager client reuses headlessly, so cookies and fingerprint match
3. A local HTTP callback server on port 8919 receives the OAuth redirect
4. The user logs in and approves → LinkedIn redirects with an auth code
5. The code is exchanged for an access token (valid ~60 days), saved to the OS
   keychain as `oauth_token:<alias>` (fallback: `~/.linkedin_mcp_token_<alias>.json`)
6. Browser session cookies (`li_at`, `JSESSIONID`) are read from the Playwright
   profile and saved as `session:<alias>` in the keychain → Voyager tier unlocked
7. The alias is registered in `~/.linkedin_mcp_users.json` and set as active

If Playwright is not installed or the probe fails (firewall), the flow falls
back to the system browser (Chrome preferred) + the same callback server, then
tries to read the cookies from Chrome's cookie store; that needs macOS Keychain
access to Chrome Safe Storage and may fail. `LINKEDIN_AUTH_MODE` forces a path.
A session that exists in the profile but not in the keychain can always be
recovered with `refresh_web_session`.

## Confirm success

After `authenticate` returns:
1. Call `check_auth` — check `active_user` (should match the alias) and `tier`
2. Call `get_profile` to verify the token works
3. Present the user's name and headline in a friendly confirmation message
4. If `tier` is `VOYAGER`: mention full profile sections, notifications, and
   messaging are now accessible
5. If `tier` is `OAUTH`: call `refresh_web_session`; if it still fails, mention
   Voyager was not captured and offer `set_web_session`

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
