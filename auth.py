"""
LinkedIn OAuth 2.0 authentication module.

Handles the full authorization code flow:
  1. Build the authorization URL and open it in the user's browser
  2. Spin up a temporary local HTTP server to receive the callback
  3. Exchange the authorization code for an access token
  4. Persist the token to disk for future requests
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional

import httpx

try:
    from playwright.sync_api import sync_playwright, Route as PlaywrightRoute
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

# Standard scopes available without partner-program approval.
# w_member_social  → create / delete posts
# r_liteprofile    → id, name, headline, profile picture
# r_emailaddress   → primary email address
# r_basicprofile   → broader profile fields (summary, location, industry)
SCOPES = [
    "openid",
    "profile",
    "email",
    "w_member_social",
]

DEFAULT_PORT = int(os.environ.get("LINKEDIN_REDIRECT_PORT", "8919"))
DEFAULT_TOKEN_FILE = os.path.expanduser(
    os.environ.get("LINKEDIN_TOKEN_FILE", "~/.linkedin_mcp_token.json")
)
DEFAULT_SESSION_FILE = os.path.expanduser(
    os.environ.get("LINKEDIN_SESSION_FILE", "~/.linkedin_mcp_session.json")
)
DEFAULT_BROWSER_DIR = os.path.expanduser(
    os.environ.get("LINKEDIN_BROWSER_DIR", "~/.linkedin_mcp_browser")
)


# ── Local callback server ──────────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot HTTP handler that captures the OAuth authorization code."""

    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            body = (
                b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                b"<h2>&#10003; LinkedIn authorization successful!</h2>"
                b"<p>You can close this window and return to Claude.</p>"
                b"</body></html>"
            )
            self.send_response(200)
        elif "error" in params:
            _CallbackHandler.error = params.get("error_description", params.get("error", ["Unknown error"]))[0]
            body = (
                b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                b"<h2>&#10007; Authorization failed</h2>"
                b"<p>Please close this window and check the error in Claude.</p>"
                b"</body></html>"
            )
            self.send_response(400)
        else:
            body = b"<html><body>Unexpected callback.</body></html>"
            self.send_response(400)

        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: ANN002
        pass  # suppress access logs


def _wait_for_code(port: int, timeout: int = 120) -> tuple[Optional[str], Optional[str]]:
    """
    Start a local HTTP server, wait up to *timeout* seconds for the OAuth
    callback, then shut down. Returns (code, error).
    """
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None

    server = HTTPServer(("", port), _CallbackHandler)

    def _serve() -> None:
        server.handle_request()  # handles exactly one request then returns

    t = Thread(target=_serve, daemon=True)
    t.start()
    t.join(timeout=timeout)

    return _CallbackHandler.auth_code, _CallbackHandler.error


# ── Public helpers ─────────────────────────────────────────────────────────────

def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Return the LinkedIn authorization URL the user must visit."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange an authorization code for an access token."""
    resp = httpx.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def run_oauth_flow(
    client_id: str,
    client_secret: str,
    port: int = DEFAULT_PORT,
) -> tuple[dict, Optional[str], Optional[str]]:
    """
    Full interactive OAuth flow.

    Returns (token_data, li_at, jsessionid).

    When Playwright is installed, a managed Chromium window handles the entire
    flow — the OAuth callback is intercepted directly and LinkedIn session
    cookies (li_at, JSESSIONID) are harvested automatically, enabling Voyager
    API access with no manual DevTools work.

    When Playwright is unavailable, falls back to webbrowser.open() + a local
    callback server; li_at and jsessionid are returned as None.

    Raises RuntimeError on failure or timeout.
    """
    if _PLAYWRIGHT_AVAILABLE:
        try:
            return _run_oauth_flow_playwright(client_id, client_secret, port)
        except Exception as exc:
            # Playwright binary may not be installed yet; degrade gracefully.
            if "executable doesn't exist" in str(exc) or "BrowserType" in str(type(exc).__name__):
                pass  # fall through to legacy flow
            else:
                raise
    token_data = _run_oauth_flow_browser(client_id, client_secret, port)
    return token_data, None, None


def has_browser_profile(path: str = DEFAULT_BROWSER_DIR) -> bool:
    """Return True if a persistent Playwright browser profile has been created."""
    return os.path.exists(path) and bool(os.listdir(path))


def _run_oauth_flow_playwright(
    client_id: str,
    client_secret: str,
    port: int,
) -> tuple[dict, Optional[str], Optional[str]]:
    """
    OAuth flow driven by a Playwright persistent context.

    Using launch_persistent_context stores the full browser fingerprint
    (cookies, localStorage, canvas seeds, etc.) in DEFAULT_BROWSER_DIR.
    VoyagerClient reuses this same profile so LinkedIn sees a consistent
    browser identity on every subsequent API call.
    """
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, state)

    captured_code: list[Optional[str]] = [None]

    _SUCCESS_HTML = (
        "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>&#10003; LinkedIn authorization successful!</h2>"
        "<p>You can close this window and return to Claude.</p>"
        "</body></html>"
    )

    def _handle_callback(route: "PlaywrightRoute") -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(route.request.url).query)
        captured_code[0] = params.get("code", [None])[0]
        route.fulfill(status=200, content_type="text/html", body=_SUCCESS_HTML)

    with sync_playwright() as p:
        os.makedirs(DEFAULT_BROWSER_DIR, exist_ok=True)
        context = p.chromium.launch_persistent_context(
            DEFAULT_BROWSER_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()

        # Intercept the OAuth callback — no local HTTP server needed.
        page.route(f"http://localhost:{port}/callback**", _handle_callback)

        page.goto(auth_url)
        # Block until LinkedIn redirects back to our callback URL (user must log in + approve).
        page.wait_for_url(f"http://localhost:{port}/callback**", timeout=120_000)

        if not captured_code[0]:
            context.close()
            raise RuntimeError("No authorization code received from LinkedIn.")

        # Harvest LinkedIn session cookies before closing the browser.
        raw_cookies = context.cookies("https://www.linkedin.com")
        li_at = next((c["value"] for c in raw_cookies if c["name"] == "li_at"), None)
        jsessionid = next((c["value"] for c in raw_cookies if c["name"] == "JSESSIONID"), None)

        context.close()

    token_data = exchange_code(captured_code[0], client_id, client_secret, redirect_uri)
    token_data["_obtained_at"] = int(time.time())
    return token_data, li_at, jsessionid


def _run_oauth_flow_browser(client_id: str, client_secret: str, port: int) -> dict:
    """Legacy OAuth flow using webbrowser.open() + local callback server."""
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, state)

    webbrowser.open(auth_url)
    code, error = _wait_for_code(port)

    if error:
        raise RuntimeError(f"LinkedIn authorization failed: {error}")
    if not code:
        raise RuntimeError("Timed out waiting for LinkedIn authorization. Please try again.")

    token_data = exchange_code(code, client_id, client_secret, redirect_uri)
    token_data["_obtained_at"] = int(time.time())
    return token_data


# ── Token persistence ──────────────────────────────────────────────────────────

def save_token(token_data: dict, path: str = DEFAULT_TOKEN_FILE) -> None:
    """Persist *token_data* to *path* (mode 0o600)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(token_data, fh, indent=2)
    os.chmod(path, 0o600)


def load_token(path: str = DEFAULT_TOKEN_FILE) -> Optional[dict]:
    """Load a previously saved token. Returns None if the file doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def is_token_expired(token_data: dict, buffer_seconds: int = 300) -> bool:
    """
    Return True if the access token has expired (or will expire within
    *buffer_seconds*).  Returns False when expiry information is unavailable
    (we optimistically assume it's still valid).
    """
    obtained_at = token_data.get("_obtained_at")
    expires_in = token_data.get("expires_in")
    if obtained_at is None or expires_in is None:
        return False
    return time.time() >= (obtained_at + expires_in - buffer_seconds)


def delete_token(path: str = DEFAULT_TOKEN_FILE) -> bool:
    """Remove the saved token. Returns True if the file existed."""
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ── Web session persistence (Voyager cookies) ──────────────────────────────────

def save_web_session(
    li_at: str,
    jsessionid: str,
    path: str = DEFAULT_SESSION_FILE,
) -> None:
    """Persist li_at and JSESSIONID cookies for Voyager API access."""
    data = {"li_at": li_at, "jsessionid": jsessionid, "_saved_at": int(time.time())}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(path, 0o600)


def load_web_session(path: str = DEFAULT_SESSION_FILE) -> Optional[dict]:
    """Load saved web session cookies. Returns None if not found."""
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def delete_web_session(path: str = DEFAULT_SESSION_FILE) -> bool:
    """Remove saved web session. Returns True if the file existed."""
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
