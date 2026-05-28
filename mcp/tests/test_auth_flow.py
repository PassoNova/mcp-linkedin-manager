"""
Tests for the OAuth flow in auth.py.

Key invariant: run_oauth_flow NEVER uses Playwright for the browser step.
It always uses the system browser (Chrome preferred) + local HTTP callback server.
Playwright is only used for the optional headless profile initialization step,
which runs AFTER the token has been obtained.
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run(coro):
    """Run a coroutine synchronously (avoids pytest-asyncio dependency)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fake_token(alias: str = "work") -> dict:
    return {
        "access_token": f"tok-{alias}",
        "token_type": "Bearer",
        "expires_in": 5_184_000,
        "scope": "email openid profile w_member_social",
        "_obtained_at": int(time.time()),
    }


# ── run_oauth_flow: system browser is always used ─────────────────────────────

class TestRunOAuthFlowUsesSystemBrowser:
    """run_oauth_flow must never call Playwright for the browser step."""

    def test_uses_system_browser_not_playwright(self, monkeypatch):
        import auth

        mock_browser_flow = MagicMock(return_value=(_fake_token(), True))
        monkeypatch.setattr(auth, "_run_oauth_flow_browser", mock_browser_flow)
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=("li_at_val", "jsess_val", None)))
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)

        token, li_at, jsess, err = _run(auth.run_oauth_flow("cid", "csec", port=9999))

        mock_browser_flow.assert_called_once_with("cid", "csec", 9999)
        assert li_at == "li_at_val"
        assert err is None

    def test_playwright_not_called_for_oauth_dance(self, monkeypatch):
        """Even when Playwright IS installed, it must not drive the OAuth browser step."""
        import auth

        monkeypatch.setattr(auth, "_run_oauth_flow_browser",
                            MagicMock(return_value=(_fake_token(), True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=("li_at_val", "jsess_val", None)))

        playwright_spy = AsyncMock(return_value=("jsess_val", None))
        monkeypatch.setattr(auth, "_init_headless_profile", playwright_spy)
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)

        _run(auth.run_oauth_flow("cid", "csec", port=9999, browser_dir="/tmp/bdir"))

        playwright_spy.assert_called_once()
        args = playwright_spy.call_args[0]
        assert args[0] == "li_at_val"
        assert args[1] == "jsess_val"
        assert args[2] == "/tmp/bdir"


class TestRunOAuthFlowChromeFallback:
    def test_no_voyager_when_chrome_not_used(self, monkeypatch):
        import auth

        monkeypatch.setattr(auth, "_run_oauth_flow_browser",
                            MagicMock(return_value=(_fake_token(), False)))

        token, li_at, jsess, err = _run(auth.run_oauth_flow("cid", "csec", port=9999))

        assert li_at is None
        assert jsess is None
        assert err is not None
        assert "Chrome" in err or "set_web_session" in err

    def test_no_voyager_when_cookie_capture_fails(self, monkeypatch):
        import auth

        monkeypatch.setattr(auth, "_run_oauth_flow_browser",
                            MagicMock(return_value=(_fake_token(), True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=(None, None, "li_at not found")))
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)

        token, li_at, jsess, err = _run(auth.run_oauth_flow("cid", "csec", port=9999))

        assert li_at is None
        assert err == "li_at not found"

    def test_voyager_without_playwright(self, monkeypatch):
        """When Playwright is not installed, li_at is still returned from cookies."""
        import auth

        monkeypatch.setattr(auth, "_run_oauth_flow_browser",
                            MagicMock(return_value=(_fake_token(), True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=("li_at_val", "jsess_val", None)))
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)

        token, li_at, jsess, err = _run(auth.run_oauth_flow("cid", "csec", port=9999))

        assert li_at == "li_at_val"
        assert jsess == "jsess_val"
        assert err is None

    def test_token_always_returned_on_success(self, monkeypatch):
        import auth

        expected = _fake_token()
        monkeypatch.setattr(auth, "_run_oauth_flow_browser",
                            MagicMock(return_value=(expected, True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=("li_at_val", "jsess_val", None)))
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)

        token, _, _, _ = _run(auth.run_oauth_flow("cid", "csec", port=9999))

        assert token["access_token"] == expected["access_token"]


# ── _run_oauth_flow_browser ────────────────────────────────────────────────────

class TestRunOAuthFlowBrowser:
    def test_opens_chrome_when_available(self, monkeypatch):
        import auth

        mock_open_chrome = MagicMock(return_value=True)
        monkeypatch.setattr(auth, "_open_in_chrome", mock_open_chrome)

        mock_wait = MagicMock(return_value=("authcode123", None))
        monkeypatch.setattr(auth, "_wait_for_code", mock_wait)

        mock_exchange = MagicMock(return_value={"access_token": "tok", "expires_in": 5_184_000})
        monkeypatch.setattr(auth, "exchange_code", mock_exchange)

        token_data, opened_chrome = auth._run_oauth_flow_browser("cid", "csec", port=9999)

        mock_open_chrome.assert_called_once()
        assert opened_chrome is True
        assert token_data["access_token"] == "tok"

    def test_falls_back_to_webbrowser_when_no_chrome(self, monkeypatch):
        import auth

        monkeypatch.setattr(auth, "_open_in_chrome", MagicMock(return_value=False))
        mock_wb = MagicMock()
        monkeypatch.setattr(auth, "webbrowser", mock_wb)

        mock_wait = MagicMock(return_value=("code", None))
        monkeypatch.setattr(auth, "_wait_for_code", mock_wait)

        mock_exchange = MagicMock(return_value={"access_token": "tok2", "expires_in": 5_184_000})
        monkeypatch.setattr(auth, "exchange_code", mock_exchange)

        token_data, opened_chrome = auth._run_oauth_flow_browser("cid", "csec", port=9999)

        mock_wb.open.assert_called_once()
        assert opened_chrome is False

    def test_raises_on_timeout(self, monkeypatch):
        import auth

        monkeypatch.setattr(auth, "_open_in_chrome", MagicMock(return_value=False))
        monkeypatch.setattr(auth, "webbrowser", MagicMock())
        monkeypatch.setattr(auth, "_wait_for_code", MagicMock(return_value=(None, None)))

        with pytest.raises(RuntimeError, match="Timed out"):
            auth._run_oauth_flow_browser("cid", "csec", port=9999)

    def test_raises_on_oauth_error(self, monkeypatch):
        import auth

        monkeypatch.setattr(auth, "_open_in_chrome", MagicMock(return_value=False))
        monkeypatch.setattr(auth, "webbrowser", MagicMock())
        monkeypatch.setattr(auth, "_wait_for_code", MagicMock(return_value=(None, "access_denied")))

        with pytest.raises(RuntimeError, match="access_denied"):
            auth._run_oauth_flow_browser("cid", "csec", port=9999)

    def test_state_passed_to_wait_for_code(self, monkeypatch):
        """The CSRF state generated in _run_oauth_flow_browser must be passed to _wait_for_code."""
        import auth

        captured_state = []

        def fake_wait(port, expected_state=None, **kw):
            captured_state.append(expected_state)
            return ("code", None)

        monkeypatch.setattr(auth, "_open_in_chrome", MagicMock(return_value=False))
        monkeypatch.setattr(auth, "webbrowser", MagicMock())
        monkeypatch.setattr(auth, "_wait_for_code", fake_wait)
        monkeypatch.setattr(auth, "exchange_code",
                            MagicMock(return_value={"access_token": "t", "expires_in": 5_184_000}))

        auth._run_oauth_flow_browser("cid", "csec", port=9999)

        assert captured_state[0] is not None
        assert len(captured_state[0]) >= 16  # token_urlsafe(16)


# ── _capture_chrome_linkedin_cookies ──────────────────────────────────────────

class TestCaptureChromeLinkedInCookies:
    def test_returns_error_when_browser_cookie3_unavailable(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_BROWSER_COOKIE3", False)

        li_at, jsess, err = auth._capture_chrome_linkedin_cookies()

        assert li_at is None
        assert err == "browser-cookie3 not installed"

    def test_returns_li_at_when_found(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_BROWSER_COOKIE3", True)

        class FakeCookie:
            def __init__(self, name, value):
                self.name = name
                self.value = value

        mock_bc3 = MagicMock()
        mock_bc3.chrome.return_value = [
            FakeCookie("li_at", "my-li-at-value"),
            FakeCookie("JSESSIONID", '"ajax:12345"'),
        ]
        monkeypatch.setattr(auth, "browser_cookie3", mock_bc3)

        li_at, jsess, err = auth._capture_chrome_linkedin_cookies()

        assert li_at == "my-li-at-value"
        assert err is None

    def test_returns_error_when_li_at_not_found(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_BROWSER_COOKIE3", True)

        mock_bc3 = MagicMock()
        mock_bc3.chrome.return_value = []  # no cookies
        monkeypatch.setattr(auth, "browser_cookie3", mock_bc3)
        monkeypatch.setattr(auth, "time", MagicMock(sleep=MagicMock()))

        li_at, jsess, err = auth._capture_chrome_linkedin_cookies()

        assert li_at is None
        assert err is not None
        assert "li_at" in err
