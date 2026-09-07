"""
Tests for the OAuth flow in auth.py.

Strategy under test (run_oauth_flow):
  auto       → Playwright login window when the headless probe can reach
               linkedin.com, otherwise the system browser + Chrome cookie capture
  playwright → Playwright login window only
  browser    → system browser only

Everything Playwright-related is mocked; no test opens a browser or touches
the network. The one exception is _CallbackServer, which is exercised against
a real loopback HTTP request.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run(coro):
    """Run a coroutine synchronously (avoids pytest-asyncio dependency)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fake_token(alias: str = "work") -> dict:
    return {
        "access_token": f"tok-{alias}",
        "token_type": "Bearer",
        "expires_in": 5_184_000,
        "scope": "email openid profile w_member_social",
        "_obtained_at": int(time.time()),
    }


def _cookie(name: str, value: str) -> dict:
    return {"name": name, "value": value, "domain": ".linkedin.com", "path": "/"}


def _fake_sync_playwright(context: MagicMock, launch_error: Exception | None = None) -> MagicMock:
    """Build a stand-in for auth._sync_playwright whose persistent context is *context*."""
    chromium = MagicMock()
    if launch_error is not None:
        chromium.launch_persistent_context.side_effect = launch_error
    else:
        chromium.launch_persistent_context.return_value = context
    browser = MagicMock()
    browser.new_page.return_value = MagicMock()
    chromium.launch.return_value = browser
    pw = MagicMock()
    pw.chromium = chromium
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=pw)
    cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=cm)


def _fake_context(cookies_by_call: list[list[dict]], pages: list | None = None) -> MagicMock:
    """Persistent-context mock returning successive cookie lists on .cookies()."""
    ctx = MagicMock()
    ctx.cookies.side_effect = list(cookies_by_call) + [cookies_by_call[-1]] * 10
    ctx.pages = pages if pages is not None else [MagicMock()]
    ctx.new_page.return_value = MagicMock()
    return ctx


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    import auth
    monkeypatch.setattr(auth.time, "sleep", lambda *_: None)


# ── resolve_auth_mode ──────────────────────────────────────────────────────────

class TestResolveAuthMode:
    def test_defaults_to_auto(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "AUTH_MODE", "auto")
        assert auth.resolve_auth_mode() == "auto"

    def test_explicit_argument_wins(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "AUTH_MODE", "browser")
        assert auth.resolve_auth_mode("playwright") == "playwright"

    def test_module_default_used_when_no_argument(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "AUTH_MODE", "browser")
        assert auth.resolve_auth_mode() == "browser"

    def test_unknown_mode_falls_back_to_auto(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "AUTH_MODE", "auto")
        assert auth.resolve_auth_mode("bogus") == "auto"

    def test_case_and_whitespace_normalized(self):
        import auth
        assert auth.resolve_auth_mode("  Browser ") == "browser"


# ── run_oauth_flow: strategy selection ────────────────────────────────────────

class TestRunOAuthFlowStrategy:
    @pytest.fixture()
    def paths(self, monkeypatch):
        """Mock both concrete flows and the probe; return the mocks."""
        import auth
        pw_flow = MagicMock(return_value=(_fake_token(), "li_pw", "js_pw"))
        br_flow = MagicMock(return_value=auth.OAuthResult(_fake_token(), "li_br", "js_br", None, "browser"))
        probe = MagicMock(return_value=None)
        monkeypatch.setattr(auth, "_run_oauth_flow_playwright", pw_flow)
        monkeypatch.setattr(auth, "_run_oauth_flow_browser_with_capture", br_flow)
        monkeypatch.setattr(auth, "probe_playwright_network", probe)
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        return {"pw": pw_flow, "browser": br_flow, "probe": probe}

    def test_auto_uses_playwright_when_probe_passes(self, paths):
        import auth
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999, browser_dir="/tmp/b", mode="auto"))
        paths["probe"].assert_called_once()
        paths["pw"].assert_called_once_with("cid", "csec", 9999, "/tmp/b")
        paths["browser"].assert_not_called()
        assert result.method == "playwright"
        assert result.li_at == "li_pw"
        assert result.jsessionid == "js_pw"
        assert result.session_error is None

    def test_auto_falls_back_to_browser_when_probe_fails(self, paths):
        import auth
        paths["probe"].return_value = "Error: net::ERR_CONNECTION_REFUSED"
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999, browser_dir="/tmp/b", mode="auto"))
        paths["pw"].assert_not_called()
        paths["browser"].assert_called_once_with("cid", "csec", 9999, "/tmp/b")
        assert result.method == "browser"
        assert result.li_at == "li_br"

    def test_auto_falls_back_when_login_window_unavailable(self, paths):
        import auth
        paths["pw"].side_effect = auth.PlaywrightLoginUnavailable("could not launch Chromium")
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="auto"))
        paths["browser"].assert_called_once()
        assert result.method == "browser"

    def test_auto_does_not_fall_back_after_user_side_failure(self, paths):
        """A timeout or closed window means the user already saw a browser; never open a second one."""
        import auth
        paths["pw"].side_effect = RuntimeError("Timed out waiting for LinkedIn authorization.")
        with pytest.raises(RuntimeError, match="Timed out"):
            _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="auto"))
        paths["browser"].assert_not_called()

    def test_playwright_mode_skips_probe_and_never_falls_back(self, paths):
        import auth
        paths["pw"].side_effect = auth.PlaywrightLoginUnavailable("blocked")
        with pytest.raises(auth.PlaywrightLoginUnavailable):
            _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="playwright"))
        paths["probe"].assert_not_called()
        paths["browser"].assert_not_called()

    def test_playwright_mode_without_playwright_installed_raises(self, paths, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="not installed"):
            _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="playwright"))

    def test_browser_mode_never_touches_playwright(self, paths):
        import auth
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="browser"))
        paths["probe"].assert_not_called()
        paths["pw"].assert_not_called()
        paths["browser"].assert_called_once()
        assert result.method == "browser"

    def test_auto_without_playwright_installed_uses_browser(self, paths, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="auto"))
        paths["probe"].assert_not_called()
        assert result.method == "browser"

    def test_playwright_login_without_li_at_reports_recovery_hint(self, paths):
        import auth
        paths["pw"].return_value = (_fake_token(), None, None)
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999, mode="auto"))
        assert result.method == "playwright"
        assert result.li_at is None
        assert "refresh_web_session" in result.session_error

    def test_module_auth_mode_used_when_mode_omitted(self, paths, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "AUTH_MODE", "browser")
        result = _run(auth.run_oauth_flow("cid", "csec", port=9999))
        assert result.method == "browser"
        paths["pw"].assert_not_called()


# ── System-browser path + Chrome cookie capture ────────────────────────────────

class TestBrowserPathWithCapture:
    def test_no_voyager_when_chrome_not_used(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_run_oauth_flow_browser", MagicMock(return_value=(_fake_token(), False)))
        result = auth._run_oauth_flow_browser_with_capture("cid", "csec", 9999, "/tmp/b")
        assert result.li_at is None and result.jsessionid is None
        assert "Chrome" in result.session_error or "set_web_session" in result.session_error
        assert result.method == "browser"

    def test_no_voyager_when_cookie_capture_fails(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_run_oauth_flow_browser", MagicMock(return_value=(_fake_token(), True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=(None, None, "cookie read failed: Unable to get key")))
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)
        result = auth._run_oauth_flow_browser_with_capture("cid", "csec", 9999, "/tmp/b")
        assert result.li_at is None
        assert result.session_error == "cookie read failed: Unable to get key"

    def test_headless_profile_seeded_with_captured_cookies(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_run_oauth_flow_browser", MagicMock(return_value=(_fake_token(), True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=("li_at_val", "jsess_val", None)))
        init = MagicMock(return_value=("jsess_fresh", None))
        monkeypatch.setattr(auth, "_init_headless_profile", init)
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        result = auth._run_oauth_flow_browser_with_capture("cid", "csec", 9999, "/tmp/bdir")
        init.assert_called_once_with("li_at_val", "jsess_val", "/tmp/bdir")
        assert result.li_at == "li_at_val"
        assert result.jsessionid == "jsess_fresh"
        assert result.session_error is None

    def test_voyager_without_playwright(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_run_oauth_flow_browser", MagicMock(return_value=(_fake_token(), True)))
        monkeypatch.setattr(auth, "_capture_chrome_linkedin_cookies",
                            MagicMock(return_value=("li_at_val", "jsess_val", None)))
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)
        result = auth._run_oauth_flow_browser_with_capture("cid", "csec", 9999, "/tmp/b")
        assert (result.li_at, result.jsessionid, result.session_error) == ("li_at_val", "jsess_val", None)

    def test_token_always_returned(self, monkeypatch):
        import auth
        expected = _fake_token()
        monkeypatch.setattr(auth, "_run_oauth_flow_browser", MagicMock(return_value=(expected, False)))
        result = auth._run_oauth_flow_browser_with_capture("cid", "csec", 9999, "/tmp/b")
        assert result.token_data["access_token"] == expected["access_token"]


# ── _run_oauth_flow_browser ────────────────────────────────────────────────────

class TestRunOAuthFlowBrowser:
    def test_opens_chrome_when_available(self, monkeypatch):
        import auth
        mock_open_chrome = MagicMock(return_value=True)
        monkeypatch.setattr(auth, "_open_in_chrome", mock_open_chrome)
        monkeypatch.setattr(auth, "_wait_for_code", MagicMock(return_value=("authcode123", None)))
        monkeypatch.setattr(auth, "exchange_code",
                            MagicMock(return_value={"access_token": "tok", "expires_in": 5_184_000}))
        token_data, opened_chrome = auth._run_oauth_flow_browser("cid", "csec", port=9999)
        mock_open_chrome.assert_called_once()
        assert opened_chrome is True
        assert token_data["access_token"] == "tok"

    def test_falls_back_to_webbrowser_when_no_chrome(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_open_in_chrome", MagicMock(return_value=False))
        mock_wb = MagicMock()
        monkeypatch.setattr(auth, "webbrowser", mock_wb)
        monkeypatch.setattr(auth, "_wait_for_code", MagicMock(return_value=("code", None)))
        monkeypatch.setattr(auth, "exchange_code",
                            MagicMock(return_value={"access_token": "tok2", "expires_in": 5_184_000}))
        _, opened_chrome = auth._run_oauth_flow_browser("cid", "csec", port=9999)
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
        assert len(captured_state[0]) >= 16


# ── _capture_chrome_linkedin_cookies ──────────────────────────────────────────

class TestCaptureChromeLinkedInCookies:
    def test_returns_error_when_browser_cookie3_unavailable(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_BROWSER_COOKIE3", False)
        li_at, _, err = auth._capture_chrome_linkedin_cookies()
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
        mock_bc3.chrome.return_value = [FakeCookie("li_at", "my-li-at-value"), FakeCookie("JSESSIONID", '"ajax:1"')]
        monkeypatch.setattr(auth, "browser_cookie3", mock_bc3)
        li_at, _, err = auth._capture_chrome_linkedin_cookies()
        assert li_at == "my-li-at-value"
        assert err is None

    def test_returns_error_when_li_at_not_found(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_BROWSER_COOKIE3", True)
        mock_bc3 = MagicMock()
        mock_bc3.chrome.return_value = []
        monkeypatch.setattr(auth, "browser_cookie3", mock_bc3)
        li_at, _, err = auth._capture_chrome_linkedin_cookies()
        assert li_at is None
        assert "li_at" in err

    def test_returns_error_when_keychain_decryption_fails(self, monkeypatch):
        """The failure this redesign exists for: browser-cookie3 cannot get Chrome's Safe Storage key."""
        import auth
        monkeypatch.setattr(auth, "_HAS_BROWSER_COOKIE3", True)
        mock_bc3 = MagicMock()
        mock_bc3.chrome.side_effect = RuntimeError("Unable to get key for cookie decryption")
        monkeypatch.setattr(auth, "browser_cookie3", mock_bc3)
        li_at, _, err = auth._capture_chrome_linkedin_cookies()
        assert li_at is None
        assert "Unable to get key" in err


# ── Session harvesting from a Playwright context / profile ────────────────────

class TestHarvestSession:
    def test_reads_both_cookies_without_navigating(self):
        import auth
        ctx = _fake_context([[_cookie("li_at", "L"), _cookie("JSESSIONID", '"ajax:9"')]])
        assert auth._harvest_session(ctx) == ("L", '"ajax:9"')
        ctx.new_page.assert_not_called()

    def test_visits_feed_once_when_jsessionid_missing(self):
        import auth
        ctx = _fake_context([[_cookie("li_at", "L")], [_cookie("li_at", "L"), _cookie("JSESSIONID", "J")]])
        li_at, jsess = auth._harvest_session(ctx)
        assert (li_at, jsess) == ("L", "J")
        ctx.new_page.assert_called_once()
        page = ctx.new_page.return_value
        page.goto.assert_called_once()
        assert "/feed/" in page.goto.call_args[0][0]
        page.close.assert_called_once()

    def test_does_not_navigate_without_li_at(self):
        import auth
        ctx = _fake_context([[]])
        assert auth._harvest_session(ctx) == (None, None)
        ctx.new_page.assert_not_called()

    def test_profile_missing(self, monkeypatch, tmp_path):
        import auth
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        li_at, _, err = auth.harvest_session_from_profile(str(tmp_path / "nope"))
        assert li_at is None and "authenticate" in err

    def test_playwright_missing(self, monkeypatch, tmp_path):
        import auth
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)
        (tmp_path / "x").write_text("")
        li_at, _, err = auth.harvest_session_from_profile(str(tmp_path))
        assert li_at is None and "playwright" in err

    def test_reads_cookies_from_profile_headlessly(self, monkeypatch, tmp_path):
        import auth
        (tmp_path / "Default").mkdir()
        ctx = _fake_context([[_cookie("li_at", "L"), _cookie("JSESSIONID", "J")]])
        fake_pw = _fake_sync_playwright(ctx)
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright", fake_pw)
        li_at, jsess, err = auth.harvest_session_from_profile(str(tmp_path))
        assert (li_at, jsess, err) == ("L", "J", None)
        call = fake_pw.return_value.__enter__.return_value.chromium.launch_persistent_context.call_args
        assert call[0][0] == str(tmp_path)
        assert call[1]["headless"] is True
        ctx.close.assert_called_once()

    def test_expired_profile_reports_error(self, monkeypatch, tmp_path):
        import auth
        (tmp_path / "Default").mkdir()
        ctx = _fake_context([[]])
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright", _fake_sync_playwright(ctx))
        li_at, _, err = auth.harvest_session_from_profile(str(tmp_path))
        assert li_at is None and "expired" in err

    def test_launch_failure_reports_error(self, monkeypatch, tmp_path):
        import auth
        (tmp_path / "Default").mkdir()
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright",
                            _fake_sync_playwright(MagicMock(), launch_error=RuntimeError("profile locked")))
        li_at, _, err = auth.harvest_session_from_profile(str(tmp_path))
        assert li_at is None and "profile locked" in err


# ── probe_playwright_network ──────────────────────────────────────────────────

class TestProbe:
    def test_not_installed(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", False)
        assert auth.probe_playwright_network() == "playwright not installed"

    def test_reachable_returns_none_and_closes_browser(self, monkeypatch):
        import auth
        fake_pw = _fake_sync_playwright(MagicMock())
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright", fake_pw)
        assert auth.probe_playwright_network() is None
        browser = fake_pw.return_value.__enter__.return_value.chromium.launch.return_value
        browser.close.assert_called_once()

    def test_network_error_returns_message(self, monkeypatch):
        import auth
        fake_pw = _fake_sync_playwright(MagicMock())
        page = fake_pw.return_value.__enter__.return_value.chromium.launch.return_value.new_page.return_value
        page.goto.side_effect = RuntimeError("net::ERR_CONNECTION_REFUSED at https://www.linkedin.com/")
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright", fake_pw)
        err = auth.probe_playwright_network()
        assert err is not None and "ERR_CONNECTION_REFUSED" in err


# ── _CallbackServer (real loopback request) ───────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_done(cb, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not cb.done and time.monotonic() < deadline:
        time.sleep(0.02)


class TestCallbackServer:
    def test_receives_code_and_stops(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth.time, "sleep", time.sleep)  # real sleeps for the loopback wait
        port = _free_port()
        cb = auth._CallbackServer(port, "state123")
        cb.start()
        try:
            assert not cb.done
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=state123", timeout=5) as r:
                assert r.status == 200
            _wait_done(cb)
            assert cb.code == "abc"
            assert cb.error is None
        finally:
            cb.close()

    def test_state_mismatch_is_an_error(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth.time, "sleep", time.sleep)
        port = _free_port()
        cb = auth._CallbackServer(port, "expected")
        cb.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=wrong", timeout=5)
            assert exc.value.code == 400
            _wait_done(cb)
            assert cb.code is None
            assert "State mismatch" in cb.error
        finally:
            cb.close()


# ── _run_oauth_flow_playwright ─────────────────────────────────────────────────

class _FakeCallback:
    """Stand-in for _CallbackServer: reports done after *ticks* polls."""

    instances: list = []

    def __init__(self, port, expected_state, *, ticks=2, code="code-1", error=None):
        self.port, self.expected_state = port, expected_state
        self._ticks, self.code, self.error = ticks, None, None
        self._final_code, self._final_error = code, error
        self.started = self.closed = False
        _FakeCallback.instances.append(self)

    def start(self):
        self.started = True

    @property
    def done(self):
        if self._ticks > 0:
            self._ticks -= 1
            return False
        self.code, self.error = self._final_code, self._final_error
        return True

    def close(self):
        self.closed = True


class TestRunOAuthFlowPlaywright:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        import auth
        _FakeCallback.instances.clear()
        self.auth = auth
        self.browser_dir = str(tmp_path / "profile")
        self.exchange = MagicMock(return_value={"access_token": "tok", "expires_in": 5_184_000})
        monkeypatch.setattr(auth, "exchange_code", self.exchange)
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)

    def _install(self, monkeypatch, ctx, callback_factory=None, launch_error=None):
        monkeypatch.setattr(self.auth, "_sync_playwright", _fake_sync_playwright(ctx, launch_error=launch_error))
        monkeypatch.setattr(self.auth, "_CallbackServer", callback_factory or _FakeCallback)

    def test_happy_path_returns_token_and_session(self, monkeypatch):
        page = MagicMock()
        ctx = _fake_context([[_cookie("li_at", "L"), _cookie("JSESSIONID", "J")]], pages=[page])
        self._install(monkeypatch, ctx)

        token, li_at, jsess = self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir)

        assert token["access_token"] == "tok" and "_obtained_at" in token
        assert (li_at, jsess) == ("L", "J")
        # Headed window on the persistent profile.
        pw = self.auth._sync_playwright.return_value.__enter__.return_value
        args, kwargs = pw.chromium.launch_persistent_context.call_args
        assert args[0] == self.browser_dir and kwargs["headless"] is False
        # Authorization URL opened in the profile's first page, with the CSRF state.
        url = page.goto.call_args[0][0]
        cb = _FakeCallback.instances[0]
        assert url.startswith(self.auth.LINKEDIN_AUTH_URL) and f"state={cb.expected_state}" in url
        assert cb.port == 9999 and cb.started and cb.closed
        # Code exchanged against the same redirect URI the callback served.
        self.exchange.assert_called_once_with("code-1", "cid", "csec", "http://localhost:9999/callback")
        ctx.close.assert_called_once()
        assert os.path.isdir(self.browser_dir)

    def test_launch_failure_is_fallbackable(self, monkeypatch):
        self._install(monkeypatch, MagicMock(), launch_error=RuntimeError("Executable doesn't exist"))
        with pytest.raises(self.auth.PlaywrightLoginUnavailable, match="launch"):
            self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir)
        assert _FakeCallback.instances[0].closed
        self.exchange.assert_not_called()

    def test_auth_page_load_failure_is_fallbackable(self, monkeypatch):
        page = MagicMock()
        page.goto.side_effect = RuntimeError("net::ERR_CONNECTION_REFUSED")
        ctx = _fake_context([[]], pages=[page])
        self._install(monkeypatch, ctx)
        with pytest.raises(self.auth.PlaywrightLoginUnavailable, match="did not load"):
            self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir)
        ctx.close.assert_called_once()

    def test_closed_window_raises_plain_runtime_error(self, monkeypatch):
        ctx = _fake_context([[]], pages=[])  # no pages → user closed the window
        self._install(monkeypatch, ctx, callback_factory=lambda p, s: _FakeCallback(p, s, ticks=99))
        with pytest.raises(RuntimeError, match="closed") as exc:
            self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir)
        assert not isinstance(exc.value, self.auth.PlaywrightLoginUnavailable)
        self.exchange.assert_not_called()

    def test_timeout_raises_plain_runtime_error(self, monkeypatch):
        ctx = _fake_context([[]], pages=[MagicMock()])
        self._install(monkeypatch, ctx, callback_factory=lambda p, s: _FakeCallback(p, s, ticks=99))
        with pytest.raises(RuntimeError, match="Timed out") as exc:
            self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir, timeout=0)
        assert not isinstance(exc.value, self.auth.PlaywrightLoginUnavailable)

    def test_denied_authorization_raises(self, monkeypatch):
        ctx = _fake_context([[]], pages=[MagicMock()])
        self._install(monkeypatch, ctx,
                      callback_factory=lambda p, s: _FakeCallback(p, s, code=None, error="access_denied"))
        with pytest.raises(RuntimeError, match="access_denied"):
            self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir)
        self.exchange.assert_not_called()

    def test_missing_li_at_still_returns_token(self, monkeypatch):
        ctx = _fake_context([[]], pages=[MagicMock()])
        self._install(monkeypatch, ctx)
        token, li_at, jsess = self.auth._run_oauth_flow_playwright("cid", "csec", 9999, self.browser_dir)
        assert token["access_token"] == "tok"
        assert li_at is None and jsess is None


# ── _env_int ──────────────────────────────────────────────────────────────────

class TestEnvInt:
    def test_default_when_unset(self, monkeypatch):
        import auth
        monkeypatch.delenv("LINKEDIN_X_TEST", raising=False)
        assert auth._env_int("LINKEDIN_X_TEST", 42) == 42

    def test_parses_valid_value(self, monkeypatch):
        import auth
        monkeypatch.setenv("LINKEDIN_X_TEST", " 7 ")
        assert auth._env_int("LINKEDIN_X_TEST", 42) == 7

    def test_non_integer_falls_back_with_warning(self, monkeypatch, caplog):
        import auth
        monkeypatch.setenv("LINKEDIN_X_TEST", "five")
        with caplog.at_level("WARNING", logger="linkedin_mcp.auth"):
            assert auth._env_int("LINKEDIN_X_TEST", 42) == 42
        assert "not an integer" in caplog.text

    def test_below_minimum_falls_back(self, monkeypatch):
        import auth
        monkeypatch.setenv("LINKEDIN_X_TEST", "0")
        assert auth._env_int("LINKEDIN_X_TEST", 42) == 42
