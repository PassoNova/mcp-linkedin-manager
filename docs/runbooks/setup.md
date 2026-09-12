# Runbook: First-Time Setup

**Audience:** End users installing the LinkedIn MCP plugin for the first time.
**Time required:** ~10 minutes.
**Prerequisites:** macOS, Windows, or Linux; Google Chrome recommended.

---

## Overview

The setup has five steps:

1. Install prerequisites (uv, Claude Code CLI)
2. Create a LinkedIn Developer App and get credentials
3. Store credentials in the OS keychain
4. Authenticate with an alias
5. Verify the connection

---

## Step 1 — Install prerequisites

**uv** (Python package manager):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify: `uv --version` should print `0.x.y` or later.

**Claude Code CLI** (required to register the MCP server):
Download from https://claude.ai/download and follow the installer.

Verify: `claude --version` should print a version number.

---

## Step 2 — Create a LinkedIn Developer App

1. Go to https://www.linkedin.com/developers/apps and click **Create app**.
2. Fill in:
   - **App name**: anything (e.g. "My Claude Assistant")
   - **LinkedIn Page**: your personal or company page
   - **Privacy policy URL**: any URL (e.g. `https://example.com`)
3. On the **Products** tab, request access to **both** products — both are auto-approved instantly:
   - **Sign In with LinkedIn using OpenID Connect** (grants `openid`, `profile`, `email`)
   - **Share on LinkedIn** (grants `w_member_social` for create/delete posts)
4. On the **Auth** tab → **OAuth 2.0 settings**, add this redirect URL exactly:
   ```
   http://localhost:8919/callback
   ```
5. Still on the **Auth** tab, copy the **Client ID** and **Client Secret**. Keep these safe.

> **Tip:** The redirect URL must match exactly — no trailing slash, no HTTPS. LinkedIn will reject the OAuth callback if it doesn't match.

---

## Step 3 — Store credentials in the OS keychain

Clone the repo and run the interactive setup command:

```bash
git clone https://github.com/PassoNova/mcp-linkedin-manager.git
cd mcp-linkedin-manager/mcp
uv sync
uv run python -m linkedin_mcp setup
```

When prompted:
- Enter your **Client ID** (visible in the terminal)
- Enter your **Client Secret** (hidden — not echoed)

The credentials are written directly to:
- **macOS**: Keychain Access (`linkedin-mcp / credentials`)
- **Windows**: Windows Credential Manager
- **Linux**: Secret Service (libsecret). There is **no file fallback** for app credentials — without a keychain backend the wizard exits and you must use environment variables or the `.env` file below.

No `.env` file is created. You do not need to set any environment variables.

**Alternative (`.env` fallback):** if the OS keychain is unavailable:
```bash
cp ../.env.example .env   # .env.example is at the repository root
chmod 600 .env
# Edit .env with LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET
```
With a keychain backend, the first `authenticate` migrates the `.env` values to the keychain (`check_auth` reports `credentials_in_keychain: true`) and the file can then be deleted. Without one, nothing is migrated and the `.env` must stay.

---

## Step 4 — Register the MCP server with Claude

```bash
claude mcp add linkedin-manager -- uv run \
  --directory /absolute/path/to/mcp-linkedin-manager/mcp \
  python server.py
```

Replace `/absolute/path/to/mcp-linkedin-manager` with the actual cloned path.

To verify registration:
```bash
claude mcp list
```
You should see `linkedin-manager` in the output.

Alternatively, for **Claude Desktop**, add to `~/.claude.json` or use the Cowork plugin installer (see [scripts/install.sh](../../scripts/install.sh)).

---

## Step 5 — Authenticate

Open Claude and say:

> **"Authenticate with LinkedIn using the alias 'work'"**

Claude calls `authenticate("work")`, which:
1. Opens Google Chrome to LinkedIn's authorization page
2. After you approve, intercepts the OAuth callback at `localhost:8919`
3. Exchanges the auth code for an access token (valid ~60 days)
4. Saves the token to the OS keychain under `oauth_token:work`
5. Reads `li_at` + `JSESSIONID` from Chrome's cookie store → saves to `session:work`

The Voyager tier is unlocked automatically if Chrome was used.

---

## Step 6 — Verify

Ask Claude: **"Check my LinkedIn auth status"**

Expected output:
```
✅ Authenticated
  active_user: work
  tier: VOYAGER
  token_expires: 2026-xx-xx
  scopes: email, openid, profile, w_member_social
```

Then: **"Get my LinkedIn profile"** — should return your name and headline.

If both succeed, setup is complete.

---

## Adding more accounts

Repeat Step 5 with a different alias:

> **"Authenticate with LinkedIn using the alias 'personal'"**

Use `switch_user("personal")` to switch the active account, or `list_users` to see all registered aliases.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "LinkedIn app credentials not found" | Keychain empty, no `.env` | Re-run `uv run python -m linkedin_mcp setup` |
| "The requested permission scope is not valid" | Products not added to the app | Complete Step 2, add both Products |
| "Bummer, something went wrong" on LinkedIn | Redirect URL mismatch | Verify `http://localhost:8919/callback` in Auth tab (no trailing slash) |
| OAuth browser never opens | `chrome` not in PATH | Install Chrome at `/Applications/Google Chrome.app` (macOS) |
| `uv sync` fails with README error | `mcp/README.md` missing | `cp README.md mcp/README.md` |
| Port 8919 in use | Another process on the port | `lsof -i :8919`, then `kill <PID>` |

For deeper diagnostics, run:
```bash
python scripts/diagnose.py
```
