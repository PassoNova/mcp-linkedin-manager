# LinkedIn MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude manage your LinkedIn profile and content through the official LinkedIn REST API v2, with an extended **Voyager tier** that unlocks full profile read/write via browser session cookies — no partner-program approval required.

---

## Features

### Authentication & account tools

| Tool | What it does |
|---|---|
| `authenticate` | OAuth 2.0 browser flow for a named alias (e.g. `work`, `personal`) |
| `logout` | Remove one user's credentials (defaults to active account) |
| `check_auth` | Show active user's token status, capability tier, scopes, and keychain status |
| `switch_user` | Set the active LinkedIn account by alias |
| `list_users` | List all registered aliases with their auth status and tier |
| `set_web_session` | Manually store `li_at` + `JSESSIONID` cookies for the active account |
| `clear_web_session` | Remove browser session cookies for the active account |
| `clear_credentials` | Remove shared app credentials (Client ID + Secret) from the OS keychain |

### Profile & content tools

| Tool | What it does |
|---|---|
| `get_profile` | Name, headline, email, profile URL, person URN |
| `get_full_profile` | Full profile sections — about, experience, education, skills (Voyager) |
| `update_headline` | Change your profile headline |
| `create_post` | Publish a PUBLIC or CONNECTIONS-only text post (up to 3,000 chars) |
| `get_posts` | List your recent posts (up to 50) — falls back to Voyager automatically |
| `get_recent_activity` | List recent posts via browser session (no partner scope needed) |
| `delete_post` | Remove a post by URN |

### Social & inbox tools

| Tool | What it does |
|---|---|
| `get_notifications` | Fetch your recent LinkedIn notifications (Voyager) |
| `get_conversations` | Fetch your recent direct message conversations (Voyager) |
| `get_community_stats` | Connection count (partner scope; graceful fallback) |
| `get_api_capabilities` | What the standard OAuth API can / cannot do |

---

## Multi-account support

The server supports multiple LinkedIn accounts, each identified by a short alias you choose at authenticate time (e.g. `work`, `personal`). App credentials (CLIENT_ID / SECRET) are shared — only user tokens are per-alias.

```
Authenticate my work account → authenticate("work")
Authenticate my personal account → authenticate("personal")
Switch to personal → switch_user("personal")
See all accounts → list_users
Log out personal → logout("personal")
```

One alias is always "active" — all tools (get_profile, create_post, etc.) operate on it implicitly. Use `switch_user` to change it. `check_auth` always shows the active account.

## Capability tiers

Each alias has its own tier:

| Tier | Condition | Available tools |
|---|---|---|
| `BASE` | No valid token | `authenticate` only |
| `OAUTH` | Valid OAuth token | All standard tools |
| `VOYAGER` | OAuth token + browser session | All tools, including full profile, notifications, and conversations |

The Voyager tier uses the same internal API as LinkedIn's web app (`li_at` + `JSESSIONID` cookies). It is captured automatically during `authenticate` if Playwright can extract the cookies, or you can set it manually with `set_web_session`.

---

## LinkedIn API limitations

The standard Consumer API (no partner approval required) supports:

- ✅ Read basic profile (name, headline, photo, email)
- ✅ Create, read, and delete posts
- ⚠️ Update headline — requires `rw_me` scope (may be partner-gated)
- ❌ Experience, education, certifications, skills → partner API only
- ❌ Connections list, messages → partner-gated scopes

**Voyager tier bypasses most of these restrictions** by using LinkedIn's own web API.

---

## Setup

### 1. Create a LinkedIn Developer App

1. Go to [https://www.linkedin.com/developers/apps](https://www.linkedin.com/developers/apps) and click **Create app**.
2. Fill in App name, LinkedIn page, and Privacy policy URL.
3. In **Products**, request:
   - **Sign In with LinkedIn using OpenID Connect** (profile + email)
   - **Share on LinkedIn** (create/delete posts)
4. In **Auth → OAuth 2.0 settings**, add the redirect URL:
   ```
   http://localhost:8919/callback
   ```
5. Copy your **Client ID** and **Client Secret**.

### 2. Save your credentials

**Option A — OS keychain (recommended):**

```bash
cd linkedin-mcp/mcp
uv run python -m linkedin_mcp setup
```

This prompts for your Client ID and Client Secret interactively (secret input, no echo) and saves them directly to the OS keychain. No `.env` file is created or needed.

**Option B — `.env` file (bootstrap):**

```bash
cd linkedin-mcp/mcp
cp .env.example .env
# Edit .env and fill in LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET
```

On first `authenticate`, credentials are auto-migrated from the `.env` file to the keychain and you can delete the file afterward.

### 3. Install dependencies

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
cd linkedin-mcp/mcp
uv sync
playwright install chromium   # needed for Voyager tier
```

### 4. Run the server

```bash
cd linkedin-mcp/mcp
uv run server.py
```

You should see the MCP server start. Press Ctrl+C to stop.

---

## Connect to Claude

### Claude Code (CLI)

```bash
claude mcp add linkedin-manager \
  -e LINKEDIN_CLIENT_ID=your_client_id \
  -e LINKEDIN_CLIENT_SECRET=your_client_secret \
  -- python /full/path/to/linkedin-mcp/mcp/server.py
```

### Claude Desktop / settings.json

Add this to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "linkedin-manager": {
      "command": "python",
      "args": ["/full/path/to/linkedin-mcp/mcp/server.py"],
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
Authenticate my LinkedIn account — use the alias "work"
```

Claude calls `authenticate("work")`, opens your browser to LinkedIn's authorization page, and saves the token under the `work` alias. The alias becomes the active account. Playwright captures the Voyager session automatically if available.

To add a second account:

```
Authenticate my personal LinkedIn account — alias "personal"
```

Then switch between them:

```
Switch to my personal account
```

If the Voyager session was not captured automatically, set it manually:

1. Open [linkedin.com](https://www.linkedin.com) in your browser (must be logged in).
2. Open DevTools → **Application** (Chrome) or **Storage** (Firefox) → Cookies → `https://www.linkedin.com`.
3. Copy `li_at` and `JSESSIONID`.
4. Tell Claude: `Set my LinkedIn web session` and provide both values.

Then try:

```
Get my LinkedIn profile
```

```
Show my full LinkedIn profile including experience and education
```

```
Show my recent LinkedIn posts
```

```
Post to LinkedIn: "Excited to share that I've been working on an AI-powered LinkedIn MCP!"
```

---

## File structure

```
linkedin-mcp/
├── .env.example         # Credential template (keychain preferred over this file)
├── mcp/
│   ├── server.py        # FastMCP server — all tools
│   ├── client.py        # LinkedIn REST API v2 + VoyagerClient
│   ├── auth.py          # OAuth 2.0 flow, keychain-backed per-user + shared credential storage
│   ├── cache.py         # Response caching layer
│   ├── __main__.py      # CLI: `python -m linkedin_mcp setup`
│   ├── pyproject.toml
│   ├── uv.lock
│   └── tests/
│       ├── test_auth_csrf.py
│       ├── test_cache.py
│       ├── test_client_version.py
│       ├── test_connection_pool.py
│       ├── test_keyring.py      # keychain tests for all credential classes
│       ├── test_users.py        # user registry (alias management) tests
│       ├── test_playwright_pool.py
│       └── test_retry.py
└── README.md
```

---

## Security notes

All three credential classes are stored in the OS keychain when `keyring` is available (macOS Keychain, Windows Credential Manager, Linux Secret Service):

| Credential | Primary storage | Fallback |
|---|---|---|
| Client ID + Secret | OS keychain (`linkedin-mcp / credentials`) | env vars / `.env` only |
| OAuth token (per alias) | OS keychain (`linkedin-mcp / oauth_token:work`) | `~/.linkedin_mcp_token_work.json` (0600) |
| Voyager session (per alias) | OS keychain (`linkedin-mcp / session:work`) | `~/.linkedin_mcp_session_work.json` (0600) |

The user registry (`~/.linkedin_mcp_users.json`) stores only alias names and the active pointer — it is not sensitive.

- App credentials have **no plaintext file fallback** — if the keychain is unavailable they must come from environment variables.
- Fallback files are written with mode `0600` (owner-read only).
- Never commit your `.env` file to version control. Delete it once credentials are in the keychain.
- LinkedIn OAuth tokens expire after **60 days**. Re-run `authenticate` when prompted.
- Browser session cookies (`li_at`, `JSESSIONID`) typically last ~1 year but are invalidated if you log out of linkedin.com.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `LinkedIn app credentials not found` | Run `python -m linkedin_mcp setup` or set `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` env vars |
| `401 Unauthorized` after authenticate | Token expired — run `authenticate` again |
| `403 Forbidden` on `update_headline` | App lacks `rw_me` scope; use `set_web_session` for Voyager tier |
| `403 Forbidden` on `get_posts` | Falling back to Voyager automatically; run `set_web_session` if not already set |
| `❌ Web session required` | Run `authenticate` or `set_web_session` to enable Voyager tools |
| `credentials_in_keychain: false` in `check_auth` | Run `python -m linkedin_mcp setup` to store credentials in the keychain |
| `keychain_available: false` in `check_auth` | Install a keyring backend: `pip install keyrings.alt` (Linux) or use macOS/Windows |
| Browser doesn't open during auth | Run `uv run server.py` directly and paste the printed URL manually |
| Port 8919 in use | Set `LINKEDIN_REDIRECT_PORT=8920` and update the LinkedIn app redirect URL |
| `tier: BASE` in `check_auth` | Token missing or expired — run `authenticate` |
| `No active user` error | No alias registered yet — run `authenticate("work")` |
| `Unknown alias` in `switch_user` | Alias not yet authenticated — run `authenticate` with that alias first |
