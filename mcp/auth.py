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


def run_oauth_flow(client_id: str, client_secret: str, port: int = DEFAULT_PORT) -> dict:
    """
    Full interactive OAuth flow.

    Opens the user's browser, waits for the callback on *localhost:port*,
    exchanges the code, and returns the raw token response dict.

    Raises RuntimeError on failure or timeout.
    """
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
    # Annotate with the wall-clock expiry time so we can detect staleness later.
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
