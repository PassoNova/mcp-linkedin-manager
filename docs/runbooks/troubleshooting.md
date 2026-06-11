# Runbook: Troubleshooting

This document covers every common failure mode, how to diagnose it, and the exact fix.

**Quick start:** when something breaks, run the diagnostics script first:

```bash
python scripts/diagnose.py
```

And check the log:

```bash
tail -f ~/.linkedin_mcp.log
grep "ERROR\|FAIL\|Exception" ~/.linkedin_mcp.log
```

---

## Authentication failures

### "LinkedIn app credentials not found"

**Cause:** The OS keychain has no entry for `linkedin-mcp / credentials` and there is no `.env` file.

**Fix:**
```bash
cd mcp
uv run python -m linkedin_mcp setup
```
Enter Client ID and Client Secret when prompted. If the keychain backend is unavailable, copy `.env.example` to `.env` and fill in values — they are auto-migrated to the keychain on next `authenticate`.

---

### "The requested permission scope is not valid"

**Cause:** The LinkedIn Developer App is missing one or both required Products.

**Fix:** Go to https://www.linkedin.com/developers/apps → your app → **Products** tab. Add both:
- **Sign In with LinkedIn using OpenID Connect**
- **Share on LinkedIn**

Both are auto-approved. Green checkmark = approved.

---

### "Bummer, something went wrong" on the LinkedIn login page

**Cause:** The redirect URL in the LinkedIn app settings does not match `http://localhost:8919/callback` exactly.

**Fix:** Go to **Auth** tab → **OAuth 2.0 settings** → verify the entry is exactly:
```
http://localhost:8919/callback
```
No trailing slash. No `https`. Remove any other redirect URLs if they conflict.

---

### `authenticate` opens browser but times out / callback never fires

**Cause options:**
1. Port 8919 is already in use by another process.
2. A firewall or network filter is blocking `localhost:8919`.
3. The browser opened but the user did not complete LinkedIn login before the 5-minute timeout.

**Diagnose:**
```bash
lsof -i :8919
```

**Fix:**
- If another process is on 8919: `kill <PID>` and re-try.
- If a firewall is blocking localhost loopback: temporarily disable it or add an exception.
- If the user timed out: simply re-run `authenticate`.

---

### "401 Unauthorized" on any tool call

**Cause:** The OAuth token has expired (~60 days after issue).

**Fix:** Re-authenticate:
```
"Authenticate with LinkedIn using the alias 'work'"
```

---

### "403 Forbidden" on `update_headline` or similar

**Cause:** The `rw_me` scope is not available on standard LinkedIn Developer apps — it requires partner-program approval. Similarly, `r_member_social` is gated.

**Fix options:**
1. Edit the headline directly at https://www.linkedin.com/in/me → Edit profile.
2. If Voyager tier is active (`check_auth` shows `tier: VOYAGER`), `update_headline` uses the Voyager path which does not require partner scopes.
3. Re-authenticate to ensure the browser session was captured (which unlocks Voyager).

---

### Chrome not found / cookies not captured

**Cause:** Google Chrome is not installed, or the system default browser is not Chrome, so `browser_cookie3` cannot find the LinkedIn cookies.

**Diagnose:**
```bash
ls "/Applications/Google Chrome.app"   # macOS
which google-chrome                     # Linux
```

**Fix option 1 — Install Chrome** and re-run `authenticate`.

**Fix option 2 — Set session manually:**
1. Open https://www.linkedin.com in Chrome, log in.
2. DevTools → Application → Cookies → `https://www.linkedin.com`
3. Copy `li_at` and `JSESSIONID`.
4. Tell Claude: "Set my LinkedIn web session: li_at is `<value>`, jsessionid is `<value>`"

---

## Tool-specific errors

### `get_posts` returns 403 / `NO_VERSION`

**Cause:** The legacy `GET /v2/ugcPosts?q=authors` endpoint requires a partner-approved version; the server now uses `GET /rest/posts?q=author` with the `LinkedIn-Version` header.

**Fix:** This is already fixed in the codebase as of the [Unreleased] version in CHANGELOG.md. If you are running an older build, pull the latest:
```bash
git pull origin main
# Then fully restart Claude (⌘Q on macOS) so Python reloads the modules
```

---

### `get_profile` shows empty headline

**Cause:** `GET /v2/me` (which includes the headline) requires partner-level `r_liteprofile` scope. The server silently omits the headline if this scope is unavailable, rather than showing a 403 error in the profile output.

**This is expected behavior** — the profile name and email still return correctly. Headline is available via the Voyager tier (`get_full_profile`).

---

### Voyager tools return "No persistent browser profile" or "Playwright error"

**Cause:** Playwright Chromium is not installed, or the Voyager browser profile directory (`~/.linkedin_mcp_browser_<alias>/`) is missing or corrupted.

**Fix:**
```bash
cd mcp
uv run playwright install chromium
```
Then re-run `authenticate` to recreate the browser profile.

If the profile directory exists but is corrupted:
```bash
rm -rf ~/.linkedin_mcp_browser_work/
```
Re-authenticate to regenerate it.

---

### `get_conversations` or `get_notifications` returns empty / error

**Cause:** These tools use the Voyager API which is rate-limited and occasionally returns empty responses during LinkedIn maintenance windows.

**Fix:** Wait a few minutes and retry. If the issue persists, check that `check_auth` shows `tier: VOYAGER` — if it shows `OAUTH`, the browser session expired and needs to be refreshed via `authenticate` or `set_web_session`.

---

## Server / startup issues

### MCP server not starting / tools not visible in Claude

**Diagnose:**
```bash
# Check that the server starts without errors
cd mcp
uv run python server.py
```

Common causes:
- `uv sync` not run: dependencies not installed. Run `uv sync`.
- `mcp/README.md` missing: `pyproject.toml` declares `readme = "README.md"`. Fix: `cp README.md mcp/README.md`.
- Missing env vars: `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` not set and keychain is empty.

---

### Code changes not reflected after editing

**Cause:** Python loads all modules at startup. Editing `.py` files while the server is running has no effect.

**Fix:** Fully quit and reopen Claude (⌘Q on macOS, then reopen). The server process restarts and loads the updated modules.

---

### `git index.lock` error

**Cause:** A previous git operation was interrupted.

**Fix:**
```bash
rm /path/to/mcp-linkedin-manager/.git/index.lock
```

---

## Escalation path

If none of the above resolves the issue:

1. Run `python scripts/diagnose.py` and copy the full output.
2. Run `grep "ERROR" ~/.linkedin_mcp.log | tail -50` and copy the errors.
3. Open an issue at https://github.com/PassoNova/mcp-linkedin-manager/issues with both outputs.

Set `LINKEDIN_MCP_DEBUG=1` before starting Claude to get verbose output in the server's stderr, which Claude Code shows in its log panel.
