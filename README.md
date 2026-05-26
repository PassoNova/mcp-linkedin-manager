# LinkedIn MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude manage your LinkedIn profile and content through the official LinkedIn REST API v2.

---

## Features

| Tool | What it does |
|---|---|
| `authenticate` | OAuth 2.0 browser flow — saves token to `~/.linkedin_mcp_token.json` |
| `logout` | Delete the saved token |
| `check_auth` | Show token status and granted scopes |
| `get_profile` | Name, headline, email, profile URL, person URN |
| `update_headline` | Change your profile headline |
| `create_post` | Publish a PUBLIC or CONNECTIONS-only text post |
| `get_posts` | List your recent posts (up to 50) |
| `delete_post` | Remove a post by URN |
| `get_api_capabilities` | What the standard API can / cannot do |
| `get_community_stats` | Connection count (partner scope; graceful fallback) |

### LinkedIn API limitations

The standard Consumer API (no partner approval required) supports:
- ✅ Read basic profile (name, headline, photo, email)
- ✅ Create, read, and delete UGC posts
- ⚠️ Update headline — requires `rw_me` scope (may be partner-gated)
- ❌ Experience, education, certifications, skills → requires LinkedIn Partner Program
- ❌ Connections list, messages, connection requests → partner-gated scopes

> **Tip:** For profile sections not available via the API, use *Claude in Chrome* — Claude can interact with linkedin.com directly through the browser extension.

---

## Setup

### 1. Create a LinkedIn Developer App

1. Go to [https://www.linkedin.com/developers/apps](https://www.linkedin.com/developers/apps) and click **Create app**.
2. Fill in App name, LinkedIn page (can be your personal page), and Privacy policy URL.
3. In **Products**, request:
   - **Sign In with LinkedIn using OpenID Connect** (for profile + email)
   - **Share on LinkedIn** (for creating/deleting posts)
4. In **Auth** → **OAuth 2.0 settings**, add an authorized redirect URL:
   ```
   http://localhost:8919/callback
   ```
5. Copy your **Client ID** and **Client Secret**.

### 2. Configure environment

```bash
cd linkedin-mcp
cp .env.example .env
# Edit .env and fill in LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET
```

### 3. Install dependencies

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run the server (test it works)

```bash
python server.py
```

You should see the MCP server start. Press Ctrl+C to stop.

---

## Connect to Claude

### Claude Code (CLI)

```bash
claude mcp add linkedin-manager -- python /full/path/to/linkedin-mcp/server.py
```

Then set your credentials in the MCP environment:

```bash
claude mcp add linkedin-manager \
  -e LINKEDIN_CLIENT_ID=your_client_id \
  -e LINKEDIN_CLIENT_SECRET=your_client_secret \
  -- python /full/path/to/linkedin-mcp/server.py
```

### Cowork (desktop app)

Add this to your Claude Code `settings.json` (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "linkedin-manager": {
      "command": "python",
      "args": ["/full/path/to/linkedin-mcp/server.py"],
      "env": {
        "LINKEDIN_CLIENT_ID": "your_client_id",
        "LINKEDIN_CLIENT_SECRET": "your_client_secret"
      }
    }
  }
}
```

---

## First use

Once connected, tell Claude:

```
Authenticate with LinkedIn
```

Claude will call the `authenticate` tool, which opens your browser to LinkedIn's authorization page. After you approve, Claude confirms success and saves the token.

Then try:

```
Get my LinkedIn profile
```

```
Post to LinkedIn: "Excited to share that I've been working on an AI-powered LinkedIn MCP!"
```

```
Show my recent LinkedIn posts
```

---

## File structure

```
linkedin-mcp/
├── server.py          # FastMCP server — all tools live here
├── client.py          # LinkedIn REST API v2 wrapper
├── auth.py            # OAuth 2.0 flow and token persistence
├── requirements.txt
├── .env.example
└── README.md
```

---

## Security notes

- The OAuth token file (`~/.linkedin_mcp_token.json`) is written with mode `0600` (owner-read only).
- Never commit your `.env` file or token file to version control.
- Access tokens issued by LinkedIn's standard Consumer API expire after **60 days**. Re-run `authenticate` when prompted.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `LINKEDIN_CLIENT_ID … must be set` | Add credentials to `.env` or pass them as env vars |
| `401 Unauthorized` after authenticate | Token expired — run `authenticate` again |
| `403 Forbidden` on update_headline | Your app likely doesn't have `rw_me` scope approved |
| Browser doesn't open | Run `python server.py` directly and paste the printed URL manually |
| Port 8919 in use | Set `LINKEDIN_REDIRECT_PORT=8920` (and update your LinkedIn app redirect URL) |
