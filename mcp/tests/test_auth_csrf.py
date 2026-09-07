"""Tests for OAuth CSRF state validation and callback-server isolation.

The callback handler records its result on the server instance it belongs to
(_OAuthCallbackServer), never on class-level state, so concurrent flows cannot
clobber each other. The server binds to the loopback interface only.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_handler(path: str, expected_state: str | None):
    """Return a _CallbackHandler wired to mock sockets and a fake server holding state."""
    from auth import _CallbackHandler

    request = MagicMock()
    request.makefile.return_value = MagicMock()
    handler = _CallbackHandler.__new__(_CallbackHandler)
    handler.request = request
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = MagicMock(expected_state=expected_state, auth_code=None, error=None)
    handler.path = path
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    return handler


class TestCallbackHandlerCSRF:
    def test_valid_state_sets_auth_code(self):
        h = _make_handler("/callback?code=mycode&state=abc123", "abc123")
        h.do_GET()
        assert h.server.auth_code == "mycode"
        assert h.server.error is None
        h.send_response.assert_called_once_with(200)

    def test_state_mismatch_sets_error_not_code(self):
        h = _make_handler("/callback?code=mycode&state=WRONG", "abc123")
        h.do_GET()
        assert h.server.auth_code is None
        assert "mismatch" in h.server.error.lower()
        h.send_response.assert_called_once_with(400)

    def test_missing_state_treated_as_mismatch(self):
        h = _make_handler("/callback?code=mycode", "abc123")
        h.do_GET()
        assert h.server.auth_code is None
        assert h.server.error is not None

    def test_no_expected_state_skips_validation(self):
        """If expected_state is None (legacy path), any state is accepted."""
        h = _make_handler("/callback?code=mycode&state=anything", None)
        h.do_GET()
        assert h.server.auth_code == "mycode"
        assert h.server.error is None

    def test_error_param_always_sets_error(self):
        h = _make_handler("/callback?error=access_denied&error_description=User+denied", "abc123")
        h.do_GET()
        assert h.server.auth_code is None
        assert h.server.error == "User denied"
        h.send_response.assert_called_once_with(400)

    def test_no_params_is_an_error_response_without_state_change(self):
        h = _make_handler("/callback", "abc123")
        h.do_GET()
        assert h.server.auth_code is None
        assert h.server.error is None
        h.send_response.assert_called_once_with(400)


# ── Server-instance isolation and loopback binding ────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestOAuthCallbackServer:
    def test_binds_to_loopback_only(self):
        from auth import _OAuthCallbackServer
        srv = _OAuthCallbackServer(_free_port(), "s")
        try:
            assert srv.server_address[0] == "127.0.0.1"
        finally:
            srv.server_close()

    def test_two_servers_keep_independent_state(self):
        """Two concurrent flows must not share code/error/expected_state."""
        from auth import _OAuthCallbackServer
        a = _OAuthCallbackServer(_free_port(), "state-a")
        b = _OAuthCallbackServer(_free_port(), "state-b")
        try:
            ta = threading.Thread(target=a.handle_request, daemon=True)
            tb = threading.Thread(target=b.handle_request, daemon=True)
            ta.start()
            tb.start()
            pa, pb = a.server_address[1], b.server_address[1]
            urllib.request.urlopen(f"http://127.0.0.1:{pa}/callback?code=code-a&state=state-a", timeout=5).close()
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(f"http://127.0.0.1:{pb}/callback?code=code-b&state=state-a", timeout=5)
            ta.join(3)
            tb.join(3)
            assert (a.auth_code, a.error) == ("code-a", None)
            assert b.auth_code is None
            assert "mismatch" in b.error.lower()
            assert a.expected_state == "state-a" and b.expected_state == "state-b"
        finally:
            a.server_close()
            b.server_close()

    def test_wait_for_code_returns_result_from_its_own_server(self):
        from auth import _wait_for_code
        port = _free_port()

        def fire():
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=zzz&state=st", timeout=2).close()
                    return
                except Exception:
                    time.sleep(0.05)

        threading.Thread(target=fire, daemon=True).start()
        code, err = _wait_for_code(port, timeout=5, expected_state="st")
        assert (code, err) == ("zzz", None)


class TestWaitForCodeDeterminism:
    def test_stray_request_does_not_end_the_wait(self):
        """A request without code/error is answered but the wait continues to the real callback."""
        from auth import _wait_for_code
        port = _free_port()

        def fire():
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico", timeout=2).close()
                except urllib.error.HTTPError:
                    break  # 400 answered: server is up
                except Exception:
                    time.sleep(0.05)
            time.sleep(0.2)
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=real&state=st", timeout=2).close()

        threading.Thread(target=fire, daemon=True).start()
        code, err = _wait_for_code(port, timeout=5, expected_state="st")
        assert (code, err) == ("real", None)

    def test_timeout_releases_the_port(self):
        """After a timeout no thread stays blocked and the port can be bound again immediately."""
        from auth import _wait_for_code, _OAuthCallbackServer
        port = _free_port()
        before = threading.active_count()
        code, err = _wait_for_code(port, timeout=1, expected_state="st")
        assert (code, err) == (None, None)
        assert threading.active_count() <= before
        srv = _OAuthCallbackServer(port, "st")  # would raise if the socket were still held
        srv.server_close()
