"""
LinkedIn MCP Server
===================
A Model Context Protocol server that lets Claude manage your LinkedIn
presence through the official LinkedIn REST API v2 and the Voyager internal
API (via a persistent Playwright browser session).

Tools exposed
─────────────
  authenticate          – OAuth 2.0 browser flow; saves token + browser session
  logout                – Delete the saved token
  check_auth            – Show token status and available scopes
  get_profile           – Name, headline, profile URL, email
  get_full_profile      – Full profile: about, experience, education, skills
  update_headline       – Change your profile headline
  create_post           – Publish a new text post
  get_posts             – List your recent posts
  delete_post           – Remove a post by URN
  get_notifications     – Fetch your recent LinkedIn notifications
  get_conversations     – Fetch your recent direct message conversations
  get_api_capabilities  – Explain what each API tier can/cannot do
  get_community_stats   – Connection count (partner scope; graceful fallback)

LinkedIn API limitations
────────────────────────
Standard Consumer API (no partner approval):
  ✅  Read basic profile (name, headline, photo, email)
  ✅  Create, read, and delete UGC posts
  ⚠️  Update headline (requires rw_me scope — may be rejected)
  ❌  Read experience, education, skills, connections

Voyager / Playwright browser session (enabled after authenticate):
  ✅  Full profile headline
  ✅  Profile sections: about, experience, education, skills (via DOM)
  ✅  Notifications
  ✅  Direct message conversations
  ❌  Send messages / connection requests (read-only via Voyager)

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
    DEFAULT_BROWSER_DIR,
    DEFAULT_PORT,
    DEFAULT_SESSION_FILE,
    DEFAULT_TOKEN_FILE,
    delete_token,
    delete_web_session,
    has_browser_profile,
    is_token_expired,
    load_token,
    load_web_session,
    run_oauth_flow,
    save_token,
    save_web_session,
)
from client import LinkedInClient, VoyagerClient

# ── Bootstrap ──────────────────────────────────────────────────────────────────

load_dotenv()

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
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set. "
            "Copy .env.example → .env and fill in your Developer App credentials."
        )
    return client_id, client_secret


def _get_client() -> LinkedInClient:
    """Return an authenticated LinkedInClient, raising if not logged in."""
    token_data = load_token()
    if not token_data:
        raise RuntimeError(
            "Not authenticated. Run the `authenticate` tool first."
        )
    if is_token_expired(token_data):
        raise RuntimeError(
            "Your LinkedIn access token has expired. "
            "Run `authenticate` again to refresh it."
        )
    return LinkedInClient(token_data["access_token"])


def _get_voyager_client() -> Optional[VoyagerClient]:
    """Return a VoyagerClient backed by the persistent browser profile, or None."""
    session = load_web_session()
    if not session:
        return None
    user_data_dir = DEFAULT_BROWSER_DIR if has_browser_profile() else None
    return VoyagerClient(session["li_at"], session["jsessionid"], user_data_dir=user_data_dir)


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
def authenticate() -> str:
    """
    Start the LinkedIn OAuth 2.0 flow.

    Opens your default browser so you can authorize the app, then waits
    for the callback on localhost.  Your access token is saved to
    ~/.linkedin_mcp_token.json (mode 0600) and reused on future calls.

    Before calling this tool, make sure LINKEDIN_CLIENT_ID and
    LINKEDIN_CLIENT_SECRET are set in your environment (or in a .env file
    next to server.py).
    """
    try:
        client_id, client_secret = _credentials()
        port = DEFAULT_PORT

        token_data, li_at, jsessionid = run_oauth_flow(client_id, client_secret, port=port)
        save_token(token_data)

        scopes = token_data.get("scope", "unknown")
        expires_in = token_data.get("expires_in", "unknown")

        session_note = ""
        if li_at and jsessionid:
            save_web_session(li_at, jsessionid)
            session_note = f"   Web session    : saved to {DEFAULT_SESSION_FILE} (Voyager API enabled)\n"
        else:
            session_note = (
                "   Web session    : not captured (run `set_web_session` manually to enable\n"
                "                    full profile read/write via Voyager API)\n"
            )

        return (
            "✅ Successfully authenticated with LinkedIn!\n"
            f"   Scopes granted : {scopes}\n"
            f"   Expires in     : {expires_in} seconds\n"
            f"   Token saved to : {DEFAULT_TOKEN_FILE}\n"
            f"{session_note}\n"
            "You can now use `get_profile`, `create_post`, and other tools."
        )
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
def logout() -> str:
    """
    Remove the saved LinkedIn access token from disk.

    After calling this, you will need to run `authenticate` again before
    using any other tool.
    """
    existed = delete_token()
    if existed:
        return f"✅ Token deleted from {DEFAULT_TOKEN_FILE}. You are now logged out."
    return "ℹ️ No token file found — you were already logged out."


@mcp.tool()
def check_auth() -> str:
    """
    Check whether you have a valid LinkedIn access token.

    Returns token metadata (scopes, expiry) without making an API call.
    """
    token_data = load_token()

    if not token_data:
        return json.dumps(
            {
                "authenticated": False,
                "message": "No token found. Run `authenticate` to log in.",
            },
            indent=2,
        )

    obtained_at = token_data.get("_obtained_at", 0)
    expires_in = token_data.get("expires_in", 0)
    expired = is_token_expired(token_data)
    expiry_ts = obtained_at + expires_in if (obtained_at and expires_in) else None

    return json.dumps(
        {
            "authenticated": True,
            "expired": expired,
            "scopes": token_data.get("scope", "unknown"),
            "expires_at": (
                time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(expiry_ts))
                if expiry_ts
                else "unknown"
            ),
            "token_file": DEFAULT_TOKEN_FILE,
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
            except Exception as ve:
                headline = f"Voyager error: {ve}"

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
            except Exception as e:
                headline = f"Not accessible via OAuth ({e}). Use set_web_session for full access."

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
        save_web_session(li_at, jsessionid)
        return (
            f"✅ Web session saved to {DEFAULT_SESSION_FILE}\n"
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
    existed = delete_web_session()
    if existed:
        return f"✅ Web session cleared ({DEFAULT_SESSION_FILE} deleted)."
    return "ℹ️ No web session found — nothing to clear."


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
def get_posts(count: int = 10) -> str:
    """
    List your recent LinkedIn UGC posts.

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
            content = el.get("specificContent", {}).get(
                "com.linkedin.ugc.ShareContent", {}
            )
            text = content.get("shareCommentary", {}).get("text", "")
            vis = el.get("visibility", {}).get(
                "com.linkedin.ugc.MemberNetworkVisibility", "UNKNOWN"
            )
            created_ms = el.get("created", {}).get("time", 0)
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
