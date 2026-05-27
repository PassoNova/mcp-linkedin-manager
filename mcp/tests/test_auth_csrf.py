"""Tests for OAuth CSRF state validation (Phase 2)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_handler(path: str):
    """Return a _CallbackHandler instance wired to mock sockets, with self.path set."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from auth import _CallbackHandler

    # BaseHTTPRequestHandler.__init__ needs (request, client_address, server).
    # We pass mocks and immediately override the attributes it would set.
    request = MagicMock()
    request.makefile.return_value = MagicMock()
    handler = _CallbackHandler.__new__(_CallbackHandler)
    handler.request = request
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = MagicMock()
    handler.path = path
    # Mock the response-writing methods so they're no-ops.
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    return handler


@pytest.fixture(autouse=True)
def _reset_handler():
    """Reset _CallbackHandler class vars before each test."""
    from auth import _CallbackHandler
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    _CallbackHandler.expected_state = None
    yield
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    _CallbackHandler.expected_state = None


class TestCallbackHandlerCSRF:
    def test_valid_state_sets_auth_code(self):
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = "abc123"
        handler = _make_handler("/callback?code=mycode&state=abc123")
        handler.do_GET()
        assert _CallbackHandler.auth_code == "mycode"
        assert _CallbackHandler.error is None

    def test_state_mismatch_sets_error_not_code(self):
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = "abc123"
        handler = _make_handler("/callback?code=mycode&state=WRONG")
        handler.do_GET()
        assert _CallbackHandler.auth_code is None
        assert _CallbackHandler.error is not None
        assert "mismatch" in _CallbackHandler.error.lower()

    def test_missing_state_treated_as_mismatch(self):
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = "abc123"
        handler = _make_handler("/callback?code=mycode")  # no state param
        handler.do_GET()
        assert _CallbackHandler.auth_code is None
        assert _CallbackHandler.error is not None

    def test_no_expected_state_skips_validation(self):
        """If expected_state is None (e.g. legacy path), any state is accepted."""
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = None
        handler = _make_handler("/callback?code=mycode&state=anything")
        handler.do_GET()
        assert _CallbackHandler.auth_code == "mycode"
        assert _CallbackHandler.error is None

    def test_error_param_always_sets_error(self):
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = "abc123"
        handler = _make_handler("/callback?error=access_denied&error_description=User+denied")
        handler.do_GET()
        assert _CallbackHandler.auth_code is None
        assert "User denied" in (_CallbackHandler.error or "")

    def test_send_response_400_on_mismatch(self):
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = "abc123"
        handler = _make_handler("/callback?code=mycode&state=WRONG")
        handler.do_GET()
        handler.send_response.assert_called_once_with(400)

    def test_send_response_200_on_valid(self):
        from auth import _CallbackHandler
        _CallbackHandler.expected_state = "abc123"
        handler = _make_handler("/callback?code=mycode&state=abc123")
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)


class TestWaitForCodePassesState:
    def test_expected_state_assigned_to_handler(self, monkeypatch):
        """_wait_for_code should propagate expected_state to _CallbackHandler."""
        from auth import _CallbackHandler
        import auth

        # Patch HTTPServer and Thread so nothing actually listens.
        mock_server = MagicMock()
        mock_thread = MagicMock()
        monkeypatch.setattr(auth, "HTTPServer", lambda *a, **kw: mock_server)
        monkeypatch.setattr(auth, "Thread", lambda **kw: mock_thread)

        auth._wait_for_code(port=9999, timeout=1, expected_state="state-xyz")

        assert _CallbackHandler.expected_state == "state-xyz"
