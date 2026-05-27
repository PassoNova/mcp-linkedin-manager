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

## Step 3 — Save credentials

```bash
cat > ~/.linkedin_mcp.env << 'EOF'
LINKEDIN_CLIENT_ID=PASTE_CLIENT_ID_HERE
LINKEDIN_CLIENT_SECRET=PASTE_CLIENT_SECRET_HERE
EOF
chmod 600 ~/.linkedin_mcp.env
```

## Step 4 — Restart Claude

Fully quit Claude (⌘Q) and reopen so the plugin picks up the credentials.

## Step 5 — Authenticate

Tell the user to say: **"Authenticate with LinkedIn"**

## Step 6 — Verify

Call `check_auth` then `get_profile`. If both return data, setup is complete.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| "credentials are not configured" | `~/.linkedin_mcp.env` missing | Re-check Step 3, restart Claude |
| "The requested permission scope is not valid" | LinkedIn Products not added | Add both Products in Step 2 |
| "Bummer, something went wrong" | Redirect URL mismatch | Verify `http://localhost:8919/callback` in Auth tab |
| "401 Unauthorized" | Token expired | Run `authenticate` again |
| Port 8919 in use | Another process on the port | `lsof -i :8919` then kill it |
