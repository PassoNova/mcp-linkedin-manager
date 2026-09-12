"""
LinkedIn MCP Server
===================
A Model Context Protocol server that lets Claude manage your LinkedIn
presence through the official LinkedIn REST API v2.

Tools exposed
─────────────
  authenticate          – OAuth 2.0 browser flow for a named alias (e.g. 'work')
  logout                – Remove one user's credentials (defaults to active)
  check_auth            – Show active user's token status and capability tier
  switch_user           – Set the active LinkedIn account by alias
  list_users            – List all registered accounts with their auth status
  get_profile           – Name, headline, profile URL, email
  update_headline       – Change your profile headline
  create_post           – Publish a new text post
  get_posts             – List your recent posts
  delete_post           – Remove a post by URN
  get_api_capabilities  – Explain what the standard API can/cannot do
  get_community_stats   – Connection count (partner scope; graceful fallback)

Capability tiers
────────────────
  OAUTH    — valid token: get_profile, create_post, get_posts, delete_post
  VOYAGER  — valid token + browser session: all of the above PLUS
             get_full_profile, get_notifications, get_conversations,
             get_recent_activity, update_headline

Logs
────
  All tool calls are logged to ~/.linkedin_mcp.log with timing and outcome.
  Tail it for live debugging: tail -f ~/.linkedin_mcp.log
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager
from textwrap import dedent
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import log_config
log_config.setup()

from auth import (
    DEFAULT_PORT,
    _HAS_KEYRING,
    _browser_dir,
    deregister_alias,
    delete_credentials,
    delete_token,
    delete_web_session,
    get_active_alias,
    harvest_session_from_profile,
    has_browser_profile,
    is_token_expired,
    load_credentials,
    load_token,
    load_user_registry,
    load_web_session,
    register_alias,
    run_oauth_flow,
    save_credentials,
    save_token,
    save_web_session,
    set_active_alias,
    validate_alias,
    _run_in_thread,
)
from client import LinkedInClient, VoyagerClient

# ── Bootstrap ──────────────────────────────────────────────────────────────────

load_dotenv()

_log = logging.getLogger("linkedin_mcp.server")


def _git_version() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


_SERVER_VERSION = _git_version()

mcp = FastMCP(
    "LinkedIn Manager",
    instructions=dedent("""
        This server manages your LinkedIn profile and content via the
        official LinkedIn REST API.  Use `check_auth` first to see
        whether you're already authenticated, then `get_profile` to
        confirm your identity before making any changes.
    """).strip(),
)


# ── Logging helpers ────────────────────────────────────────────────────────────

@contextmanager
def _tool_log(name: str, **ctx):
    """Context manager that logs entry, exit, timing, and errors for a tool call."""
    start = time.monotonic()
    ctx_str = " ".join(f"{k}={v!r}" for k, v in ctx.items()) if ctx else ""
    _log.info("TOOL %s START %s", name, ctx_str)
    try:
        yield
        elapsed = time.monotonic() - start
        _log.info("TOOL %s OK (%.2fs)", name, elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - start
        _log.error("TOOL %s ERROR (%.2fs): %s", name, elapsed, exc)
        raise


# ── Internal helpers ───────────────────────────────────────────────────────────

def _credentials() -> tuple[str, str]:
    creds = load_credentials()
    if creds:
        return creds["client_id"], creds["client_secret"]
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "LinkedIn app credentials not found. Run `python -m linkedin_mcp setup` "
            "to save them to the OS keychain, or set LINKEDIN_CLIENT_ID and "
            "LINKEDIN_CLIENT_SECRET in your environment or .env file."
        )
    save_credentials(client_id, client_secret)
    return client_id, client_secret


def _active_alias() -> str:
    alias = get_active_alias()
    if not alias:
        raise RuntimeError(
            "No active user. Run `authenticate` with an alias (e.g. 'work') first."
        )
    return alias


def _get_client(alias: Optional[str] = None) -> LinkedInClient:
    """Return an authenticated LinkedInClient for alias (defaults to active user)."""
    alias = alias or _active_alias()
    token_data = load_token(alias)
    if not token_data:
        raise RuntimeError(
            f"Not authenticated as '{alias}'. Run `authenticate` with this alias."
        )
    if is_token_expired(token_data):
        raise RuntimeError(
            f"Token for '{alias}' has expired. Run `authenticate` again."
        )
    return LinkedInClient(token_data["access_token"])


_voyager_singletons: dict[str, VoyagerClient] = {}
_voyager_session_keys: dict[str, str] = {}
# Per-alias lifecycle locks: serialise singleton creation/eviction, session
# persistence and profile removal for one account, so a concurrent tool call
# cannot re-open (or re-create) a profile while it is being deleted — without a
# slow profile for one alias blocking every other alias.
_alias_locks: dict[str, threading.RLock] = {}
_alias_locks_guard = threading.Lock()
# Per-alias lifecycle generation: bumped by clear_web_session / logout. Flows
# that own the profile for a while (OAuth login, set_web_session validation)
# capture it first and only persist their cookies if it is unchanged, so a
# clear that happened mid-flight cannot be undone by a late save.
_session_generation: dict[str, int] = {}
# Aliases cleared/logged out since the last persisted session. While an alias
# is here, profile recovery is refused: a profile can only exist because a flow
# that was in progress during the clear (re)created it, and its cookies must
# not silently re-enable Voyager. Persisting a session again lifts it.
_cleared_pending: set[str] = set()


def _alias_lock(alias: str) -> threading.RLock:
    with _alias_locks_guard:
        lock = _alias_locks.get(alias)
        if lock is None:
            lock = _alias_locks[alias] = threading.RLock()
        return lock


def _lifecycle_generation(alias: str) -> int:
    with _alias_lock(alias):
        return _session_generation.get(alias, 0)


def _bump_generation(alias: str) -> None:
    """Mark the alias as cleared: invalidates in-flight saves and blocks profile recovery."""
    with _alias_lock(alias):
        _session_generation[alias] = _session_generation.get(alias, 0) + 1
        _cleared_pending.add(alias)


def _session_persisted(alias: str) -> None:
    """A session was (re)stored on purpose; profile recovery is allowed again."""
    with _alias_lock(alias):
        _cleared_pending.discard(alias)


def _save_session_if_current(li_at: str, jsessionid: str, alias: str, generation: int) -> bool:
    """Persist the web session unless the alias was cleared since *generation* was read."""
    with _alias_lock(alias):
        if _session_generation.get(alias, 0) != generation:
            _log.warning(
                "Web session for '%s' was cleared while a login/validation was in progress; discarding it",
                alias,
            )
            return False
        save_web_session(li_at, jsessionid, alias)
        _session_persisted(alias)
        return True


def _remove_browser_profile(alias: str) -> bool:
    """Delete the alias's persistent Playwright profile. Returns True if one was removed.

    The alias comes from the persisted registry, so it is re-validated here and
    the path must be a real directory (not a symlink) before ``rmtree`` runs.
    """
    try:
        validate_alias(alias)
    except ValueError as exc:
        raise ValueError(f"refusing to remove a browser profile for invalid alias {alias!r}: {exc}") from exc
    bdir = _browser_dir(alias)
    if os.path.islink(bdir):
        raise ValueError(f"refusing to remove {bdir}: it is a symlink")
    if not os.path.isdir(bdir):
        return False
    shutil.rmtree(bdir)
    _log.info("Browser profile removed for '%s' (%s)", alias, bdir)
    return True


def _recover_session_from_profile(alias: str) -> Optional[dict]:
    """Re-read the LinkedIn session from the alias's persistent Playwright profile (locked)."""
    with _alias_lock(alias):
        return _recover_session_from_profile_unlocked(alias)


def _recover_session_from_profile_unlocked(alias: str) -> Optional[dict]:
    """Re-read the LinkedIn session from the alias's persistent Playwright profile.

    Runs when no web session is saved but a profile exists (e.g. the login
    happened inside the Playwright window but the cookies were never stored,
    or the session entry was cleared). Persists what it finds so the next call
    is a plain keychain read. Returns the session dict or None.
    """
    if alias in _cleared_pending:
        _log.info("Not recovering a session for '%s': it was cleared and not re-authenticated", alias)
        return None
    bdir = _browser_dir(alias)
    if not has_browser_profile(bdir):
        return None
    _invalidate_voyager(alias)  # Chromium locks the profile; make sure it is free
    _log.info("No web session for '%s'; trying to recover it from %s", alias, bdir)
    li_at, jsessionid, err = _run_in_thread(harvest_session_from_profile, bdir)
    if not li_at:
        _log.info("Session recovery for '%s' failed: %s", alias, err)
        return None
    save_web_session(li_at, jsessionid or "", alias)
    return load_web_session(alias)


def _get_voyager_client(alias: Optional[str] = None) -> Optional[VoyagerClient]:
    """Return a reusable VoyagerClient for alias, creating one only when session changes."""
    alias = alias or _active_alias()
    with _alias_lock(alias):
        return _get_voyager_client_unlocked(alias)


def _get_voyager_client_unlocked(alias: Optional[str] = None) -> Optional[VoyagerClient]:
    alias = alias or _active_alias()
    session = load_web_session(alias)
    if not session:
        session = _recover_session_from_profile(alias)
    if not session:
        _log.debug("No web session for '%s'; Voyager unavailable", alias)
        return None
    key = session.get("li_at", "")
    if alias not in _voyager_singletons or key != _voyager_session_keys.get(alias):
        if alias in _voyager_singletons:
            _log.debug("Session changed for '%s'; recycling VoyagerClient", alias)
            _voyager_singletons[alias].close()
        bdir = _browser_dir(alias)
        udd = bdir if has_browser_profile(bdir) else None
        _log.debug(
            "Creating VoyagerClient for '%s' (browser_dir=%s)",
            alias,
            udd or "none — will navigate without persistent profile",
        )
        _voyager_singletons[alias] = VoyagerClient(
            session["li_at"], session["jsessionid"], user_data_dir=udd
        )
        _voyager_session_keys[alias] = key
    return _voyager_singletons[alias]


def _invalidate_voyager(alias: Optional[str] = None) -> None:
    """Close and evict Voyager singleton(s). Pass alias to target one; None clears all."""
    if alias is None:
        for known in list(_voyager_singletons):
            with _alias_lock(known):
                _invalidate_voyager_unlocked(known)
        return
    with _alias_lock(alias):
        _invalidate_voyager_unlocked(alias)


def _invalidate_voyager_unlocked(alias: Optional[str] = None) -> None:
    if alias is not None:
        if alias in _voyager_singletons:
            _log.debug("Invalidating VoyagerClient for '%s'", alias)
            _voyager_singletons[alias].close()
            del _voyager_singletons[alias]
            _voyager_session_keys.pop(alias, None)
    else:
        for vc in _voyager_singletons.values():
            vc.close()
        _voyager_singletons.clear()
        _voyager_session_keys.clear()
        _log.debug("All VoyagerClient singletons invalidated")


def _format_error(exc: Exception) -> str:
    """Turn an exception into a user-friendly error string."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text[:400]

        if status == 401:
            return (
                "❌ LinkedIn returned 401 Unauthorized. "
                "Your token may have expired — run `authenticate` again."
            )
        if status == 403:
            return (
                f"❌ LinkedIn returned 403 Forbidden. "
                f"This action requires a scope or partner-program access that "
                f"your app doesn't have.\nDetails: {json.dumps(detail, indent=2)}"
            )
        return f"❌ LinkedIn API error {status}:\n{json.dumps(detail, indent=2)}"
    return f"❌ {type(exc).__name__}: {exc}"


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
async def authenticate(alias: str) -> str:
    """
    Start the LinkedIn OAuth 2.0 flow for a named account.

    Opens your browser to LinkedIn's authorization page, then saves the
    token and Voyager session under the given alias. Use `switch_user` to
    change the active account, and `list_users` to see all registered accounts.

    Args:
        alias: Short name for this account (e.g. 'work', 'personal').
               Allowed characters: letters, digits, hyphens, underscores.
               Max 32 characters. Re-authenticating an existing alias replaces
               its tokens.

    Before calling this, app credentials must be available — run
    `python -m linkedin_mcp setup` or set LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET.
    """
    with _tool_log("authenticate", alias=alias):
        try:
            validate_alias(alias)
            client_id, client_secret = _credentials()
            # Release the persistent profile before the login window opens on it.
            with _alias_lock(alias):
                # Release the profile and snapshot the generation under one lock, so a
                # clear/logout cannot slip between the two and be missed later.
                _invalidate_voyager_unlocked(alias)
                generation = _session_generation.get(alias, 0)

            try:
                result = await run_oauth_flow(
                    client_id, client_secret, port=DEFAULT_PORT, browser_dir=_browser_dir(alias)
                )
            except Exception:
                # The flow failed (timeout, closed window, denied consent) but may still
                # have written cookies into the profile. If a clear/logout happened while
                # the window was open, that profile must not survive the clear.
                with _alias_lock(alias):
                    if _session_generation.get(alias, 0) != generation:
                        _remove_browser_profile(alias)
                        _log.warning(
                            "authenticate('%s') failed after a mid-flight clear; profile removed", alias
                        )
                raise
            with _alias_lock(alias):
                if _lifecycle_generation(alias) != generation:
                    # clear_web_session / logout ran while the window was open: persist
                    # nothing (token, registry, cookies) and drop whatever the flow
                    # wrote into the profile in the meantime.
                    _remove_browser_profile(alias)
                    _log.warning("authenticate('%s') discarded: cleared/logged out mid-flight", alias)
                    return (
                        f"❌ Login discarded: '{alias}' was cleared or logged out while the "
                        "login window was open. Run `authenticate` again."
                    )
                save_token(result.token_data, alias)
                register_alias(alias)

            scopes = result.token_data.get("scope", "unknown")
            expires_in = result.token_data.get("expires_in", "unknown")
            via = (
                "Playwright login window"
                if result.method == "playwright"
                else "system browser + Chrome cookie store"
            )

            if result.li_at and not _save_session_if_current(
                result.li_at, result.jsessionid or "", alias, generation
            ):
                # clear_web_session / logout ran while the window was open.
                result = result._replace(
                    li_at=None,
                    jsessionid=None,
                    session_error="the web session was cleared while the login was in progress",
                )
            with _alias_lock(alias):
                # Final check after every write. Any generation change means a clear or a
                # logout completed while the login was in progress: the web session it
                # captured is gone. A logout has also deleted the token and deregistered
                # the alias, so nothing persisted and reporting success would be a lie;
                # a plain clear keeps the OAuth token and only loses the web session.
                if _session_generation.get(alias, 0) != generation:
                    # A logout that failed halfway can leave the alias registered with
                    # the token already gone; treat a missing token like a logout too.
                    if (
                        alias not in load_user_registry().get("aliases", [])
                        or load_token(alias) is None
                    ):
                        _remove_browser_profile(alias)
                        delete_token(alias)
                        _log.warning("authenticate('%s') discarded: logged out mid-flight", alias)
                        return (
                            f"❌ Login discarded: '{alias}' was logged out while the login was in "
                            "progress, so nothing was saved. Run `authenticate` again."
                        )
                    result = result._replace(
                        li_at=None,
                        jsessionid=None,
                        session_error="the web session was cleared while the login was in progress",
                    )
            if result.li_at:
                tier_note = (
                    "Voyager API enabled"
                    if result.jsessionid
                    else "Voyager API enabled (JSESSIONID will be refreshed on first use)"
                )
                session_note = f"   Web session    : captured via {via} ({tier_note})\n"
                _log.info(
                    "Authentication complete for '%s': tier=VOYAGER method=%s scopes=%s",
                    alias, result.method, scopes,
                )
            else:
                reason = result.session_error or "unknown"
                session_note = (
                    f"   Web session    : not captured via {via} ({reason})\n"
                    "                    Voyager tools are unavailable until a session exists. Options:\n"
                    "                    - run `authenticate` again (a Playwright login window is used when its\n"
                    "                      Chromium can reach linkedin.com; `playwright install chromium` if missing)\n"
                    "                    - run `refresh_web_session` if you have since logged in inside that window\n"
                    "                    - run `set_web_session` with cookies copied from your browser\n"
                )
                _log.info(
                    "Authentication complete for '%s': tier=OAUTH method=%s scopes=%s session_err=%s",
                    alias, result.method, scopes, reason,
                )

            return (
                f"✅ Authenticated as '{alias}'!\n"
                f"   Scopes granted : {scopes}\n"
                f"   Expires in     : {expires_in} seconds\n"
                f"{session_note}\n"
                f"'{alias}' is now the active account. Use `switch_user` to change accounts."
            )
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def logout(alias: str = "") -> str:
    """
    Remove a user's credentials — OAuth token, web session cookies and the
    logged-in browser profile — and unregister the alias.

    Args:
        alias: Which account to log out. Leave empty to log out the active account.
    """
    with _tool_log("logout", alias=alias or "(active)"):
        try:
            target = alias.strip() or _active_alias()
            with _alias_lock(target):
                _bump_generation(target)  # any in-flight login/validation must not save
                _invalidate_voyager(target)  # release Chromium's lock on the profile first
                _remove_browser_profile(target)
                try:
                    # strict: keychain failures raise instead of being swallowed, so
                    # the alias is only deregistered once both entries are gone.
                    delete_token(target, strict=True)
                    delete_web_session(target, strict=True)
                except RuntimeError as exc:
                    _log.warning("logout: %s", exc)
                    return (
                        f"❌ '{target}' NOT logged out: {exc}. The alias stays registered; "
                        f"fix the keychain (or delete the `linkedin-mcp` entries for "
                        f"'{target}' manually) and run `logout` again."
                    )
                deregister_alias(target)  # inside the lock: no authenticate can cross this boundary
                # Retire the clear tombstone: the profile and session are gone, so there
                # is nothing left to block recovery of. The generation counter and the
                # alias lock are kept on purpose: an in-flight authenticate compares
                # against the counter, and dropping it back to 0 could hide this logout.
                _cleared_pending.discard(target)
            _log.info("Logged out '%s'", target)
            return f"✅ '{target}' logged out and removed."
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def check_auth() -> str:
    """
    Check the active user's authentication status.

    Returns token metadata (alias, scopes, expiry, capability tier) without
    making an API call.

    Tier values:
      BASE    — no valid token (not authenticated or expired)
      OAUTH   — valid token, OAuth tools available
      VOYAGER — valid token + browser session, all tools available
    """
    with _tool_log("check_auth"):
        alias = get_active_alias()
        credentials_in_keychain = load_credentials() is not None

        if not alias:
            return json.dumps(
                {
                    "authenticated": False,
                    "active_user": None,
                    "tier": "BASE",
                    "keychain_available": _HAS_KEYRING,
                    "credentials_in_keychain": credentials_in_keychain,
                    "message": "No users registered. Run `authenticate` with an alias.",
                    "server_version": _SERVER_VERSION,
                },
                indent=2,
            )

        token_data = load_token(alias)

        if not token_data:
            return json.dumps(
                {
                    "authenticated": False,
                    "active_user": alias,
                    "tier": "BASE",
                    "keychain_available": _HAS_KEYRING,
                    "credentials_in_keychain": credentials_in_keychain,
                    "message": f"No token for '{alias}'. Run `authenticate` again.",
                    "server_version": _SERVER_VERSION,
                },
                indent=2,
            )

        obtained_at = token_data.get("_obtained_at", 0)
        expires_in_secs = token_data.get("expires_in", 0)
        expired = is_token_expired(token_data)
        expiry_ts = obtained_at + expires_in_secs if (obtained_at and expires_in_secs) else None

        if expired:
            tier = "BASE"
        elif load_web_session(alias):
            tier = "VOYAGER"
        else:
            tier = "OAUTH"

        _log.debug("check_auth: alias=%s tier=%s expired=%s", alias, tier, expired)
        return json.dumps(
            {
                "active_user": alias,
                "authenticated": True,
                "expired": expired,
                "tier": tier,
                "scopes": token_data.get("scope", "unknown"),
                "expires_at": (
                    time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(expiry_ts))
                    if expiry_ts
                    else "unknown"
                ),
                "keychain_available": _HAS_KEYRING,
                "credentials_in_keychain": credentials_in_keychain,
                "server_version": _SERVER_VERSION,
            },
            indent=2,
        )


@mcp.tool()
def get_profile() -> str:
    """
    Retrieve your LinkedIn profile information.

    Returns: id, full name, headline, public profile URL, email address,
    and a direct link to your LinkedIn profile.

    Fields like experience, education, and certifications are NOT available
    through the standard Consumer API — see `get_api_capabilities` for details.
    """
    with _tool_log("get_profile"):
        try:
            client = _get_client()
            info = client.get_userinfo()

            person_id = info.get("sub", "")
            name = info.get("name") or f"{info.get('given_name', '')} {info.get('family_name', '')}".strip()
            picture = info.get("picture", "")
            email = info.get("email", "N/A")
            locale = info.get("locale", {})

            headline = ""
            vanity = ""
            source = "oauth"

            voyager = _get_voyager_client()
            if voyager:
                try:
                    vme = voyager.get_me()
                    headline = vme.get("headline", "")
                    vanity = vme.get("public_id", "")
                    if vme.get("picture_url"):
                        picture = vme["picture_url"]
                    source = "voyager"
                    _log.debug("get_profile: enriched with Voyager data (public_id=%s)", vanity)
                except Exception as vex:
                    _log.warning("get_profile: Voyager enrichment failed: %s", vex)

            if not headline and source == "oauth":
                try:
                    me = client.get_profile()
                    hl = me.get("headline", {})
                    if isinstance(hl, dict):
                        localized = hl.get("localized", {})
                        headline = list(localized.values())[0] if localized else ""
                    elif isinstance(hl, str):
                        headline = hl
                    vanity = me.get("vanityName", "")
                except Exception:
                    headline = ""

            profile_url = (
                f"https://www.linkedin.com/in/{vanity}" if vanity
                else "https://www.linkedin.com/in/~"
            )

            result = {
                "id": person_id,
                "name": name,
                "headline": headline,
                "email": email,
                "profile_url": profile_url,
                "picture_url": picture,
                "locale": locale,
                "person_urn": f"urn:li:person:{person_id}",
                "headline_source": source,
            }
            return json.dumps(result, indent=2)
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def update_headline(
    headline: str,
    locale: str = "en_US",
) -> str:
    """
    Update your LinkedIn profile headline.

    Args:
        headline: Your new headline text (max 220 characters).
        locale:   Locale string, e.g. "en_US", "es_ES" (default: en_US).

    Note: This requires the `rw_me` OAuth scope. Standard Consumer apps may
    receive a 403 response — in that case LinkedIn's partner program is needed
    for write access to profile fields.
    """
    if len(headline) > 220:
        return f"❌ Headline is {len(headline)} characters. LinkedIn allows a maximum of 220."

    with _tool_log("update_headline", chars=len(headline)):
        voyager = _get_voyager_client()
        if voyager:
            try:
                vme = voyager.get_me()
                public_id = vme.get("public_id", "")
                if not public_id:
                    return "❌ Could not determine your LinkedIn public ID from the web session."
                voyager.update_headline(headline, public_id)
                _log.info("Headline updated via Voyager for public_id=%s", public_id)
                return f"✅ Headline updated via web session:\n  \"{headline}\""
            except Exception as exc:
                _log.warning("Voyager headline update failed: %s", exc)
                return (
                    f"⚠️  Voyager update failed: {_format_error(exc)}\n"
                    "Falling back to OAuth API..."
                ) + _try_oauth_headline(headline, locale)

        return _try_oauth_headline(headline, locale)


def _try_oauth_headline(headline: str, locale: str) -> str:
    try:
        client = _get_client()
        client.update_headline(headline, locale=locale)
        _log.info("Headline updated via OAuth API")
        return f"✅ Headline updated via OAuth:\n  \"{headline}\""
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
def set_web_session(li_at: str, jsessionid: str) -> str:
    """
    Store LinkedIn browser session cookies for Voyager API access.

    This unlocks full profile read/write (headline, and more) without needing
    LinkedIn partner-program approval, by using the same internal API as
    LinkedIn's own web app.

    How to get your cookies (one-time setup, cookies last ~1 year):
      1. Open linkedin.com in your browser and make sure you are logged in.
      2. Open DevTools → Application (Chrome) or Storage (Firefox) → Cookies
         → https://www.linkedin.com
      3. Copy the value of:
           li_at       — a long alphanumeric string
           JSESSIONID  — looks like "ajax:1234567890123456789" (copy with or without quotes)
      4. Pass both values to this tool.

    Args:
        li_at:      Value of the li_at cookie from linkedin.com.
        jsessionid: Value of the JSESSIONID cookie from linkedin.com.
    """
    li_at = li_at.strip()
    jsessionid = jsessionid.strip()
    if not li_at:
        return "❌ li_at is required."

    with _tool_log("set_web_session"):
        try:
            active = _active_alias()
            bdir = _browser_dir(active)
            with _alias_lock(active):
                # Release the profile (the validation client opens it) and snapshot the
                # generation under one lock, so an overlapping clear/logout is observed.
                _invalidate_voyager_unlocked(active)
                generation = _session_generation.get(active, 0)
            udd = bdir if has_browser_profile(bdir) else None
            _log.debug("set_web_session: validating session for '%s' (browser_dir=%s)", active, udd)
            vc = VoyagerClient(li_at, jsessionid, user_data_dir=udd)
            try:
                me = vc.get_me()
            finally:
                vc.close()
            name = f"{me.get('first_name', '')} {me.get('last_name', '')}".strip()
            headline = me.get("headline", "")
            with _alias_lock(active):
                # Save, failure cleanup and singleton invalidation in one lock scope, so a
                # concurrent Voyager request cannot create a client that is then closed
                # underneath it between the save and the invalidation.
                if not _save_session_if_current(li_at, jsessionid, active, generation):
                    _remove_browser_profile(active)  # validation may have re-created it
                    return (
                        f"❌ Not saved: the web session for '{active}' was cleared while it was "
                        "being validated. Run `set_web_session` again if you still want it."
                    )
                _invalidate_voyager_unlocked(active)
            _log.info("Web session saved for '%s' (verified as %s)", active, name)
            return (
                f"✅ Web session saved for '{active}'\n"
                f"   Verified as  : {name}\n"
                f"   Headline     : {headline or '(not set)'}\n\n"
                "get_profile and update_headline will now use the Voyager API automatically."
            )
        except Exception as exc:
            _log.error("set_web_session failed: %s", exc)
            return (
                f"❌ Session validation failed — browser profile may not have a valid LinkedIn session.\n"
                f"   {_format_error(exc)}\n\n"
                "Run `authenticate` first to set up the browser profile, then try again."
            )


@mcp.tool()
def refresh_web_session() -> str:
    """
    Re-read the LinkedIn session cookies from the persistent browser profile.

    Use this when `check_auth` shows tier OAUTH even though you logged in
    inside the Playwright login window, or after LinkedIn rotated JSESSIONID.
    No browser interaction is needed: the profile at
    ~/.linkedin_mcp_browser_<alias>/ is opened headlessly and its cookies are
    stored as the active account's web session.
    """
    with _tool_log("refresh_web_session"):
        try:
            active = _active_alias()
            bdir = _browser_dir(active)
            if not has_browser_profile(bdir):
                return (
                    f"❌ No browser profile for '{active}'. Run `authenticate` first "
                    "(the Playwright login window creates it)."
                )
            with _alias_lock(active):  # never interleave with clear_web_session / logout
                _invalidate_voyager(active)  # Chromium locks the profile; free it first
                li_at, jsessionid, err = _run_in_thread(harvest_session_from_profile, bdir)
                if not li_at:
                    return (
                        f"❌ Could not recover a session from the browser profile ({err}).\n"
                        "   Run `authenticate` again and log in inside the window that opens, "
                        "or use `set_web_session`."
                    )
                save_web_session(li_at, jsessionid or "", active)
                _session_persisted(active)
            _log.info("Web session refreshed for '%s' from browser profile", active)
            return (
                f"✅ Web session refreshed for '{active}' from the browser profile.\n"
                f"   JSESSIONID   : {'present' if jsessionid else 'will be refreshed on first use'}\n\n"
                "Voyager tools (get_recent_activity, get_full_profile, ...) are enabled."
            )
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def clear_web_session() -> str:
    """
    Remove the stored LinkedIn browser session cookies AND the persistent
    browser profile for the active account, switching the Voyager tier off.

    Both must go: the profile is a logged-in browser, and the server would
    otherwise re-harvest the session from it on the next call. After clearing,
    get_profile and update_headline fall back to the OAuth API (which cannot
    read/write the headline without partner-level scopes) until you run
    `authenticate` again.
    """
    with _tool_log("clear_web_session"):
        try:
            active = _active_alias()
            with _alias_lock(active):
                _bump_generation(active)  # any in-flight login/validation must not save
                _invalidate_voyager(active)  # release Chromium's lock on the profile first
                # Profile first: if its removal fails, the stored session is left in
                # place so a logged-in profile is never left behind for recovery.
                profile_removed = _remove_browser_profile(active)
                try:
                    # strict: a keychain failure raises instead of being swallowed, so
                    # success is never reported while a live li_at survives there.
                    existed = delete_web_session(active, strict=True)
                except RuntimeError as exc:
                    _log.warning("clear_web_session: %s", exc)
                    return (
                        f"❌ Browser profile {'removed' if profile_removed else 'was not present'}, "
                        f"but the stored web session for '{active}' could not be removed from "
                        f"the OS keychain ({exc}). Delete the `linkedin-mcp / session:{active}` "
                        f"entry manually and re-run."
                    )
            if existed or profile_removed:
                _log.info("Web session cleared for '%s'", active)
                removed = " and ".join(
                    part for part, done in (("stored cookies removed", existed),
                                            ("browser profile removed", profile_removed)) if done
                )
                return (
                    f"✅ Web session cleared for '{active}' ({removed}). "
                    "Voyager stays off until you run `authenticate` again."
                )
            return f"ℹ️ No web session found for '{active}' — nothing to clear."
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def switch_user(alias: str) -> str:
    """
    Set the active LinkedIn account.

    All subsequent tool calls (get_profile, create_post, etc.) will operate
    on this account until you switch again.

    Args:
        alias: The alias to switch to. Must have been registered via authenticate.
    """
    with _tool_log("switch_user", alias=alias):
        try:
            set_active_alias(alias)
            _log.info("Active account switched to '%s'", alias)
            return f"✅ Switched to '{alias}'. All tools will now use this account."
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def list_users() -> str:
    """
    List all registered LinkedIn accounts with their authentication status.

    Shows which account is currently active and the capability tier
    (BASE / OAUTH / VOYAGER) for each.
    """
    with _tool_log("list_users"):
        reg = load_user_registry()
        active = reg.get("active")
        users = []
        for a in reg.get("aliases", []):
            token = load_token(a)
            expired = is_token_expired(token) if token else True
            has_session = load_web_session(a) is not None
            tier = (
                "VOYAGER" if (token and not expired and has_session) else
                "OAUTH" if (token and not expired) else
                "BASE"
            )
            users.append({
                "alias": a,
                "active": a == active,
                "tier": tier,
                "authenticated": bool(token and not expired),
            })
        return json.dumps({"active": active, "users": users}, indent=2)


@mcp.tool()
def clear_credentials() -> str:
    """
    Remove LinkedIn app credentials (Client ID + Secret) from the OS keychain.

    After clearing, run `python -m linkedin_mcp setup` or set
    LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in your environment
    before calling authenticate again.
    """
    with _tool_log("clear_credentials"):
        removed = delete_credentials()
        if removed:
            _log.info("App credentials removed from keychain")
            return (
                "✅ App credentials removed from the OS keychain.\n\n"
                "Run `python -m linkedin_mcp setup` or set LINKEDIN_CLIENT_ID and "
                "LINKEDIN_CLIENT_SECRET in your environment to re-add them."
            )
        return "ℹ️ No credentials found in the OS keychain — nothing to clear."


@mcp.tool()
def create_post(
    text: str,
    visibility: str = "PUBLIC",
) -> str:
    """
    Publish a new LinkedIn text post.

    Args:
        text:       The body of your post (max 3,000 characters).
        visibility: "PUBLIC" (everyone) or "CONNECTIONS" (1st-degree only).
                    Default: PUBLIC.

    Returns the URN of the created post, which you can pass to `delete_post`
    if needed.
    """
    text = text.strip()
    if not text:
        return "❌ Post text cannot be empty."
    if len(text) > 3000:
        return f"❌ Post is {len(text)} characters. LinkedIn allows a maximum of 3,000."

    visibility = visibility.upper()
    if visibility not in ("PUBLIC", "CONNECTIONS"):
        return "❌ visibility must be 'PUBLIC' or 'CONNECTIONS'."

    with _tool_log("create_post", chars=len(text), visibility=visibility):
        try:
            client = _get_client()
            person_urn = client.get_person_urn()
            result = client.create_post(text, visibility=visibility, person_urn=person_urn)

            post_urn = result.get("id", "unknown")
            _log.info("Post created: urn=%s visibility=%s", post_urn, visibility)
            return (
                f"✅ Post published successfully!\n"
                f"   URN        : {post_urn}\n"
                f"   Visibility : {visibility}\n"
                f"   Characters : {len(text)}\n\n"
                f"Save the URN above if you may want to delete this post later."
            )
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def get_recent_activity(count: int = 10) -> str:
    """
    List your recent LinkedIn posts via browser session (no partner scope needed).

    Scrapes your LinkedIn activity page — works even when the OAuth token
    lacks the r_member_social scope required by the official API.

    Args:
        count: Number of posts to retrieve (max 50, default 10).

    Requires a web session (run `authenticate` or `set_web_session` first).
    """
    count = max(1, min(count, 50))
    with _tool_log("get_recent_activity", count=count):
        try:
            voyager = _get_voyager_client()
            if not voyager:
                return "❌ Web session required. Run `authenticate` or `set_web_session` first."

            me = voyager.get_me()
            public_id = me.get("public_id", "")
            if not public_id:
                return "❌ Could not determine your LinkedIn public ID."

            posts = voyager.get_recent_posts(public_id, count=count)
            _log.info("get_recent_activity: returned %d posts for %s", len(posts), public_id)
            return json.dumps(
                {"public_id": public_id, "total_returned": len(posts), "posts": posts},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def get_posts(count: int = 10) -> str:
    """
    List your recent LinkedIn posts.

    Args:
        count: Number of posts to retrieve. Min 1, max 50. Default: 10.

    Each post includes its URN, publication status, creation timestamp,
    visibility, and a preview of the text body.
    """
    count = max(1, min(count, 50))
    with _tool_log("get_posts", count=count):
        try:
            client = _get_client()
            person_urn = client.get_person_urn()
            elements = client.get_posts(count=count, person_urn=person_urn)

            posts = []
            for el in elements:
                text = el.get("commentary", "")
                if not text:
                    content = el.get("specificContent", {}).get(
                        "com.linkedin.ugc.ShareContent", {}
                    )
                    text = content.get("shareCommentary", {}).get("text", "")

                vis = el.get("visibility", "")
                if isinstance(vis, dict):
                    vis = vis.get("com.linkedin.ugc.MemberNetworkVisibility", "UNKNOWN")
                vis = vis or "UNKNOWN"

                created_ms = el.get("publishedAt") or el.get("lastModifiedAt", 0)
                if not created_ms:
                    created_ms = el.get("created", {}).get("time", 0) if isinstance(el.get("created"), dict) else 0
                created_str = (
                    time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(created_ms / 1000))
                    if created_ms
                    else "unknown"
                )

                posts.append(
                    {
                        "urn": el.get("id", ""),
                        "status": el.get("lifecycleState", ""),
                        "visibility": vis,
                        "created_at": created_str,
                        "text_preview": (text[:300] + "…") if len(text) > 300 else text,
                        "char_count": len(text),
                    }
                )

            _log.info("get_posts (OAuth): returned %d posts", len(posts))
            return json.dumps({"total_returned": len(posts), "posts": posts}, indent=2)
        except Exception as exc:
            _OAUTH_FALLBACK_CODES = {403, 426}
            if not (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _OAUTH_FALLBACK_CODES):
                return _format_error(exc)
            _oauth_exc = exc

        # OAuth lacks r_member_social scope (or version mismatch) — fall back to Voyager.
        _log.info("get_posts: OAuth returned %s; falling back to Voyager", _oauth_exc)
        try:
            voyager = _get_voyager_client()
            if not voyager:
                return (
                    "❌ OAuth API is unavailable (scope or version issue) and no web session is set. "
                    "Run `authenticate` or `set_web_session` to enable Voyager fallback."
                )
            me = voyager.get_me()
            public_id = me.get("public_id", "")
            if not public_id:
                return "❌ Could not determine your LinkedIn public ID for Voyager fallback."
            posts = voyager.get_recent_posts(public_id, count=count)
            _log.info("get_posts (Voyager fallback): returned %d posts", len(posts))
            return json.dumps(
                {"source": "voyager", "total_returned": len(posts), "posts": posts},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as exc2:
            return _format_error(exc2)


@mcp.tool()
def delete_post(post_urn: str) -> str:
    """
    Permanently delete one of your LinkedIn posts.

    Args:
        post_urn: The URN of the post to delete, e.g.
                  "urn:li:ugcPost:1234567890123456789".
                  Get it from `get_posts`.

    ⚠️  This action is irreversible.
    """
    post_urn = post_urn.strip()
    if not post_urn:
        return "❌ post_urn cannot be empty."

    with _tool_log("delete_post", urn=post_urn):
        try:
            client = _get_client()
            client.delete_post(post_urn)
            _log.info("Post deleted: %s", post_urn)
            return f"✅ Post deleted:\n   {post_urn}"
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def get_full_profile(public_id: str = "") -> str:
    """
    Fetch complete LinkedIn profile sections via browser automation.

    Returns about/summary, experience, education, and skills sections
    scraped directly from linkedin.com — no partner-program API access required.

    Args:
        public_id: LinkedIn vanity name (e.g. "john-doe").
                   Leave empty to fetch your own profile.

    Requires a web session (run `authenticate` first).
    """
    with _tool_log("get_full_profile", public_id=public_id or "(self)"):
        try:
            voyager = _get_voyager_client()
            if not voyager:
                return "❌ Web session required. Run `authenticate` first."

            if not public_id:
                me = voyager.get_me()
                public_id = me.get("public_id", "")
                if not public_id:
                    return "❌ Could not determine your LinkedIn public ID."

            sections = voyager.get_profile_sections(public_id)
            _log.info("get_full_profile: scraped %d sections for %s", len(sections), public_id)
            return json.dumps({"public_id": public_id, "sections": sections}, indent=2, ensure_ascii=False)
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def get_notifications(count: int = 20) -> str:
    """
    Fetch your recent LinkedIn notifications.

    Args:
        count: Number of notifications to retrieve (max 50, default 20).

    Requires a web session (run `authenticate` first).
    """
    count = max(1, min(count, 50))
    with _tool_log("get_notifications", count=count):
        try:
            voyager = _get_voyager_client()
            if not voyager:
                return "❌ Web session required. Run `authenticate` first."

            items = voyager.get_notifications(count=count)
            _log.info("get_notifications: returned %d items", len(items))
            return json.dumps(
                {"total_returned": len(items), "notifications": items},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def get_conversations(count: int = 20) -> str:
    """
    Fetch your recent LinkedIn direct message conversations.

    Args:
        count: Number of conversations to retrieve (default 20).

    Requires a web session (run `authenticate` first).
    """
    with _tool_log("get_conversations", count=count):
        try:
            voyager = _get_voyager_client()
            if not voyager:
                return "❌ Web session required. Run `authenticate` first."

            me = voyager.get_me()
            entity_urn = me.get("entity_urn", "")
            if not entity_urn:
                return "❌ Could not determine your LinkedIn entity URN."

            items = voyager.get_conversations(entity_urn, count=count)
            _log.info("get_conversations: returned %d conversations", len(items))
            return json.dumps(
                {"total_returned": len(items), "conversations": items},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as exc:
            return _format_error(exc)


@mcp.tool()
def get_api_capabilities() -> str:
    """
    Explain what the LinkedIn standard Consumer API can and cannot do.

    Call this before attempting profile-write or community operations to
    understand which features are available without partner-program access.
    """
    with _tool_log("get_api_capabilities"):
        alias = get_active_alias()
        token_data = load_token(alias) if alias else None
        scopes = token_data.get("scope", "unknown") if token_data else "not authenticated"

        capabilities = {
            "current_scopes": scopes,
            "oauth_api_available": {
                "get_profile": "✅ Name, headline, email, profile picture, person URN",
                "create_post": "✅ Publish text posts (PUBLIC or CONNECTIONS visibility)",
                "get_posts": "✅ List your recent posts with text preview",
                "delete_post": "✅ Remove a post you published",
                "update_headline": (
                    "⚠️  Requires `rw_me` scope — most standard apps receive 403. "
                    "Voyager (web session) is more reliable for this."
                ),
            },
            "voyager_available_after_authenticate": {
                "get_profile": "✅ Full headline via Voyager (no partner scope needed)",
                "update_headline": "✅ Works reliably via Voyager PATCH",
                "get_full_profile": "✅ About, experience, education, skills — scraped via browser DOM",
                "get_notifications": "✅ Recent notifications via Voyager REST endpoint",
                "get_conversations": "✅ Recent DM conversations via Voyager GraphQL",
            },
            "not_available": {
                "send_messages": "❌ w_messages scope not in Consumer API; Voyager write for messages not implemented",
                "send_connection_requests": "❌ w_connections scope is partner-gated",
                "reactions": "❌ Not exposed via Voyager without specific post URNs",
                "search_people": "❌ Voyager search GraphQL queryId not yet captured",
                "connections_list": "❌ Voyager connections GraphQL queryId not yet captured",
            },
            "linkedin_partner_program": "https://business.linkedin.com/marketing-solutions/partner-program",
        }

        return json.dumps(capabilities, indent=2)


@mcp.tool()
def get_community_stats() -> str:
    """
    Retrieve basic community statistics (connection count).

    Note: The r_network scope required for this is gated to LinkedIn partner
    apps. This tool will return a clear explanation if the scope isn't
    available rather than failing silently.
    """
    with _tool_log("get_community_stats"):
        try:
            client = _get_client()
            result = client.get_connections_count()
            return json.dumps(result, indent=2)
        except Exception as exc:
            return _format_error(exc)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _log.info("LinkedIn MCP server starting (version=%s)", _SERVER_VERSION)
    mcp.run()
