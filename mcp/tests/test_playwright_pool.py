"""Tests for persistent Playwright context on VoyagerClient (Phase 7)."""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_playwright(mocker):
    """Patch client._sync_playwright and return mock page/context/pw objects."""
    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.evaluate.return_value = {"status": 200, "body": '{"ok": 1}'}

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.cookies.return_value = [{"name": "JSESSIONID", "value": '"live_jsess"'}]

    mock_chromium = MagicMock()
    mock_chromium.launch_persistent_context.return_value = mock_context

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    mock_sp_instance = MagicMock()
    mock_sp_instance.__enter__ = MagicMock(return_value=mock_pw)
    mock_sp_instance.__exit__ = MagicMock(return_value=False)

    mocker.patch("client._sync_playwright", return_value=mock_sp_instance)

    return {
        "page": mock_page,
        "context": mock_context,
        "chromium": mock_chromium,
        "pw": mock_pw,
        "sp_instance": mock_sp_instance,
    }


def _make_vc(user_data_dir="/tmp/fake-profile"):
    import client as c
    return c.VoyagerClient(li_at="x", jsessionid="y", user_data_dir=user_data_dir)


# ---------------------------------------------------------------------------
# _ensure_context
# ---------------------------------------------------------------------------

class TestEnsureContext:
    def test_launch_called_once_for_two_requests(self, mock_playwright):
        vc = _make_vc()
        # Simulate two _browser_request calls
        vc._browser_request("/me", "GET", None)
        vc._browser_request("/identity/profiles/abc", "GET", None)

        mock_playwright["chromium"].launch_persistent_context.assert_called_once()

    def test_page_route_set_up_once(self, mock_playwright):
        vc = _make_vc()
        vc._ensure_context()
        vc._ensure_context()  # second call must not re-create the page
        mock_playwright["context"].new_page.assert_called_once()
        mock_playwright["page"].route.assert_called_once()

    def test_raises_without_user_data_dir(self):
        import client as c
        vc = c.VoyagerClient(li_at="x", jsessionid="y", user_data_dir=None)
        with pytest.raises(RuntimeError, match="authenticate"):
            vc._ensure_context()

    def test_atexit_registered(self, mock_playwright, mocker):
        mock_atexit = mocker.patch("client.atexit.register")
        vc = _make_vc()
        vc._ensure_context()
        mock_atexit.assert_called_once_with(vc.close)


# ---------------------------------------------------------------------------
# _browser_request
# ---------------------------------------------------------------------------

class TestBrowserRequest:
    def test_goto_feed_called_when_not_on_linkedin(self, mock_playwright):
        mock_playwright["page"].url = "about:blank"
        vc = _make_vc()
        vc._browser_request("/me", "GET", None)
        mock_playwright["page"].goto.assert_called_once()
        assert "feed" in mock_playwright["page"].goto.call_args[0][0]

    def test_goto_feed_skipped_when_already_on_linkedin(self, mock_playwright):
        mock_playwright["page"].url = "https://www.linkedin.com/feed/"
        vc = _make_vc()
        vc._browser_request("/me", "GET", None)
        mock_playwright["page"].goto.assert_not_called()

    def test_voyager_fetch_script_evaluated(self, mock_playwright):
        vc = _make_vc()
        vc._browser_request("/me", "GET", None)
        mock_playwright["page"].evaluate.assert_called_once()
        args = mock_playwright["page"].evaluate.call_args[0]
        assert isinstance(args[0], str)  # the JS script
        assert args[1]["method"] == "GET"

    def test_raises_on_voyager_error_status(self, mock_playwright):
        mock_playwright["page"].evaluate.return_value = {"status": 401, "body": "Unauthorized"}
        vc = _make_vc()
        with pytest.raises(RuntimeError, match="Voyager API error 401"):
            vc._browser_request("/me", "GET", None)

    def test_live_jsessionid_from_cookies(self, mock_playwright):
        mock_playwright["context"].cookies.return_value = [
            {"name": "JSESSIONID", "value": '"live_from_browser"'}
        ]
        vc = _make_vc()
        vc._browser_request("/me", "GET", None)
        eval_kwargs = mock_playwright["page"].evaluate.call_args[0][1]
        assert eval_kwargs["jsessionid"] == "live_from_browser"


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_tears_down_all(self, mock_playwright):
        vc = _make_vc()
        vc._ensure_context()
        vc.close()

        mock_playwright["page"].close.assert_called_once()
        mock_playwright["context"].close.assert_called_once()
        # self._playwright is mock_pw (returned by __enter__); close calls its __exit__
        mock_playwright["pw"].__exit__.assert_called_once_with(None, None, None)
        assert vc._playwright is None
        assert vc._context is None
        assert vc._page is None

    def test_close_is_idempotent(self, mock_playwright):
        vc = _make_vc()
        vc._ensure_context()
        vc.close()
        vc.close()  # second close must not raise

    def test_close_before_ensure_context_is_noop(self):
        vc = _make_vc()
        vc.close()  # must not raise even though context was never started


# ---------------------------------------------------------------------------
# Scrape methods use persistent page
# ---------------------------------------------------------------------------

class TestScrapeMethodsUsePersistentPage:
    def test_notifications_uses_shared_page(self, mock_playwright):
        mock_playwright["page"].evaluate.return_value = ["notif1"]
        vc = _make_vc()
        vc.get_notifications(count=1)
        mock_playwright["chromium"].launch_persistent_context.assert_called_once()

    def test_posts_uses_shared_page(self, mock_playwright):
        mock_playwright["page"].evaluate.return_value = [{"urn": "u", "text": "t", "time": ""}]
        vc = _make_vc()
        vc._browser_scrape_posts("alice", 5)
        mock_playwright["chromium"].launch_persistent_context.assert_called_once()

    def test_profile_sections_uses_shared_page(self, mock_playwright):
        mock_playwright["page"].evaluate.return_value = "some text"
        vc = _make_vc()
        vc._browser_scrape_profile("alice")
        mock_playwright["chromium"].launch_persistent_context.assert_called_once()
