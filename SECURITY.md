# Security

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's
private vulnerability reporting on this repository (**Security → Report a
vulnerability**). You will get an acknowledgement within a few days; fixes ship
as a patch release and are noted under *Security* in the
[changelog](https://github.com/PassoNova/mcp-linkedin-manager/blob/main/CHANGELOG.md).

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

## Known residual risks

Accepted for now; each is bounded and documented so nobody has to rediscover it.

- **Profile lifecycle during a long login.** `authenticate` owns the per-alias
  Playwright profile for as long as the login window is open (minutes), and it
  cannot hold the alias lock for that time. A `clear_web_session` / `logout`
  issued meanwhile is honoured — the in-flight login persists nothing, the
  profile it wrote is removed when the flow ends, and recovery from that
  profile is refused until a session is stored again on purpose — but until
  the flow ends the on-disk profile can hold a live session, and Chromium can
  re-create the directory. Likewise a Voyager request already executing when a
  clear runs may fail rather than be waited for: there is no per-request lease
  on the profile. The security outcome (Voyager stays off after a clear) holds;
  the availability outcome (that request succeeding) does not.
- **Fallback-file permissions are fixed lazily.** The `0600` / `0700` modes are
  re-applied whenever the server loads, saves, or opens a file or profile, not
  by a background sweep: a file the server has not touched since an upgrade
  keeps its old mode until the next call. On a filesystem that rejects `chmod`
  the server refuses the file (fail closed) rather than using it; fix the
  mode by hand or delete the file. All of this is POSIX-only (see above for
  Windows).
- **Expired cookies are sent once.** A captured `li_at` that LinkedIn has since
  expired is not pre-validated; the first Voyager call sends it and is
  rejected, after which the tool reports the missing session.
- **`refresh_web_session` trusts the profile.** It is an explicit user action
  and harvests whatever session the profile contains, even after a clear.
- **Pre-marker installs.** `scripts/install.sh` will not delete an install
  made before the `.linkedin-mcp-install` marker existed; remove it by hand
  once, as the error message says.
- **`install.sh` emptiness test.** The "is the target directory empty?" check
  parses `ls -A`; a directory whose only entries are names made solely of
  newline characters reads as empty and would be replaced. Such names do not
  occur in practice and the marker/git/`$HOME`/`/` guards still apply.

- **`set_web_session` validates outside the lifecycle lock.** The standalone validation client opens the profile after the generation snapshot; a clear or logout during those seconds can remove the profile underneath it. The later generation check prevents the save; the validation itself may fail and is simply re-run. Accepted.
- **Profile ownership across overlapping logins.** A stale `authenticate` that observes a
  lifecycle-generation bump removes the alias's profile; if a newer `authenticate` for the
  same alias started in between, that cleanup can remove the newer login's profile and the
  user simply re-runs `authenticate`. Flows are not leased per directory. Impact: one extra
  login on a single-user, local tool. Accepted.
- **Clear tombstone is process-local.** `_cleared_pending` does not survive a server
  restart, so a login that recreated the profile after a clear can be harvested by the next
  `_get_voyager_client` once the process restarts. Run `clear_web_session` (or `logout`)
  again after a restart if a clear raced a login. Accepted; a persisted tombstone is tracked
  as future work.
- **Installer replace is not atomic.** `scripts/install.sh` validates the target directory
  and then removes it by path; a parent directory or mount swapped between the two steps by
  another local process could redirect the removal. The installer already refuses `/`,
  `$HOME`, git checkouts and unmarked non-empty directories; it does not defend against a
  hostile local user with write access to the parent. Accepted for a user-run installer.
