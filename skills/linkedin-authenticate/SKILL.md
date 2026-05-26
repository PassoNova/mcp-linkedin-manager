---
name: linkedin-authenticate
description: >
  Authenticate or re-authenticate with LinkedIn via OAuth 2.0. Trigger with
  "authenticate with LinkedIn", "connect LinkedIn", "log in to LinkedIn",
  "LinkedIn token expired", "refresh LinkedIn auth", or when any LinkedIn
  tool returns a 401 error or "not authenticated" message.
---

Follow these steps precisely.

## Pre-flight check

Call `check_auth` first.

- If it returns `"authenticated": true` and `"expired": false` — tell the user
  they are already authenticated and ask if they want to re-authenticate anyway.
- If it returns expired or not authenticated — proceed immediately.

## Run the OAuth flow

Call the `authenticate` tool.

What happens behind the scenes:
1. The tool opens the user's default browser to LinkedIn's authorization page
2. A local HTTP server starts on port 8919 to receive the callback
3. The user approves the permissions on LinkedIn
4. LinkedIn redirects to `http://localhost:8919/callback` with an auth code
5. The tool exchanges the code for an access token (valid ~60 days)
6. The token is saved to `~/.linkedin_mcp_token.json` (mode 0600, owner-read only)

## Confirm success

After `authenticate` returns, call `get_profile` to verify the token works.
Present the user's name and headline in a friendly confirmation message.

## Handle errors

| Error message | What to tell the user |
|---|---|
| "credentials are not configured" | Run the `linkedin-setup` skill first |
| "The requested permission scope is not valid" | Go to your LinkedIn Developer App → Products tab and add both "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn" |
| "Bummer" / redirect to localhost | Verify `http://localhost:8919/callback` is listed under Auth → Authorized redirect URLs in your LinkedIn Developer App |
| Port 8919 in use | Run `lsof -i :8919 \| grep LISTEN` in the terminal and kill the process, then retry |
| Timeout (no browser opened) | Check that a default browser is configured on the system |
