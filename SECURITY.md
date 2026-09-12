# Security

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's
private vulnerability reporting on this repository (**Security → Report a
vulnerability**). You will get an acknowledgement within a few days; fixes ship
as a patch release and are noted under *Security* in `CHANGELOG.md`.

Only the latest release is supported.

## What this server handles

The server runs locally, as a child process of Claude, and talks only to
`linkedin.com` / `api.linkedin.com`. It holds three classes of secrets:

| Secret | Where it lives | Fallback |
|---|---|---|
| LinkedIn app Client ID + Secret | OS keychain (`python -m linkedin_mcp setup`) | environment variables / `.env` (no plaintext file is ever written by the server) |
| OAuth access token (per alias) | OS keychain | `~/.linkedin_mcp_token_<alias>.json`, mode `0600` from creation |
| Web session cookies `li_at` + `JSESSIONID` (per alias) | OS keychain | `~/.linkedin_mcp_session_<alias>.json`, mode `0600` from creation |

In addition, the Playwright profile `~/.linkedin_mcp_browser_<alias>/` contains
the live browser session (equivalent to being logged in to LinkedIn). It is
created `0700` and re-tightened every time it is opened. The log file
`~/.linkedin_mcp.log` and its rotated backups are `0600`.

The `0600` / `0700` guarantees are POSIX (macOS, Linux). On Windows, Python's
`os.chmod` cannot set an owner-only ACL; there the protection is whatever the
per-user location under your profile directory gives you.

Things the project deliberately does **not** do:

- Pass the client secret to `claude mcp add --env` / `-e` (it would be stored in
  plaintext in Claude's config). The installers register the server without
  credentials and point you to the keychain setup.
- Log secret values. Tokens and cookies are never written to the log.
- Rewrite git history. If a file that should not have been tracked is found
  (as with `.claude/settings.local.json`), it is untracked and the fact is
  recorded in the changelog.

The OAuth callback listens on `127.0.0.1` only, uses a random `state`
parameter compared in constant time, and shuts down after one request.

## Voyager tier — read this before enabling it

The **Voyager** tier drives LinkedIn's *unofficial, internal* web API — the one
`linkedin.com` itself uses — authenticated with your own browser session
cookies. It is what unlocks full profile sections, notifications and
conversations, none of which the official Consumer API exposes.

- This is **not a supported LinkedIn developer surface**. Automated access to
  LinkedIn with your session is prohibited by LinkedIn's
  [User Agreement](https://www.linkedin.com/legal/user-agreement), and LinkedIn
  actively detects it.
- Consequences can include warnings, temporary restrictions (e.g. forced
  re-verification, rate limits) or **suspension of your LinkedIn account**.
- Voyager is **off unless a web session exists** for the active alias. If no
  `li_at` cookie has been captured, no request touches Voyager and the tools
  that need it fall back to the official API or report the missing session.
  (A captured cookie that LinkedIn has since expired is still sent once and
  rejected; the server does not pre-validate cookies.) `clear_web_session` switches it off: it deletes the
  stored cookies **and** the per-alias browser profile (the server would
  otherwise re-harvest the session from the still-logged-in profile on the
  next call). Voyager stays off until you run `authenticate` again.
- Use it only on your own account, at your own risk, and keep request volume
  low. The server does not add retries or parallelism on Voyager endpoints
  beyond what a normal browsing session would generate.

## Install-time integrity

`scripts/install.sh` downloads `linkedin-mcp.plugin` from a GitHub Release and
verifies it against the `linkedin-mcp.plugin.sha256` published alongside it
(releases that predate the checksum produce a warning instead). It also refuses
to wipe `$HOME`, `/`, or any non-empty directory that is not a previous
linkedin-mcp install.

## Dependencies

`mcp/uv.lock` pins every dependency. The project constrains `mcp`,
`cryptography`, `starlette`, `pydantic-settings` and `python-multipart` to
versions past their known advisories; `uvx pip-audit` against the exported
lock is part of the release checklist.
