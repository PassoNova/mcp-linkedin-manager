"""
LinkedIn OAuth 2.0 authentication module.

Handles the full authorization code flow:
  1. Build the authorization URL and open it in the user's browser
  2. Spin up a temporary local HTTP server to receive the callback
  3. Exchange the authorization code for an access token
  4. Persist the token to disk for future requests
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional

import httpx

try:
    from playwright.async_api import async_playwright, Route as PlaywrightRoute
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

try:
    import keyring
    import keyring.errors
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

try:
    import browser_cookie3
    _HAS_BROWSER_COOKIE3 = True
except ImportError:
    _HAS_BROWSER_COOKIE3 = False


_KR_SERVICE = "linkedin-mcp"
_KR_KEY = "session"
_KR_KEY_TOKEN = "oauth_token"
_KR_KEY_CREDS = "credentials"

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
DEFAULT_BROWSER_DIR = os.path.expanduser(
    os.environ.get("LINKEDIN_BROWSER_DIR", "~/.linkedin_mcp_browser")
)
DEFAULT_USERS_FILE = os.path.expanduser(
    os.environ.get("LINKEDIN_USERS_FILE", "~/.linkedin_mcp_users.json")
)

_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def validate_alias(alias: str) -> None:
    """Raise ValueError if alias contains invalid characters or is too long."""
    if not _ALIAS_RE.match(alias):
        raise ValueError(
            f"Invalid alias {alias!r}. Use 1–32 letters, digits, hyphens, or underscores."
        )


def _token_path(alias: str) -> str:
    return os.path.expanduser(f"~/.linkedin_mcp_token_{alias}.json")


def _session_path(alias: str) -> str:
    return os.path.expanduser(f"~/.linkedin_mcp_session_{alias}.json")


def _browser_dir(alias: str) -> str:
    return os.path.expanduser(f"~/.linkedin_mcp_browser_{alias}")


# ── Local callback server ──────────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot HTTP handler that captures the OAuth authorization code."""

    auth_code: Optional[str] = None
    error: Optional[str] = None
    expected_state: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        received_state = params.get("state", [None])[0]

        if "code" in params:
            # Validate state token to prevent CSRF.
            if _CallbackHandler.expected_state and received_state != _CallbackHandler.expected_state:
                _CallbackHandler.error = (
                    f"State mismatch in OAuth callback — possible CSRF attempt. "
                    f"Expected {_CallbackHandler.expected_state!r}, got {received_state!r}."
                )
                body = (
                    b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                    b"<h2>&#10007; Authorization failed</h2>"
                    b"<p>State mismatch detected. Please close this window and try again.</p>"
                    b"</body></html>"
                )
                self.send_response(400)
            else:
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


def _wait_for_code(
    port: int,
    timeout: int = 120,
    expected_state: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Start a local HTTP server, wait up to *timeout* seconds for the OAuth
    callback, then shut down. Returns (code, error).
    """
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    _CallbackHandler.expected_state = expected_state

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


def has_browser_profile(path: str = DEFAULT_BROWSER_DIR) -> bool:
    """Return True if a persistent Playwright browser profile has been created."""
    return os.path.exists(path) and bool(os.listdir(path))


def _capture_chrome_linkedin_cookies() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Read li_at and JSESSIONID from the system Chrome cookie store after OAuth.

    Returns (li_at, jsessionid, error_message). error_message is None on success.
    """
    if not _HAS_BROWSER_COOKIE3:
        return None, None, "browser-cookie3 not installed"
    for attempt in range(3):
        try:
            cj = browser_cookie3.chrome(domain_name=".linkedin.com")
            li_at = next((c.value for c in cj if c.name == "li_at"), None)
            jsessionid = next((c.value for c in cj if c.name == "JSESSIONID"), None)
            if li_at:
                return li_at, jsessionid, None
        except Exception as exc:
            last_err = str(exc)
            if attempt < 2:
                time.sleep(3)
                continue
            return None, None, f"cookie read failed: {last_err}"
        # li_at not found yet — Chrome may not have flushed it; wait and retry
        if attempt < 2:
            time.sleep(3)
    return None, None, "li_at not found in Chrome's cookie store for .linkedin.com"


def _is_playwright_binary_error(exc: Exception) -> bool:
    """Return True when Playwright binary is missing or not installed."""
    exc_str = str(exc)
    return (
        "executable doesn't exist" in exc_str
        or "no such file" in exc_str.lower()
        or "BrowserType" in type(exc).__name__
        or "run `playwright install`" in exc_str
    )


def _is_playwright_network_error(exc: Exception) -> bool:
    """Return True when Playwright's Chromium can't reach the network (e.g. proxy/VPN)."""
    exc_str = str(exc)
    return "ERR_CONNECTION_REFUSED" in exc_str or "net::" in exc_str


async def _playwright_can_launch(browser_dir: str) -> tuple[bool, Optional[Exception]]:
    """
    Probe-launch Playwright Chromium headlessly to verify the binary exists and starts.
    Returns (can_launch, exc_if_not). Closes the context immediately on success.
    """
    try:
        async with async_playwright() as p:
            os.makedirs(browser_dir, exist_ok=True)
            ctx = await p.chromium.launch_persistent_context(
                browser_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            await ctx.close()
        return True, None
    except Exception as exc:
        return False, exc


async def _init_headless_profile(
    li_at: str,
    jsessionid: str,
    browser_dir: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Seed a headless Playwright profile with cookies captured from Chrome.

    Injects li_at (and JSESSIONID if present), navigates to linkedin.com/feed/
    so Cloudflare fingerprints the context and LinkedIn issues/refreshes
    JSESSIONID.  The resulting persistent profile is what VoyagerClient reuses
    for every subsequent API call.

    Returns (jsessionid_final, error_message).  error_message is None on success.
    On failure the original jsessionid is returned unchanged so the caller can
    still save the session even without a fully warmed profile.
    """
    try:
        async with async_playwright() as p:
            os.makedirs(browser_dir, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                browser_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            cookies: list[dict] = [
                {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
            ]
            if jsessionid:
                cookies.append(
                    {"name": "JSESSIONID", "value": jsessionid, "domain": ".linkedin.com", "path": "/"}
                )
            await context.add_cookies(cookies)

            page = await context.new_page()
            try:
                await page.goto(
                    "https://www.linkedin.com/feed/",
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
            except Exception:
                pass  # best-effort; profile directory is still written

            live_cookies = await context.cookies("https://www.linkedin.com")
            jsessionid_final = next(
                (c["value"] for c in live_cookies if c["name"] == "JSESSIONID"),
                jsessionid,
            )
            await context.close()
        return jsessionid_final, None
    except Exception as exc:
        return jsessionid, f"headless profile init failed: {exc}"


async def run_oauth_flow(
    client_id: str,
    client_secret: str,
    port: int = DEFAULT_PORT,
    browser_dir: str = DEFAULT_BROWSER_DIR,
) -> tuple[dict, Optional[str], Optional[str], Optional[str]]:
    """
    Full interactive OAuth flow. Returns (token_data, li_at, jsessionid, session_error).

    Primary path — Playwright headful:
      Run the full OAuth flow inside Playwright's bundled Chromium so that auth,
      cookie capture, and profile creation all happen in one persistent browser
      context.  VoyagerClient reuses this same profile so LinkedIn always sees a
      consistent fingerprint.

    Fallback — system Chrome + cookie capture (Playwright not installed):
      Opens the OAuth URL in Chrome and reads li_at via browser-cookie3.
      Voyager API will not be available because there is no Playwright profile.

    Raises RuntimeError on failure or timeout.
    """
    # ── Primary: Playwright headful (consistent fingerprint for Voyager API) ────
    if _PLAYWRIGHT_AVAILABLE:
        try:
            token_data, li_at, jsessionid = await _run_oauth_flow_playwright(
                client_id, client_secret, port, browser_dir
            )
            err = None if li_at else "li_at cookie not found in Playwright browser context"
            return token_data, li_at, jsessionid, err
        except Exception as exc:
            # Only fall back to system browser when the Playwright binary itself is
            # missing.  Network errors with a working binary should not open a second
            # browser — that confuses the user who is already watching Chromium.
            if not _is_playwright_binary_error(exc):
                raise

    # ── Fallback: system browser (Playwright binary not installed) ──────────────
    token_data, opened_via_chrome = _run_oauth_flow_browser(client_id, client_secret, port)
    if opened_via_chrome:
        li_at, jsessionid, cookie_err = _capture_chrome_linkedin_cookies()
        return token_data, li_at, jsessionid, cookie_err
    return token_data, None, None, "Playwright is not installed and system browser is not Chrome; run `set_web_session` manually"


async def _run_oauth_flow_playwright(
    client_id: str,
    client_secret: str,
    port: int,
    browser_dir: str = DEFAULT_BROWSER_DIR,
) -> tuple[dict, Optional[str], Optional[str]]:
    """
    OAuth flow driven by a Playwright headful persistent context.

    launch_persistent_context stores the full browser fingerprint (cookies,
    localStorage, canvas seeds, etc.) in browser_dir.  VoyagerClient reuses
    this same profile so LinkedIn always sees a consistent identity.

    Uses a real local HTTP server to capture the OAuth callback (same as the
    browser fallback) rather than Playwright route interception.  This avoids
    net::ERR_CONNECTION_REFUSED errors that occur when Playwright tries to
    navigate to the localhost callback URL before the route handler fires.
    """
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, state)

    # Start the callback server BEFORE opening the browser so the port is
    # already bound when LinkedIn redirects back.
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    _CallbackHandler.expected_state = state

    server = HTTPServer(("", port), _CallbackHandler)

    def _serve() -> None:
        server.handle_request()

    server_thread = Thread(target=_serve, daemon=True)
    server_thread.start()

    try:
        async with async_playwright() as p:
            os.makedirs(browser_dir, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                browser_dir,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = await context.new_page()
            await page.goto(auth_url)

            # Block until the HTTP server captures the callback (user logs in
            # and approves on LinkedIn, which redirects to localhost).
            await asyncio.get_event_loop().run_in_executor(
                None, server_thread.join, 120
            )

            code = _CallbackHandler.auth_code
            error = _CallbackHandler.error

            if error:
                await context.close()
                raise RuntimeError(f"LinkedIn authorization failed: {error}")
            if not code:
                await context.close()
                raise RuntimeError("Timed out waiting for LinkedIn authorization. Please try again.")

            # Navigate to LinkedIn feed so the server issues JSESSIONID before
            # we read cookies — JSESSIONID is only set on real page requests.
            try:
                await page.goto(
                    "https://www.linkedin.com/feed/",
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
            except Exception:
                pass  # best-effort; li_at is the critical cookie

            raw_cookies = await context.cookies("https://www.linkedin.com")
            li_at = next((c["value"] for c in raw_cookies if c["name"] == "li_at"), None)
            jsessionid = next((c["value"] for c in raw_cookies if c["name"] == "JSESSIONID"), None)

            await context.close()

        token_data = exchange_code(code, client_id, client_secret, redirect_uri)
        token_data["_obtained_at"] = int(time.time())
        return token_data, li_at, jsessionid
    finally:
        server.server_close()


def _is_chrome_available() -> bool:
    """Return True if Google Chrome is installed on this system."""
    # macOS: check standard app bundle locations.
    chrome_paths = [
        "/Applications/Google Chrome.app",
        os.path.expanduser("~/Applications/Google Chrome.app"),
    ]
    return any(os.path.exists(p) for p in chrome_paths)


def _open_in_chrome(url: str) -> bool:
    """Open *url* in Google Chrome directly. Returns True if Chrome was found."""
    import subprocess
    # macOS: 'open -a' forces the URL to open in Chrome regardless of default browser.
    try:
        subprocess.run(
            ["open", "-a", "Google Chrome", url],
            check=True, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _run_oauth_flow_browser(client_id: str, client_secret: str, port: int) -> tuple[dict, bool]:
    """
    OAuth flow using a local callback server.

    Returns (token_data, opened_via_chrome).

    Prefers opening Chrome explicitly (so browser-cookie3 can capture li_at
    afterwards) and falls back to webbrowser.open() when Chrome isn't available.
    """
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, state)

    opened_via_chrome = _open_in_chrome(auth_url)
    if not opened_via_chrome:
        webbrowser.open(auth_url)

    code, error = _wait_for_code(port, expected_state=state)

    if error:
        raise RuntimeError(f"LinkedIn authorization failed: {error}")
    if not code:
        raise RuntimeError("Timed out waiting for LinkedIn authorization. Please try again.")

    token_data = exchange_code(code, client_id, client_secret, redirect_uri)
    token_data["_obtained_at"] = int(time.time())
    return token_data, opened_via_chrome


# ── Token persistence (per-user, keyed by alias) ───────────────────────────────

def save_token(token_data: dict, alias: str) -> None:
    """Persist token_data to OS keychain under alias, falling back to a per-alias file."""
    key = f"{_KR_KEY_TOKEN}:{alias}"
    if _HAS_KEYRING:
        try:
            keyring.set_password(_KR_SERVICE, key, json.dumps(token_data))
            return
        except Exception:
            pass
    path = _token_path(alias)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(token_data, fh, indent=2)
    os.chmod(path, 0o600)


def load_token(alias: str) -> Optional[dict]:
    """Load saved token for alias, checking OS keychain first then file."""
    key = f"{_KR_KEY_TOKEN}:{alias}"
    if _HAS_KEYRING:
        try:
            raw = keyring.get_password(_KR_SERVICE, key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    path = _token_path(alias)
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


def delete_token(alias: str) -> bool:
    """Remove saved token for alias from keychain and/or file. Returns True if anything deleted."""
    key = f"{_KR_KEY_TOKEN}:{alias}"
    deleted = False
    if _HAS_KEYRING:
        try:
            keyring.delete_password(_KR_SERVICE, key)
            deleted = True
        except Exception:
            pass
    path = _token_path(alias)
    if os.path.exists(path):
        os.remove(path)
        deleted = True
    return deleted


# ── Web session persistence (Voyager cookies, per-user) ───────────────────────

def save_web_session(li_at: str, jsessionid: str, alias: str) -> None:
    """Persist li_at and JSESSIONID cookies for alias, preferring OS keychain."""
    key = f"{_KR_KEY}:{alias}"
    data = {"li_at": li_at, "jsessionid": jsessionid, "_saved_at": int(time.time())}
    if _HAS_KEYRING:
        try:
            keyring.set_password(_KR_SERVICE, key, json.dumps(data))
            return
        except Exception:
            pass
    path = _session_path(alias)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(path, 0o600)


def load_web_session(alias: str) -> Optional[dict]:
    """Load saved web session cookies for alias, checking keychain first then file."""
    key = f"{_KR_KEY}:{alias}"
    if _HAS_KEYRING:
        try:
            raw = keyring.get_password(_KR_SERVICE, key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    path = _session_path(alias)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def delete_web_session(alias: str) -> bool:
    """Remove saved web session for alias from keychain and/or file. Returns True if deleted."""
    key = f"{_KR_KEY}:{alias}"
    deleted = False
    if _HAS_KEYRING:
        try:
            keyring.delete_password(_KR_SERVICE, key)
            deleted = True
        except Exception:
            pass
    path = _session_path(alias)
    if os.path.exists(path):
        os.remove(path)
        deleted = True
    return deleted


# ── App credential persistence ─────────────────────────────────────────────────

def save_credentials(client_id: str, client_secret: str) -> bool:
    """Store app credentials in OS keychain. Returns True if saved, False if keyring unavailable."""
    if not _HAS_KEYRING:
        return False
    try:
        data = {"client_id": client_id, "client_secret": client_secret}
        keyring.set_password(_KR_SERVICE, _KR_KEY_CREDS, json.dumps(data))
        return True
    except Exception:
        return False


def load_credentials() -> Optional[dict]:
    """Load app credentials from OS keychain. Returns None if not stored or keyring unavailable."""
    if not _HAS_KEYRING:
        return None
    try:
        raw = keyring.get_password(_KR_SERVICE, _KR_KEY_CREDS)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def delete_credentials() -> bool:
    """Remove app credentials from OS keychain. Returns True if they existed."""
    if not _HAS_KEYRING:
        return False
    try:
        keyring.delete_password(_KR_SERVICE, _KR_KEY_CREDS)
        return True
    except Exception:
        return False


# ── User registry ──────────────────────────────────────────────────────────────

def load_user_registry(path: str = DEFAULT_USERS_FILE) -> dict:
    """Return {"active": str|None, "aliases": list[str]}."""
    if not os.path.exists(path):
        return {"active": None, "aliases": []}
    with open(path) as fh:
        return json.load(fh)


def save_user_registry(registry: dict, path: str = DEFAULT_USERS_FILE) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(registry, fh, indent=2)


def get_active_alias(path: str = DEFAULT_USERS_FILE) -> Optional[str]:
    return load_user_registry(path).get("active")


def set_active_alias(alias: str, path: str = DEFAULT_USERS_FILE) -> None:
    reg = load_user_registry(path)
    if alias not in reg["aliases"]:
        raise ValueError(f"Unknown alias {alias!r}. Run `authenticate` with this alias first.")
    reg["active"] = alias
    save_user_registry(reg, path)


def register_alias(alias: str, path: str = DEFAULT_USERS_FILE) -> None:
    """Add alias to registry. Sets it as active if it's the first, or already active."""
    reg = load_user_registry(path)
    if alias not in reg["aliases"]:
        reg["aliases"].append(alias)
    if reg["active"] is None:
        reg["active"] = alias
    save_user_registry(reg, path)


def deregister_alias(alias: str, path: str = DEFAULT_USERS_FILE) -> None:
    """Remove alias from registry; if it was active, promote another alias or set None."""
    reg = load_user_registry(path)
    reg["aliases"] = [a for a in reg["aliases"] if a != alias]
    if reg.get("active") == alias:
        reg["active"] = reg["aliases"][0] if reg["aliases"] else None
    save_user_registry(reg, path)
