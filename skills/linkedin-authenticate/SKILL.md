---
name: linkedin-authenticate
description: >
  Authenticate or re-authenticate with LinkedIn via OAuth 2.0, or manage
  multiple accounts. Trigger with "authenticate with LinkedIn", "connect
  LinkedIn", "log in to LinkedIn", "LinkedIn token expired", "refresh LinkedIn
  auth", "switch LinkedIn account", "add another LinkedIn account", or when
  any LinkedIn tool returns a 401 error or "not authenticated" message.
---

## Pre-flight check

Call `check_auth` first.
- `"authenticated": true` and `"expired": false` → ask if they want to
  re-authenticate or switch to a different account
- expired or not authenticated → proceed immediately

Also call `list_users` to show which accounts are already registered.

## Determine the alias

Ask the user for a short alias for the account they want to authenticate:
- Examples: `work`, `personal`, `client-acme`
- Rules: letters, digits, hyphens, underscores only; max 32 characters
- If re-authenticating an existing alias, the old token is replaced

## Run the OAuth flow

Call `authenticate(alias)` with the chosen alias. Tell the user a browser
window will open and that they should log in and approve the app there. What
happens:
1. A Playwright browser window opens on LinkedIn's authorization page, using a
   per-alias profile (`~/.linkedin_mcp_browser_<alias>/`). If Playwright's
   Chromium cannot reach the network, the system browser is used instead.
2. A local callback server on port 8919 receives the OAuth redirect
3. User approves → token exchanged (valid ~60 days), saved to keychain as `oauth_token:<alias>`
4. Browser session cookies (`li_at`, `jsessionid`) are read from that browser
   profile → saved as `session:<alias>` → Voyager tier unlocked automatically
5. The alias is registered and set as the active account

The tool's reply names the path it used ("via Playwright login window" or
"via system browser + Chrome cookie store") and, if the session was not
captured, lists the recovery options. Waiting for the user to log in can take
a few minutes; do not call `authenticate` again while a window is open.

## Confirm success

Call `check_auth` (note `active_user` and `tier`), then `get_profile`.
- If `tier` is `VOYAGER`: full profile, notifications, and messaging are accessible
- If `tier` is `OAUTH` and the user logged in inside the window: call
  `refresh_web_session` first — it re-reads the session from the browser
  profile without any interaction. Only offer `set_web_session` if that fails.

## Switching accounts

Use `switch_user(alias)` to change the active account. All tools then operate
on the new alias until switched again. Use `list_users` to see all registered
accounts and their current tier.

## Handle errors

| Error | Fix |
|---|---|
| "LinkedIn app credentials not found" | Run `python -m linkedin_mcp setup` in the terminal first |
| "Invalid alias" | Use only letters, digits, hyphens, underscores (max 32 chars) |
| "The requested permission scope is not valid" | Add both Products in LinkedIn Developer App |
| "Bummer" / localhost redirect | Verify `http://localhost:8919/callback` in Auth tab |
| Port 8919 in use | `lsof -i :8919 \| grep LISTEN` → kill the process |
| Timeout / no browser | Check default browser is configured; a login window waits up to 5 minutes |
| "not captured via Playwright login window" | Run `refresh_web_session`; if that fails, re-run `authenticate` |
| "not captured via system browser" / "Unable to get key for cookie decryption" | Playwright path was skipped: run `python scripts/diagnose.py`, `playwright install chromium`, or set `LINKEDIN_AUTH_MODE=playwright` |
