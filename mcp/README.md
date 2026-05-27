# LinkedIn MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude manage your LinkedIn profile and content — through the official LinkedIn REST API and, optionally, LinkedIn's internal Voyager API via a browser session for full profile access.

---

## Capability tiers

| Tier | Requires | Unlocks |
|---|---|---|
| **OAUTH** | LinkedIn Developer App + OAuth flow | Profile, posting, delete |
| **VOYAGER** | OAUTH + browser session (captured automatically during `authenticate`) | Full profile, headline edit, notifications, messaging |

---

## Tools

### Auth
| Tool | What it does |
|---|---|
| `authenticate` | OAuth 2.0 browser flow — saves token; also captures browser session cookies for Voyager |
| `logout` | Delete the saved OAuth token |
| `check_auth` | Token status, granted scopes, and active capability **tier** |
| `set_web_session` | Manually store `li_at` + `JSESSIONID` cookies (if not captured automatically) |
| `clear_web_session` | Remove saved browser session cookies |

### Profile
| Tool | What it does |
|---|---|
| `get_profile` | Name, headline, email, profile URL — Voyager used automatically when available |
| `update_headline` | Change your headline — Voyager preferred, OAuth fallback |
| `get_full_profile` | About, experience, education, certifications, skills — **Voyager tier only** |

### Posts
| Tool | What it does |
|---|---|
| `create_post` | Publish a PUBLIC or CONNECTIONS-only text post |
| `get_posts` | List your recent posts (up to 50) — Voyager fallback when OAuth scope missing |
| `get_recent_activity` | Scrape your activity feed via browser — **Voyager tier only** |
| `delete_post` | Remove a post by URN |

### Community & Messaging
| Tool | What it does |
|---|---|
| `get_notifications` | Recent LinkedIn notifications — **Voyager tier only** |
| `get_conversations` | Recent DM conversations — **Voyager tier only** |
| `get_community_stats` | Connection count (partner scope; graceful fallback) |
| `get_api_capabilities` | What the standard API can / cannot do |

---

## Setup

### Prerequisites

- **uv** — [install](https://docs.astral.sh/uv/getting-started/installation/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Claude Code CLI** — `claude --version` or [download](https://claude.ai/download)
- **Playwright** *(optional, for Voyager tier)* — `uv run playwright install chromium`

### 1. Create a LinkedIn Developer App

1. Go to [https://www.linkedin.com/developers/apps](https://www.linkedin.com/developers/apps) → **Create app**
2. Fill in App name, LinkedIn page, and any privacy policy URL
3. **Products** tab → request access to both (auto-approved instantly):
   - **Sign In with LinkedIn using OpenID Connect**
   - **Share on LinkedIn**
4. **Auth** tab → **OAuth 2.0 settings** → add redirect URL exactly:
   ```
   http://localhost:8919/callback
   ```
5. Copy **Client ID** and **Client Secret**

### 2. Save credentials

```bash
cat > ~/.linkedin_mcp.env << 'EOF'
LINKEDIN_CLIENT_ID=PASTE_CLIENT_ID_HERE
LINKEDIN_CLIENT_SECRET=PASTE_CLIENT_SECRET_HERE
EOF
chmod 600 ~/.linkedin_mcp.env
```

### 3. Connect to Claude Code

The server uses [uv](https://docs.astral.sh/uv/) for dependency management — no separate `pip install` needed.

```bash
claude mcp add linkedin-manager \
  --env-file ~/.linkedin_mcp.env \
  -- uv run --directory /full/path/to/linkedin-mcp/mcp python server.py
```

Or add directly to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "linkedin-manager": {
      "command": "uv",
      "args": ["run", "--directory", "/full/path/to/linkedin-mcp/mcp", "python", "server.py"],
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

Claude opens your browser to LinkedIn's authorization page. After approving, the token is saved and browser session cookies are captured automatically — unlocking the **VOYAGER** tier for full profile access.

Then try:

```
Get my LinkedIn profile
Get my full profile including experience and education
Show my recent LinkedIn notifications
Post to LinkedIn: "Just set up Claude as my LinkedIn copilot!"
```

---

## File structure

```
mcp/
├── server.py          # FastMCP server — all tools
├── client.py          # LinkedInClient (REST API) + VoyagerClient (browser)
├── auth.py            # OAuth 2.0 flow, token + session persistence
├── pyproject.toml
├── uv.lock
└── README.md
```

---

## Security notes

- OAuth token (`~/.linkedin_mcp_token.json`) and web session (`~/.linkedin_mcp_session.json`) are written with mode `0600`.
- Never commit your `.env` file or token file to version control.
- OAuth tokens expire after **60 days**. Browser session cookies (`li_at`) last about **1 year**.
- Re-run `authenticate` when prompted for expired tokens.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `credentials are not configured` | Check `~/.linkedin_mcp.env` and restart Claude |
| `401 Unauthorized` | Token expired — run `authenticate` again |
| `403 Forbidden` on `update_headline` | Voyager session needed — run `authenticate` |
| `403 Forbidden` on profile sections | Use `get_full_profile` (Voyager) instead |
| Browser doesn't open | Ensure a default browser is configured |
| Port 8919 in use | `lsof -i :8919 \| grep LISTEN` → kill the process |
| Voyager calls fail | Run `uv run playwright install chromium` in the `mcp/` directory |
