# LinkedIn MCP — Setup Steps

Walk the user through the complete first-time setup in this exact order.
Confirm each step is done before moving to the next. Use clear, friendly language.

## Step 1 — Prerequisites

Confirm the user has:
- **uv** installed (`uv --version` in the terminal). If not, tell them to run:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Claude Code CLI** installed (`claude --version`). If not, direct them to
  https://claude.ai/download

## Step 2 — Create a LinkedIn Developer App

Tell the user to:
1. Go to https://www.linkedin.com/developers/apps and click **Create app**
2. Fill in App name (e.g. "My Claude Assistant"), their LinkedIn Company/personal page, and any privacy policy URL
3. Click the **Products** tab and request access to **both**:
   - **Sign In with LinkedIn using OpenID Connect** (grants openid, profile, email)
   - **Share on LinkedIn** (grants w_member_social for creating/deleting posts)
   - Both are auto-approved instantly — a green checkmark confirms it
4. Click the **Auth** tab → **OAuth 2.0 settings** → add this exact redirect URL:
   `http://localhost:8919/callback`
5. Copy the **Client ID** and **Client Secret** from the Auth tab

## Step 3 — Save credentials to the OS keychain

Tell the user to run this command in the terminal. The Client Secret is entered
via hidden input (no echo) — credentials go straight to the OS keychain:

```bash
cd /path/to/linkedin-mcp/mcp
uv run python -m linkedin_mcp setup
```

This stores credentials in macOS Keychain, Windows Credential Manager, or Linux
Secret Service. No `.env` file is created.

**Alternative (bootstrap via .env):** If the OS keychain is unavailable, the
user can copy `.env.example` to `.env`, fill in credentials, and they will be
auto-migrated to the keychain on the first `authenticate` call.

## Step 4 — Authenticate

Ask the user for a short alias for their account (letters, digits, hyphens,
underscores only — e.g. `work`, `personal`). Then tell them to say:

> **"Authenticate with LinkedIn using the alias 'work'"**

Claude calls `authenticate("work")`, which:
1. Opens the user's default browser to LinkedIn's authorization page
2. Intercepts the OAuth callback via Playwright (no manual server setup)
3. Exchanges the auth code for an access token (valid ~60 days)
4. Saves the token to the OS keychain under `oauth_token:work`
5. Captures `li_at` + `JSESSIONID` browser cookies → saves to `session:work`
   → Voyager tier unlocked automatically

The alias `work` becomes the active account. All tools operate on it until
`switch_user` is called.

## Step 5 — Verify

Call `check_auth` — confirm `active_user` matches the alias and `tier` is
`VOYAGER` (or at least `OAUTH`). Then call `get_profile` to display the user's
name and headline. If both return data, setup is complete.

## Adding more accounts

Repeat Step 4 with a different alias (e.g. `personal`). The first alias remains
active. Use `switch_user("personal")` to switch, or `list_users` to see all accounts.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| "LinkedIn app credentials not found" | Keychain empty and no `.env` | Re-run `python -m linkedin_mcp setup` |
| "The requested permission scope is not valid" | LinkedIn Products not added | Complete Step 2, add both Products |
| "Bummer, something went wrong" on LinkedIn | Redirect URL mismatch | Verify `http://localhost:8919/callback` in Auth tab |
| "401 Unauthorized" | Token expired | Run `authenticate("work")` again |
| "Invalid alias" | Special characters in alias | Use only letters, digits, hyphens, underscores |
| "403 Forbidden" | Scope not approved | See `get_api_capabilities` for what's available |
| Port 8919 in use | Another process using the port | Run `lsof -i :8919` to find and kill it |
