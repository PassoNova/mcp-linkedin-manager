---
name: linkedin-authenticate
description: >
  Authenticate or re-authenticate with LinkedIn via OAuth 2.0. Trigger with
  "authenticate with LinkedIn", "connect LinkedIn", "log in to LinkedIn",
  "LinkedIn token expired", "refresh LinkedIn auth", or when any LinkedIn
  tool returns a 401 error or "not authenticated" message.
---

## Pre-flight check

Call `check_auth` first.
- `"authenticated": true` and `"expired": false` → ask if they want to re-authenticate anyway
- expired or not authenticated → proceed immediately

## Run the OAuth flow

Call the `authenticate` tool. What happens:
1. Browser opens to LinkedIn's authorization page
2. Local HTTP server starts on port 8919 for the callback
3. User approves → LinkedIn redirects to `http://localhost:8919/callback`
4. Tool exchanges code for token (valid ~60 days), saved to `~/.linkedin_mcp_token.json`
5. Browser session cookies (`li_at`, `jsessionid`) captured → Voyager tier unlocked

## Confirm success

Call `check_auth` (note the `tier` field), then `get_profile`.
If tier is `VOYAGER`, mention that full profile, notifications, and messaging are now accessible.

## Handle errors

| Error | Fix |
|---|---|
| "credentials are not configured" | Run `linkedin-setup` skill first |
| "The requested permission scope is not valid" | Add both Products in LinkedIn Developer App |
| "Bummer" / localhost redirect | Verify `http://localhost:8919/callback` in Auth tab |
| Port 8919 in use | `lsof -i :8919 \| grep LISTEN` → kill the process |
| Timeout / no browser | Check default browser is configured |
