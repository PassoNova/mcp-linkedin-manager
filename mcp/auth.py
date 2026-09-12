"""
LinkedIn OAuth 2.0 authentication module.

Handles the full authorization code flow:
  1. Build the authorization URL and open it in the user's system browser
  2. Spin up a temporary local HTTP server to receive the callback
  3. Exchange the authorization code for an access token
  4. Persist the token to disk / OS keychain for future requests

Design decision: the login happens inside a *headed* Playwright window on the
per-alias persistent profile whenever Playwright's Chromium can reach
linkedin.com (checked with a short headless probe first). The Voyager session
cookies are then read straight from that profile — the same profile
VoyagerClient reuses headlessly — so no Chrome cookie-store decryption and no
OS-keychain access is needed, and the cookies match the browser fingerprint
LinkedIn saw at login.

If the probe fails (macOS Application Firewall, Little Snitch, no Playwright
Chromium installed) the flow falls back to the system browser + local callback
server, followed by best-effort capture from Chrome's cookie store. Set
LINKEDIN_AUTH_MODE=playwright|browser to force one path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import NamedTuple, Optional

import httpx

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _sync_playwright = None  # type: ignore[assignment]
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


# ── Module logger ──────────────────────────────────────────────────────────────

_log = logging.getLogger("linkedin_mcp.auth")

_KR_SERVICE = "linkedin-mcp"
_KR_KEY = "session"
_KR_KEY_TOKEN = "oauth_token"
_KR_KEY_CREDS = "credentials"

# ── Constants ─────────────────────────────────────────────────────────────────

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

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


def ensure_private_dir(path: str) -> None:
    """Create ``path`` (if needed) and force owner-only permissions (0700).

    The Playwright profile holds the live LinkedIn session cookie, so it must
    never be group- or world-readable. ``os.makedirs(mode=...)`` is masked by
    the umask, and pre-existing directories are left untouched by it, so an
    explicit ``chmod`` follows in both cases.
    """
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def _write_private_json(path: str, data: dict) -> None:
    """Write ``data`` as JSON to ``path`` with mode 0600 from the moment it exists.

    Opening with ``os.open(..., 0o600)`` avoids the window where a file created
    by ``open(path, "w")`` is readable under the default umask before a later
    ``chmod``. The mode argument only applies to a *new* file, so an existing
    (possibly looser) file is tightened on its descriptor before anything is
    written to it.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        else:  # pragma: no cover - Windows < 3.13
            os.chmod(path, 0o600)
    except OSError:
        os.close(fd)
        raise
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)


# ── OAuth callback pages ───────────────────────────────────────────────────────

_SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LinkedIn connected</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f3f4f6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .card {
    background: #fff;
    border-radius: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,.08);
    max-width: 480px;
    width: 100%;
    padding: 48px 40px 40px;
    text-align: center;
  }
  .icon { font-size: 48px; margin-bottom: 16px; }
  h1 { font-size: 22px; font-weight: 700; color: #111; margin-bottom: 8px; }
  .subtitle { font-size: 14px; color: #6b7280; margin-bottom: 32px; line-height: 1.5; }
  .keep-open {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #fef9c3;
    color: #854d0e;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 36px;
  }
  .features {
    text-align: left;
    border-top: 1px solid #f0f0f0;
    padding-top: 28px;
  }
  .features-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #9ca3af;
    margin-bottom: 16px;
  }
  .feature {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid #f9f9f9;
  }
  .feature:last-child { border-bottom: none; }
  .feature-icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: #f3f4f6;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    flex-shrink: 0;
  }
  .feature-text strong {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: #111;
    margin-bottom: 2px;
  }
  .feature-text span { font-size: 12px; color: #6b7280; line-height: 1.4; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">&#9989;</div>
  <h1>LinkedIn connected</h1>
  <p class="subtitle">Claude now has access to your LinkedIn account.<br>Head back to Claude to get started.</p>
  <div class="keep-open">
    <span>&#9888;&#65039;</span>
    Keep this tab open — Claude needs it to complete setup.
  </div>
  <div class="features">
    <div class="features-label">What you can do now</div>
    <div class="feature">
      <div class="feature-icon">&#128221;</div>
      <div class="feature-text">
        <strong>Create &amp; manage posts</strong>
        <span>Draft, publish, or delete LinkedIn posts directly from Claude.</span>
      </div>
    </div>
    <div class="feature">
      <div class="feature-icon">&#128100;</div>
      <div class="feature-text">
        <strong>Read your full profile</strong>
        <span>Experience, education, skills, headline — all accessible to Claude.</span>
      </div>
    </div>
    <div class="feature">
      <div class="feature-icon">&#128276;</div>
      <div class="feature-text">
        <strong>Check notifications</strong>
        <span>See who reacted, commented, or mentioned you.</span>
      </div>
    </div>
    <div class="feature">
      <div class="feature-icon">&#128172;</div>
      <div class="feature-text">
        <strong>Browse conversations</strong>
        <span>Review your recent LinkedIn DMs without leaving Claude.</span>
      </div>
    </div>
    <div class="feature">
      <div class="feature-icon">&#9999;&#65039;</div>
      <div class="feature-text">
        <strong>Update your headline</strong>
        <span>Let Claude help you craft and set a sharper headline.</span>
      </div>
    </div>
  </div>
</div>
</body>
</html>"""

_ERROR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorization failed</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f3f4f6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .card {
    background: #fff;
    border-radius: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,.08);
    max-width: 400px;
    width: 100%;
    padding: 48px 40px 40px;
    text-align: center;
  }
  .icon { font-size: 48px; margin-bottom: 16px; }
  h1 { font-size: 22px; font-weight: 700; color: #111; margin-bottom: 8px; }
  p { font-size: 14px; color: #6b7280; line-height: 1.6; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">&#10060;</div>
  <h1>Authorization failed</h1>
  <p>Something went wrong during LinkedIn authorization.<br>
     You can close this tab and try <code>authenticate</code> again from Claude.</p>
</div>
</body>
</html>"""


# ── Local callback server ──────────────────────────────────────────────────────

class _OAuthCallbackServer(HTTPServer):
    """Loopback-only HTTP server that owns the state of one OAuth callback.

    State lives on the server instance, not on the handler class, so two
    concurrent flows (different ports) can never clobber each other's code,
    error, or expected CSRF state.
    """

    allow_reuse_address = True

    def __init__(self, port: int, expected_state: Optional[str]) -> None:
        super().__init__(("127.0.0.1", port), _CallbackHandler)
        self.expected_state = expected_state
        self.auth_code: Optional[str] = None
        self.error: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.auth_code is not None or self.error is not None


class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot HTTP handler that records the OAuth authorization code on its server."""

    server: _OAuthCallbackServer  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        srv = self.server

        received_state = params.get("state", [None])[0]

        if "code" in params:
            if srv.expected_state and not secrets.compare_digest(
                (received_state or "").encode(), srv.expected_state.encode()
            ):
                srv.error = "State mismatch in OAuth callback — possible CSRF attempt."
                body = _ERROR_PAGE.encode()
                self.send_response(400)
            else:
                srv.auth_code = params["code"][0]
                body = _SUCCESS_PAGE.encode()
                self.send_response(200)
        elif "error" in params:
            srv.error = params.get("error_description", params.get("error", ["Unknown error"]))[0]
            body = _ERROR_PAGE.encode()
            self.send_response(400)
        else:
            body = _ERROR_PAGE.encode()
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
    Serve the loopback OAuth callback until a code or error arrives, or until
    *timeout* seconds have passed. Returns (code, error).

    Requests that carry neither ``code`` nor ``error`` (a favicon probe, a
    stray hit on /callback) are answered and ignored; they do not end the
    wait. The server is closed deterministically on every exit path, so no
    thread is left blocked in handle_request().
    """
    callback = _CallbackServer(port, expected_state)
    callback.start()
    try:
        deadline = time.monotonic() + timeout
        while not callback.done and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        callback.close()
    return callback.code, callback.error


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
    _log.debug("Exchanging authorization code for access token")
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
    _log.info("Access token obtained (expires_in=%s)", resp.json().get("expires_in"))
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
                _log.info("Captured li_at from Chrome cookie store (attempt %d)", attempt + 1)
                return li_at, jsessionid, None
        except Exception as exc:
            last_err = str(exc)
            _log.debug("Cookie capture attempt %d failed: %s", attempt + 1, last_err)
            if attempt < 2:
                time.sleep(3)
                continue
            return None, None, f"cookie read failed: {last_err}"
        if attempt < 2:
            _log.debug("li_at not found yet, waiting for Chrome to flush cookies")
            time.sleep(3)
    return None, None, "li_at not found in Chrome's cookie store for .linkedin.com"


def _is_chrome_available() -> bool:
    """Return True if Google Chrome is installed on this system."""
    chrome_paths = [
        "/Applications/Google Chrome.app",
        os.path.expanduser("~/Applications/Google Chrome.app"),
    ]
    return any(os.path.exists(p) for p in chrome_paths)


def _open_in_chrome(url: str) -> bool:
    """Open *url* in Google Chrome directly. Returns True if Chrome was found."""
    import subprocess
    try:
        subprocess.run(
            ["open", "-a", "Google Chrome", url],
            check=True, capture_output=True,
        )
        _log.info("Opened OAuth URL in Chrome")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ── Auth flow configuration ────────────────────────────────────────────────────

# auto       — Playwright login window when its Chromium can reach linkedin.com,
#              otherwise the system browser (Chrome preferred).
# playwright — always use the Playwright login window (error if unavailable).
# browser    — always use the system browser + Chrome cookie-store capture.
AUTH_MODE = os.environ.get("LINKEDIN_AUTH_MODE", "auto").strip().lower()
AUTH_MODES = ("auto", "playwright", "browser")

def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer from the environment, falling back to *default*.

    A bad value logs a warning instead of raising, so a typo in
    LINKEDIN_AUTH_TIMEOUT cannot stop the server or CLI from starting.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        _log.warning("%s=%r is not an integer; using default %d", name, raw, default)
        return default
    if value < minimum:
        _log.warning("%s=%d is below the minimum %d; using default %d", name, value, minimum, default)
        return default
    return value


# Seconds to wait for the user to finish logging in / approving the app.
AUTH_TIMEOUT = _env_int("LINKEDIN_AUTH_TIMEOUT", 300)

# Milliseconds for the headless network probe and initial page loads.
PROBE_TIMEOUT_MS = _env_int("LINKEDIN_PROBE_TIMEOUT_MS", 15000, minimum=100)

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
_FEED_URL = "https://www.linkedin.com/feed/"


class OAuthResult(NamedTuple):
    """Outcome of run_oauth_flow()."""

    token_data: dict
    li_at: Optional[str]
    jsessionid: Optional[str]
    session_error: Optional[str]
    method: str  # "playwright" | "browser"


class PlaywrightLoginUnavailable(RuntimeError):
    """Raised when the Playwright login window cannot even be opened.

    Distinct from user-side failures (timeout, closed window, denied consent) so
    the caller can fall back to the system browser *only* when the user never
    got a chance to interact.
    """


def _cookies_from_list(cookies: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """Extract (li_at, JSESSIONID) values from a Playwright cookie list."""
    li_at = next((c["value"] for c in cookies if c.get("name") == "li_at"), None)
    jsessionid = next((c["value"] for c in cookies if c.get("name") == "JSESSIONID"), None)
    return li_at, jsessionid


def _run_in_thread(fn, *args, **kwargs):
    """Run *fn* on a fresh thread and return its result.

    Playwright's sync API refuses to run inside a thread that owns an asyncio
    loop (the MCP server's tool-dispatch thread). Every Playwright helper in
    this module is synchronous and is invoked through this shim.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args, **kwargs).result()


# ── Playwright helpers (sync API; call via _run_in_thread from async code) ─────

def probe_playwright_network(timeout_ms: int = PROBE_TIMEOUT_MS) -> Optional[str]:
    """Return None if Playwright's Chromium can reach linkedin.com, else why not.

    Any HTTP response counts as reachable (LinkedIn may answer a headless
    request with 999 or a redirect; that is still a working network path).
    Only launch failures and network-level errors count as unreachable — those
    are the cases where the macOS Application Firewall or tools like Little
    Snitch block the bundled Chromium binary.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return "playwright not installed"
    try:
        with _sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            try:
                page = browser.new_page()
                page.goto(
                    "https://www.linkedin.com/",
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
            finally:
                browser.close()
        _log.debug("Playwright network probe: linkedin.com reachable")
        return None
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
        _log.warning("Playwright network probe failed: %s", msg)
        return msg


def _harvest_session(context) -> tuple[Optional[str], Optional[str]]:
    """Read li_at / JSESSIONID from a live Playwright context.

    If li_at is present but JSESSIONID is not, visit /feed/ once so LinkedIn
    issues it. Never navigates when there is no li_at (nothing to refresh).
    """
    li_at, jsessionid = _cookies_from_list(context.cookies("https://www.linkedin.com"))
    if li_at and not jsessionid:
        page = context.new_page()
        try:
            page.goto(_FEED_URL, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
        except Exception as nav_err:
            _log.debug("Feed navigation while harvesting session: %s (non-fatal)", nav_err)
        finally:
            page.close()
        li_at, jsessionid = _cookies_from_list(context.cookies("https://www.linkedin.com"))
    return li_at, jsessionid


def harvest_session_from_profile(
    browser_dir: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Open the persistent profile headlessly and read the LinkedIn session.

    Returns (li_at, jsessionid, error). error is None on success. This is the
    recovery path: once a login has happened inside the Playwright profile,
    the cookies can be re-read at any time without touching Chrome or the
    OS keychain. The profile must not be open elsewhere (Chromium locks it),
    so callers close any live VoyagerClient for the alias first.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return None, None, "playwright not installed"
    if not has_browser_profile(browser_dir):
        return None, None, "no browser profile — run `authenticate` first"
    try:
        ensure_private_dir(browser_dir)
        with _sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                browser_dir, headless=True, args=_LAUNCH_ARGS
            )
            try:
                li_at, jsessionid = _harvest_session(context)
            finally:
                context.close()
    except Exception as exc:
        _log.warning("Session harvest from profile failed: %s", exc)
        return None, None, f"profile read failed: {exc}"
    if not li_at:
        return None, None, "no LinkedIn session in the browser profile (expired or never logged in)"
    _log.info("Harvested LinkedIn session from profile %s", browser_dir)
    return li_at, jsessionid, None


def _init_headless_profile(
    li_at: str,
    jsessionid: Optional[str],
    browser_dir: str,
) -> tuple[Optional[str], Optional[str]]:
    """Seed a headless Playwright profile with cookies captured from Chrome.

    Used only on the system-browser path. Injects li_at (and JSESSIONID if
    present), visits /feed/ so LinkedIn issues or refreshes JSESSIONID, and
    leaves a persistent profile behind for VoyagerClient.

    Returns (jsessionid_final, error_message). On failure the original
    jsessionid is returned unchanged.
    """
    try:
        _log.debug("Initializing Playwright headless profile at %s", browser_dir)
        ensure_private_dir(browser_dir)
        with _sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                browser_dir, headless=True, args=_LAUNCH_ARGS
            )
            try:
                cookies: list[dict] = [
                    {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/"},
                ]
                if jsessionid:
                    cookies.append(
                        {"name": "JSESSIONID", "value": jsessionid, "domain": ".linkedin.com", "path": "/"}
                    )
                context.add_cookies(cookies)
                page = context.new_page()
                try:
                    page.goto(_FEED_URL, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
                except Exception as nav_err:
                    _log.debug("Feed navigation during profile init: %s (non-fatal)", nav_err)
                _, jsessionid_final = _cookies_from_list(context.cookies("https://www.linkedin.com"))
            finally:
                context.close()
        _log.info("Playwright headless profile initialized at %s", browser_dir)
        return jsessionid_final or jsessionid, None
    except Exception as exc:
        _log.warning("Headless profile init failed: %s", exc)
        return jsessionid, f"headless profile init failed: {exc}"


# ── Local callback server, pollable (used by the Playwright login path) ────────

class _CallbackServer:
    """Serve the OAuth callback on a background thread until a result arrives.

    Unlike _wait_for_code(), this does not block the caller, so the caller can
    drive a browser window and watch for it being closed while it waits. Binds
    to 127.0.0.1 only and keeps its state on its own server instance.
    """

    def __init__(self, port: int, expected_state: Optional[str]) -> None:
        self._server = _OAuthCallbackServer(port, expected_state)
        self._server.timeout = 0.5  # makes handle_request() return periodically
        self._stop = False
        self._thread = Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        while not self._stop and not self.done:
            self._server.handle_request()

    def start(self) -> None:
        self._thread.start()

    @property
    def code(self) -> Optional[str]:
        return self._server.auth_code

    @property
    def error(self) -> Optional[str]:
        return self._server.error

    @property
    def done(self) -> bool:
        return self._server.done

    def close(self) -> None:
        """Stop serving and release the socket; returns once the thread has exited."""
        self._stop = True
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self._server.server_close()
        except Exception:
            pass


# ── OAuth flow: Playwright login window ────────────────────────────────────────

def _run_oauth_flow_playwright(
    client_id: str,
    client_secret: str,
    port: int,
    browser_dir: str,
    timeout: int = AUTH_TIMEOUT,
) -> tuple[dict, Optional[str], Optional[str]]:
    """OAuth flow driven by a *headed* Playwright window on the persistent profile.

    The user logs in and approves the app inside that window. Because the
    session is created in the same Chromium profile VoyagerClient later reuses
    headlessly, the cookies match the fingerprint LinkedIn saw at login —
    no Chrome cookie store, no OS keychain decryption, no cookie injection.

    Returns (token_data, li_at, jsessionid).

    Raises PlaywrightLoginUnavailable if the window could not be opened or
    the authorization page could not load (caller may fall back). Raises
    RuntimeError for timeouts, a closed window, or a denied authorization.
    """
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, state)
    ensure_private_dir(browser_dir)

    callback = _CallbackServer(port, state)
    callback.start()
    _log.info("Starting OAuth flow — opening Playwright login window (profile %s)", browser_dir)
    try:
        with _sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    browser_dir, headless=False, args=_LAUNCH_ARGS
                )
            except Exception as exc:
                raise PlaywrightLoginUnavailable(f"could not launch Chromium: {exc}") from exc
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(auth_url, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
                except Exception as exc:
                    raise PlaywrightLoginUnavailable(
                        f"authorization page did not load: {exc}"
                    ) from exc

                deadline = time.monotonic() + timeout
                while not callback.done:
                    if time.monotonic() > deadline:
                        _log.error("OAuth callback timed out after %ds", timeout)
                        raise RuntimeError(
                            "Timed out waiting for LinkedIn authorization. Please try again."
                        )
                    if not context.pages:
                        _log.error("Login window closed before authorization completed")
                        raise RuntimeError(
                            "The login window was closed before authorization completed."
                        )
                    time.sleep(0.5)

                if callback.error:
                    _log.error("OAuth callback error: %s", callback.error)
                    raise RuntimeError(f"LinkedIn authorization failed: {callback.error}")

                li_at, jsessionid = _harvest_session(context)
            finally:
                context.close()
    finally:
        callback.close()

    _log.info("Authorization code received; exchanging for token")
    token_data = exchange_code(callback.code, client_id, client_secret, redirect_uri)
    token_data["_obtained_at"] = int(time.time())
    if li_at:
        _log.info("Captured LinkedIn session from the Playwright login profile")
    else:
        _log.warning("Playwright login completed but li_at was not found in the profile")
    return token_data, li_at, jsessionid


# ── OAuth flow: system browser ─────────────────────────────────────────────────

def _run_oauth_flow_browser(client_id: str, client_secret: str, port: int) -> tuple[dict, bool]:
    """
    OAuth flow using system browser + local HTTP callback server.

    Returns (token_data, opened_via_chrome).

    Prefers Chrome explicitly (so browser-cookie3 can capture li_at afterwards)
    and falls back to webbrowser.open() when Chrome isn't available.
    """
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, state)

    _log.info("Starting OAuth flow — opening system browser to LinkedIn authorization page")
    opened_via_chrome = _open_in_chrome(auth_url)
    if not opened_via_chrome:
        _log.info("Chrome not found; using default browser (Voyager session capture unavailable)")
        webbrowser.open(auth_url)

    _log.debug("Waiting for OAuth callback on localhost:%d (timeout 120s)", port)
    code, error = _wait_for_code(port, expected_state=state)

    if error:
        _log.error("OAuth callback error: %s", error)
        raise RuntimeError(f"LinkedIn authorization failed: {error}")
    if not code:
        _log.error("OAuth callback timed out")
        raise RuntimeError("Timed out waiting for LinkedIn authorization. Please try again.")

    _log.info("Authorization code received; exchanging for token")
    token_data = exchange_code(code, client_id, client_secret, redirect_uri)
    token_data["_obtained_at"] = int(time.time())
    return token_data, opened_via_chrome


def _run_oauth_flow_browser_with_capture(
    client_id: str, client_secret: str, port: int, browser_dir: str
) -> OAuthResult:
    """System-browser flow followed by best-effort Chrome cookie capture."""
    token_data, opened_via_chrome = _run_oauth_flow_browser(client_id, client_secret, port)

    if not opened_via_chrome:
        return OAuthResult(
            token_data, None, None,
            "Chrome not found; Voyager session not captured. "
            "Run `set_web_session` with cookies from your browser to enable Voyager tools.",
            "browser",
        )

    li_at, jsessionid, cookie_err = _capture_chrome_linkedin_cookies()
    if not li_at:
        _log.warning("Voyager session capture failed: %s", cookie_err)
        return OAuthResult(token_data, None, None, cookie_err, "browser")

    if _PLAYWRIGHT_AVAILABLE:
        jsessionid, _ = _init_headless_profile(li_at, jsessionid, browser_dir)
    else:
        _log.info("Playwright not available — skipping headless profile init")

    return OAuthResult(token_data, li_at, jsessionid, None, "browser")


# ── OAuth flow: entry point ────────────────────────────────────────────────────

def resolve_auth_mode(mode: Optional[str] = None) -> str:
    """Normalize the requested auth mode, defaulting to LINKEDIN_AUTH_MODE / auto."""
    mode = (mode or AUTH_MODE or "auto").strip().lower()
    if mode not in AUTH_MODES:
        _log.warning("Unknown LINKEDIN_AUTH_MODE %r; using 'auto'", mode)
        return "auto"
    return mode


def _run_oauth_flow_sync(
    client_id: str,
    client_secret: str,
    port: int,
    browser_dir: str,
    mode: str,
) -> OAuthResult:
    if mode != "browser":
        if not _PLAYWRIGHT_AVAILABLE:
            if mode == "playwright":
                raise RuntimeError(
                    "LINKEDIN_AUTH_MODE=playwright but Playwright is not installed. "
                    "Run `pip install playwright && playwright install chromium`."
                )
            _log.info("Playwright not installed; using system browser for OAuth")
        else:
            probe_err = probe_playwright_network() if mode == "auto" else None
            if probe_err:
                _log.warning(
                    "Playwright Chromium cannot reach linkedin.com (%s); "
                    "falling back to the system browser", probe_err,
                )
            else:
                try:
                    token_data, li_at, jsessionid = _run_oauth_flow_playwright(
                        client_id, client_secret, port, browser_dir
                    )
                except PlaywrightLoginUnavailable as exc:
                    if mode == "playwright":
                        raise
                    _log.warning(
                        "Playwright login window unavailable (%s); "
                        "falling back to the system browser", exc,
                    )
                else:
                    err = (
                        None if li_at else
                        "login completed but no li_at cookie was found in the Playwright profile; "
                        "run `refresh_web_session` after logging in, or `set_web_session`"
                    )
                    return OAuthResult(token_data, li_at, jsessionid, err, "playwright")

    return _run_oauth_flow_browser_with_capture(client_id, client_secret, port, browser_dir)


async def run_oauth_flow(
    client_id: str,
    client_secret: str,
    port: int = DEFAULT_PORT,
    browser_dir: str = DEFAULT_BROWSER_DIR,
    mode: Optional[str] = None,
) -> OAuthResult:
    """
    Full interactive OAuth flow. Returns an OAuthResult.

    Strategy (mode defaults to LINKEDIN_AUTH_MODE, normally "auto"):
      1. Playwright login window on the per-alias persistent profile, when the
         bundled Chromium can reach linkedin.com. The Voyager session is read
         straight from that profile after the user approves the app.
      2. Otherwise the system browser (Chrome preferred) + local callback
         server, followed by best-effort capture from Chrome's cookie store
         and a headless profile seeded with those cookies.

    Everything Playwright-related runs on a worker thread because its sync
    API cannot run on the server's asyncio thread.

    Raises RuntimeError on failure or timeout.
    """
    resolved = resolve_auth_mode(mode)
    return await asyncio.to_thread(
        _run_oauth_flow_sync, client_id, client_secret, port, browser_dir, resolved,
    )


# ── Token persistence (per-user, keyed by alias) ───────────────────────────────

def save_token(token_data: dict, alias: str) -> None:
    """Persist token_data to OS keychain under alias, falling back to a per-alias file."""
    key = f"{_KR_KEY_TOKEN}:{alias}"
    if _HAS_KEYRING:
        try:
            keyring.set_password(_KR_SERVICE, key, json.dumps(token_data))
            _log.debug("Token for '%s' saved to OS keychain", alias)
            return
        except Exception as exc:
            _log.debug("Keychain save failed, using file: %s", exc)
    path = _token_path(alias)
    _write_private_json(path, token_data)
    _log.debug("Token for '%s' saved to %s", alias, path)


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
    *buffer_seconds*). Returns False when expiry information is unavailable.
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
    _log.debug("Token for '%s' deleted: %s", alias, deleted)
    return deleted


# ── Web session persistence (Voyager cookies, per-user) ───────────────────────

def save_web_session(li_at: str, jsessionid: str, alias: str) -> None:
    """Persist li_at and JSESSIONID cookies for alias, preferring OS keychain."""
    key = f"{_KR_KEY}:{alias}"
    data = {"li_at": li_at, "jsessionid": jsessionid, "_saved_at": int(time.time())}
    if _HAS_KEYRING:
        try:
            keyring.set_password(_KR_SERVICE, key, json.dumps(data))
            _log.debug("Web session for '%s' saved to OS keychain", alias)
            return
        except Exception as exc:
            _log.debug("Keychain session save failed, using file: %s", exc)
    path = _session_path(alias)
    _write_private_json(path, data)
    _log.debug("Web session for '%s' saved to %s", alias, path)


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
    _log.debug("Web session for '%s' deleted: %s", alias, deleted)
    return deleted


# ── App credential persistence ─────────────────────────────────────────────────

def save_credentials(client_id: str, client_secret: str) -> bool:
    """Store app credentials in OS keychain. Returns True if saved, False if keyring unavailable."""
    if not _HAS_KEYRING:
        return False
    try:
        data = {"client_id": client_id, "client_secret": client_secret}
        keyring.set_password(_KR_SERVICE, _KR_KEY_CREDS, json.dumps(data))
        _log.debug("App credentials saved to OS keychain")
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
