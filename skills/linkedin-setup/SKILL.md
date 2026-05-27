---
name: linkedin-setup
description: >
  First-time setup guide for the LinkedIn MCP plugin. Trigger with "set up
  LinkedIn", "configure LinkedIn MCP", "LinkedIn setup", "how do I connect
  LinkedIn", or when the user gets a credentials error from any LinkedIn tool.
---

Walk the user through the complete first-time setup in this exact order.
Confirm each step is done before moving to the next.

## Step 1 — Prerequisites

Confirm the user has:
- **uv** installed (`uv --version`). If not: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Claude Code CLI** installed (`claude --version`). If not: https://claude.ai/download

## Step 2 — Create a LinkedIn Developer App

Tell the user to:
1. Go to https://www.linkedin.com/developers/apps → **Create app**
2. Fill in App name, LinkedIn page, any privacy policy URL
3. **Products** tab → request access to **both**:
   - **Sign In with LinkedIn using OpenID Connect**
   - **Share on LinkedIn** (both auto-approved instantly)
4. **Auth** tab → **OAuth 2.0 settings** → add redirect URL exactly:
   `http://localhost:8919/callback`
5. Copy **Client ID** and **Client Secret**

## Step 3 — Save credentials to the OS keychain

Run this command in the terminal — it prompts for credentials interactively
(Client Secret is hidden, never echoed):

```bash
cd /path/to/linkedin-mcp/mcp
uv run python -m linkedin_mcp setup
```

Credentials are stored in the OS keychain (macOS Keychain / Windows Credential
Manager / Linux Secret Service). No `.env` file is created or needed.

If the OS keychain is unavailable, fall back to a `.env` file:

```bash
cp .env.example .env
# Edit .env with LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET
```

On first `authenticate`, the `.env` credentials are auto-migrated to the keychain.

## Step 4 — Authenticate

Ask the user for a short alias for this account (e.g. `work` or `personal`),
then tell them to say:

> **"Authenticate with LinkedIn using the alias 'work'"**

Claude will call `authenticate("work")`, open the browser to LinkedIn's
authorization page, and — after approval — save the token under the `work` alias.
Browser session cookies are captured automatically, unlocking the Voyager tier.

## Step 5 — Verify

Call `check_auth` (look for `active_user` and `tier`), then `get_profile`.
If both return data, setup is complete.

To add more accounts later, repeat Step 4 with a different alias (e.g. `personal`),
then use `switch_user` to move between them.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| "LinkedIn app credentials not found" | Keychain empty and no `.env` | Re-run `python -m linkedin_mcp setup` |
| "The requested permission scope is not valid" | LinkedIn Products not added | Add both Products in Step 2 |
| "Bummer, something went wrong" | Redirect URL mismatch | Verify `http://localhost:8919/callback` in Auth tab |
| "401 Unauthorized" | Token expired | Run `authenticate` again with the same alias |
| "Invalid alias" | Alias contains spaces or special characters | Use only letters, digits, hyphens, underscores |
| Port 8919 in use | Another process on the port | `lsof -i :8919` then kill it |
