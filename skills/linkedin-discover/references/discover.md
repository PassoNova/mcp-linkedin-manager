# LinkedIn MCP — Capability Discovery

This is the entry-point skill. Run it to understand the current state of the
LinkedIn MCP and what is available to the user right now.

## Step 1 — Query live state

Call these three tools in parallel:
- `check_auth` — returns the active user, authentication status, and capability tier
- `get_api_capabilities` — returns which tools are available in the current tier
- `list_users` — returns all registered aliases and their individual tiers

## Step 2 — Interpret the tier

Use the `tier` field from `check_auth` to determine what's unlocked:

### BASE tier (no users registered)
Nothing is available yet. The user needs to complete first-time setup.

Tell the user:
> "The LinkedIn plugin is installed but not yet configured. I need your
> LinkedIn Developer credentials to get started. Say **'Set up LinkedIn'**
> and I'll walk you through it (about 5 minutes)."

Route to: `linkedin-setup` skill.

---

### OAUTH tier (credentials configured, OAuth token active)
Core profile and posts features are available.

Tell the user:
> "LinkedIn is connected as **[active_user]** via OAuth. Here's what's available right now:"

| Capability | Status |
|---|---|
| View profile (name, headline, email) | ✅ Available |
| Create, list, delete posts | ✅ Available |
| Update headline | ⚠️ May need partner scope |
| View experience, education, certifications | ❌ Requires Voyager session |
| View notifications | ❌ Requires Voyager session |
| View messages / conversations | ❌ Requires Voyager session |

Then explain how to unlock more:
> "To unlock the full profile sections, notifications, and messaging, say
> **'Authenticate with LinkedIn'** — during the OAuth flow, your browser
> session cookies are captured automatically and stored alongside the token.
> This unlocks Voyager access without any additional setup."

---

### VOYAGER tier (browser session cookies active)
Full capabilities unlocked.

Tell the user:
> "LinkedIn is fully connected as **[active_user]**. All capabilities are active:"

| Capability | Status |
|---|---|
| View profile (name, headline, email) | ✅ OAuth |
| Create, list, delete posts | ✅ OAuth |
| Update headline | ✅ Voyager |
| View full profile (experience, education, certs, skills, about) | ✅ Voyager |
| View notifications | ✅ Voyager |
| View conversations / messages | ✅ Voyager |

---

## Step 3 — Offer next actions

After presenting the status, offer the most relevant next step based on tier:

- **BASE**: "Say 'Set up LinkedIn' to get started."
- **OAUTH**: Offer "Get my LinkedIn profile" or "Post to LinkedIn" or "Authenticate
  with LinkedIn to unlock full profile and messaging."
- **VOYAGER**: Offer "Get my LinkedIn profile", "Show my notifications",
  "Get my conversations", or "Post to LinkedIn."

## Multi-account summary (when list_users has more than one entry)

Present a compact table:

| Alias | Active | Tier |
|---|---|---|
| work | ✅ | VOYAGER |
| personal | — | OAUTH |

Offer: "Say 'Switch to personal' to change the active account."

## Note on token expiry

If `check_auth` returns `"expired": true`, tell the user their token has
expired (tokens last ~60 days) and route immediately to the `linkedin-authenticate`
skill. Do not attempt any other tool calls before re-authenticating.
