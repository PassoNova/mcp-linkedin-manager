---
name: linkedin-setup
description: >
  First-time setup guide for the LinkedIn MCP plugin. Trigger with "set up
  LinkedIn", "configure LinkedIn MCP", "LinkedIn setup", "how do I connect
  LinkedIn", or when the user gets a credentials error from any LinkedIn tool.
---

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

## Step 3 — Save credentials

Tell the user to create the file `~/.linkedin_mcp.env` with this content,
replacing the placeholders with their actual values:

```
LINKEDIN_CLIENT_ID=their_client_id_here
LINKEDIN_CLIENT_SECRET=their_client_secret_here
```

They can do this in the terminal with:
```bash
cat > ~/.linkedin_mcp.env << 'EOF'
LINKEDIN_CLIENT_ID=PASTE_CLIENT_ID_HERE
LINKEDIN_CLIENT_SECRET=PASTE_CLIENT_SECRET_HERE
EOF
chmod 600 ~/.linkedin_mcp.env
```

## Step 4 — Restart Claude

Tell the user to fully quit Claude (⌘Q) and reopen it so the plugin picks up
the new credentials file.

## Step 5 — Authenticate

Tell the user to say: **"Authenticate with LinkedIn"**

Claude will call the `authenticate` tool, which opens their browser to
LinkedIn's authorization page. After approving, the browser shows a success
page and Claude confirms the token is saved.

## Step 6 — Verify

Call `check_auth` to confirm authentication succeeded, then call `get_profile`
to display their name and headline. If both return data, setup is complete.

## Troubleshooting guide

If the user hits an error, diagnose by matching these patterns:

| Error | Cause | Fix |
|---|---|---|
| "credentials are not configured" | ~/.linkedin_mcp.env missing or not loaded | Re-check Step 3, restart Claude |
| "The requested permission scope is not valid" | LinkedIn Products not added | Complete Step 2, add both Products |
| "Bummer, something went wrong" on LinkedIn | Redirect URL mismatch | Verify `http://localhost:8919/callback` in Auth tab |
| "401 Unauthorized" | Token expired | Run `authenticate` again |
| "403 Forbidden" | Scope not approved | See `get_api_capabilities` for what's available |
| Port 8919 in use | Another process using the port | Run `lsof -i :8919` to find and kill it |
