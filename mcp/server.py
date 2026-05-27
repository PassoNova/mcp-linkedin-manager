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

LinkedIn API limitations
────────────────────────
The standard Consumer API (no partner-program approval required) supports:
  ✅  Read basic profile (name, headline, photo, email)
  ✅  Create, read, and delete UGC posts
  ⚠️  Update headline (requires rw_me scope — may be rejected by LinkedIn)
  ❌  Read/write experience, education, certifications, skills
      → These require the restricted Profile API (LinkedIn partner program)
  ❌  Read connections list or send connection requests
      → Requires r_network / w_connections (partner-gated)

Usage
─────
  1. Copy .env.example → .env and fill in your credentials.
  2. pip install -r requirements.txt
  3. python server.py
  4. Add to Claude Code: claude mcp add linkedin-manager -- python /path/to/server.py
"""

from __future__ import annotations

import json
import os
import time
from textwrap import dedent
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from auth import (
    DEFAULT_PORT,
    deregister_alias,
    delete_credentials,
    delete_token,
    delete_web_session,
    get_active_alias,
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
    _browser_dir,
)
from client import LinkedInClient, VoyagerClient

# ── Bootstrap ──────────────────────────────────────────────────────────────────

load_dotenv()

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
    # Auto-migrate env-var credentials to keychain on first use.
    save_credentials(client_id, client_secret)
    return client_id, client_secret


def _active_alias() -> str:
    """Return the active alias or raise a helpful error."""
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


def _get_voyager_client(alias: Optional[str] = None) -> Optional[VoyagerClient]:
    """Return a reusable VoyagerClient for alias, creating one only when session changes."""
    alias = alias or _active_alias()
    session = load_web_session(alias)
    if not session:
        return None
    key = session.get("li_at", "")
    if alias not in _voyager_singletons or key != _voyager_session_keys.get(alias):
        if alias in _voyager_singletons:
            _voyager_singletons[alias].close()
        bdir = _browser_dir(alias)
        udd = bdir if has_browser_profile(bdir) else None
        _voyager_singletons[alias] = VoyagerClient(
            session["li_at"], session["jsessionid"], user_data_dir=udd
        )
        _voyager_session_keys[alias] = key
    return _voyager_singletons[alias]


def _invalidate_voyager(alias: Optional[str] = None) -> None:
    """Close and evict Voyager singleton(s). Pass alias to target one; None clears all."""
    if alias is not None:
        if alias in _voyager_singletons:
            _voyager_singletons[alias].close()
            del _voyager_singletons[alias]
            _voyager_session_keys.pop(alias, None)
    else:
        for vc in _voyager_singletons.values():
            vc.close()
        _voyager_singletons.clear()
        _voyager_session_keys.clear()


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
def authenticate(alias: str) -> str:
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
    try:
        validate_alias(alias)
        client_id, client_secret = _credentials()

        token_data, li_at, jsessionid = run_oauth_flow(
            client_id, client_secret, port=DEFAULT_PORT, browser_dir=_browser_dir(alias)
        )
        save_token(token_data, alias)
        register_alias(alias)
        _invalidate_voyager(alias)

        scopes = token_data.get("scope", "unknown")
        expires_in = token_data.get("expires_in", "unknown")

        if li_at and jsessionid:
            save_web_session(li_at, jsessionid, alias)
            session_note = "   Web session    : captured automatically (Voyager API enabled)\n"
        else:
            session_note = (
                "   Web session    : not captured — run `set_web_session` manually\n"
                "                    to enable full profile read/write via Voyager API\n"
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
    Remove a user's credentials and unregister the alias.

    Args:
        alias: Which account to log out. Leave empty to log out the active account.
    """
    try:
        target = alias.strip() or _active_alias()
        delete_token(target)
        delete_web_session(target)
        _invalidate_voyager(target)
        deregister_alias(target)
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
    from auth import _HAS_KEYRING
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
    try:
        client = _get_client()
        info = client.get_userinfo()  # most reliable with openid + profile + email

        person_id = info.get("sub", "")
        name = info.get("name") or f"{info.get('given_name', '')} {info.get('family_name', '')}".strip()
        picture = info.get("picture", "")
        email = info.get("email", "N/A")
        locale = info.get("locale", {})

        headline = ""
        vanity = ""
        source = "oauth"

        # Prefer Voyager when a web session is available — it has no scope restrictions.
        voyager = _get_voyager_client()
        if voyager:
            try:
                vme = voyager.get_me()
                headline = vme.get("headline", "")
                vanity = vme.get("public_id", "")
                if vme.get("picture_url"):
                    picture = vme["picture_url"]
                source = "voyager"
            except Exception:
                # Voyager unavailable (e.g. playwright not installed) — fall through to OAuth
                pass

        # Fall back to /v2/me (likely 403 without partner scopes, kept for completeness)
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
                headline = ""  # /v2/me requires partner scopes; not available via standard OAuth

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

    # Prefer Voyager — works without partner-level OAuth scopes.
    voyager = _get_voyager_client()
    if voyager:
        try:
            # Need the public_id (vanity name) to build the PATCH URL.
            vme = voyager.get_me()
            public_id = vme.get("public_id", "")
            if not public_id:
                return "❌ Could not determine your LinkedIn public ID from the web session."
            voyager.update_headline(headline, public_id)
            return f"✅ Headline updated via web session:\n  \"{headline}\""
        except Exception as exc:
            return (
                f"⚠️  Voyager update failed: {_format_error(exc)}\n"
                "Falling back to OAuth API..."
            ) + _try_oauth_headline(headline, locale)

    return _try_oauth_headline(headline, locale)


def _try_oauth_headline(headline: str, locale: str) -> str:
    try:
        client = _get_client()
        client.update_headline(headline, locale=locale)
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
    if not li_at or not jsessionid:
        return "❌ Both li_at and jsessionid are required."

    try:
        # Quick sanity check — try fetching the profile before saving.
        vc = VoyagerClient(li_at, jsessionid)
        me = vc.get_me()
        name = f"{me.get('first_name', '')} {me.get('last_name', '')}".strip()
        headline = me.get("headline", "")
        active = _active_alias()
        save_web_session(li_at, jsessionid, active)
        _invalidate_voyager(active)
        return (
            f"✅ Web session saved for '{active}'\n"
            f"   Verified as  : {name}\n"
            f"   Headline     : {headline or '(not set)'}\n\n"
            "get_profile and update_headline will now use the Voyager API automatically."
        )
    except Exception as exc:
        return (
            f"❌ Session validation failed — cookies may be invalid or expired.\n"
            f"   {_format_error(exc)}\n\n"
            "Make sure you are still logged in to linkedin.com and copied the correct cookie values."
        )


@mcp.tool()
def clear_web_session() -> str:
    """
    Remove the stored LinkedIn browser session cookies.

    After clearing, get_profile and update_headline fall back to the OAuth API
    (which cannot read/write the headline without partner-level scopes).
    """
    try:
        active = _active_alias()
        existed = delete_web_session(active)
        _invalidate_voyager(active)
        if existed:
            return f"✅ Web session cleared for '{active}'."
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
    try:
        set_active_alias(alias)
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
    removed = delete_credentials()
    if removed:
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

    try:
        client = _get_client()
        person_urn = client.get_person_urn()
        result = client.create_post(text, visibility=visibility, person_urn=person_urn)

        post_urn = result.get("id", "unknown")
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
    try:
        voyager = _get_voyager_client()
        if not voyager:
            return "❌ Web session required. Run `authenticate` or `set_web_session` first."

        me = voyager.get_me()
        public_id = me.get("public_id", "")
        if not public_id:
            return "❌ Could not determine your LinkedIn public ID."

        posts = voyager.get_recent_posts(public_id, count=count)
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
    try:
        client = _get_client()
        person_urn = client.get_person_urn()
        elements = client.get_posts(count=count, person_urn=person_urn)

        posts = []
        for el in elements:
            # REST /rest/posts format: flat fields.
            # Fallback handles legacy ugcPosts shape if ever returned.
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

        return json.dumps(
            {"total_returned": len(posts), "posts": posts},
            indent=2,
        )
    except Exception as exc:
        _OAUTH_FALLBACK_CODES = {403, 426}
        if not (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _OAUTH_FALLBACK_CODES):
            return _format_error(exc)

    # OAuth lacks r_member_social scope (or version mismatch) — fall back to Voyager.
    try:
        voyager = _get_voyager_client()
        if not voyager:
            return "❌ OAuth API is unavailable (scope or version issue) and no web session is set. Run `authenticate` or `set_web_session` to enable Voyager fallback."
        me = voyager.get_me()
        public_id = me.get("public_id", "")
        if not public_id:
            return "❌ Could not determine your LinkedIn public ID for Voyager fallback."
        posts = voyager.get_recent_posts(public_id, count=count)
        return json.dumps(
            {"source": "voyager", "total_returned": len(posts), "posts": posts},
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        return _format_error(exc)


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

    try:
        client = _get_client()
        client.delete_post(post_urn)
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
    try:
        voyager = _get_voyager_client()
        if not voyager:
            return "❌ Web session required. Run `authenticate` first."

        items = voyager.get_notifications(count=count)
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
    try:
        voyager = _get_voyager_client()
        if not voyager:
            return "❌ Web session required. Run `authenticate` first."

        me = voyager.get_me()
        entity_urn = me.get("entity_urn", "")
        if not entity_urn:
            return "❌ Could not determine your LinkedIn entity URN."

        items = voyager.get_conversations(entity_urn, count=count)
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
    token_data = load_token()
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
    try:
        client = _get_client()
        result = client.get_connections_count()
        return json.dumps(result, indent=2)
    except Exception as exc:
        return _format_error(exc)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
