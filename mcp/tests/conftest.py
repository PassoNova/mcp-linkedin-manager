"""Shared fixtures for all LinkedIn MCP tests.

Key invariant: tests NEVER touch ~/.linkedin_mcp_*.  The autouse
`_isolate_files` fixture redirects LINKEDIN_TOKEN_FILE and
LINKEDIN_SESSION_FILE to tmp paths for every test in the suite.
"""
from __future__ import annotations

import json
import time

import pytest


# ---------------------------------------------------------------------------
# File isolation — always active, never touches real dot-files
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_files(tmp_path, monkeypatch):
    """Redirect token and session files to tmp_path for every test."""
    monkeypatch.setenv("LINKEDIN_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("LINKEDIN_SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setenv("LINKEDIN_BROWSER_DIR", str(tmp_path / "browser"))


# ---------------------------------------------------------------------------
# Token / session helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_token_file(tmp_path):
    """Write a minimal valid token JSON; return the path."""
    data = {
        "access_token": "fake-access-token",
        "token_type": "Bearer",
        "expires_in": 5_184_000,
        "scope": "email,openid,profile,w_member_social",
        "_obtained_at": int(time.time()),
    }
    path = tmp_path / "token.json"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture()
def fake_session_file(tmp_path):
    """Write a minimal valid session JSON; return the path."""
    data = {
        "li_at": "fake-li-at-cookie",
        "jsessionid": "fake-jsessionid",
        "_saved_at": int(time.time()),
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture()
def env_credentials(monkeypatch):
    """Set required OAuth credential env vars."""
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "test-client-secret")
