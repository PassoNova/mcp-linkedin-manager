---
name: linkedin-discover
description: >
  Discover what LinkedIn capabilities are currently active and what can be
  unlocked. Trigger with "what can you do with LinkedIn", "LinkedIn status",
  "what LinkedIn features are available", "check my LinkedIn connection",
  "LinkedIn capabilities", or at the start of any session before using other
  LinkedIn skills.
---

This is the entry-point skill. Run it to understand the current state of the
LinkedIn MCP and what is available to the user right now.

## Step 1 — Query live state

Call these two tools in parallel:
- `check_auth` — returns authentication status and the active capability tier
- `get_api_capabilities` — returns which tools are available in the current tier

## Step 2 — Interpret the tier

Use the `tier` field from `check_auth` to determine what's unlocked:

### BASE tier (no credentials configured)
Nothing is available yet. Tell the user:
> "The LinkedIn plugin is installed but not yet configured. Say **'Set up LinkedIn'**
> and I'll walk you through it (about 5 minutes)."

Route to: `linkedin-setup` skill.

### OAUTH tier (credentials + token active)
Core features are available. Tell the user:
> "LinkedIn is connected via OAuth. Here's what's available right now:"

| Capability | Status |
|---|---|
| View profile (name, headline, email) | ✅ |
| Create, list, delete posts | ✅ |
| Update headline | ⚠️ May need partner scope |
| Full profile, notifications, messaging | ❌ Requires Voyager session |

Offer to unlock more: "Say **'Authenticate with LinkedIn'** — during the OAuth
flow, browser session cookies are captured automatically, unlocking Voyager access."

### VOYAGER tier (browser session active)
Full capabilities unlocked. Tell the user:
> "LinkedIn is fully connected. All capabilities are active."

| Capability | Status |
|---|---|
| View profile (name, headline, email) | ✅ OAuth |
| Create, list, delete posts | ✅ OAuth |
| Update headline | ✅ Voyager |
| Full profile (experience, education, certs, skills, about) | ✅ Voyager |
| Notifications | ✅ Voyager |
| Conversations / messages | ✅ Voyager |

## Step 3 — Offer next actions

- **BASE**: "Say 'Set up LinkedIn' to get started."
- **OAUTH**: Offer profile view, posting, or "Authenticate with LinkedIn to unlock full profile."
- **VOYAGER**: Offer profile view, notifications, conversations, or posting.

If `check_auth` returns `"expired": true`, tell the user their token has expired
(~60 days) and route to `linkedin-authenticate` immediately.
