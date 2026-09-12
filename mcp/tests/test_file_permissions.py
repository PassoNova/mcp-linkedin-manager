"""Permissions of the files and directories that hold live credentials.

The Playwright profile directory (``~/.linkedin_mcp_browser_<alias>``) holds
the LinkedIn session cookie, the token/session fallback files hold the OAuth
token and cookies, and the log file can carry aliases and API error bodies.
All of them must be owner-only from the moment they exist, regardless of the
process umask, and pre-existing loose permissions must be tightened on open.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from unittest.mock import MagicMock

import pytest

from tests.test_auth_flow import _FakeCallback, _cookie, _fake_context, _fake_sync_playwright


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture(autouse=True)
def _permissive_umask():
    """Run every test under a wide-open umask so the code must set modes explicitly."""
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


# ── ensure_private_dir / browser profile ─────────────────────────────────────

class TestPrivateDir:
    def test_creates_directory_with_0700(self, tmp_path):
        import auth
        target = tmp_path / "profile"
        auth.ensure_private_dir(str(target))
        assert target.is_dir() and _mode(target) == 0o700

    def test_tightens_existing_directory(self, tmp_path):
        import auth
        target = tmp_path / "profile"
        target.mkdir(mode=0o755)
        assert _mode(target) == 0o755
        auth.ensure_private_dir(str(target))
        assert _mode(target) == 0o700

    def test_harvest_chmods_existing_profile(self, monkeypatch, tmp_path):
        import auth
        (tmp_path / "Default").mkdir()
        os.chmod(tmp_path, 0o755)
        ctx = _fake_context([[_cookie("li_at", "L"), _cookie("JSESSIONID", "J")]])
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright", _fake_sync_playwright(ctx))
        li_at, _, err = auth.harvest_session_from_profile(str(tmp_path))
        assert (li_at, err) == ("L", None)
        assert _mode(tmp_path) == 0o700

    def test_playwright_login_creates_profile_with_0700(self, monkeypatch, tmp_path):
        import auth
        _FakeCallback.instances.clear()
        browser_dir = tmp_path / "profile"
        ctx = _fake_context([[_cookie("li_at", "L"), _cookie("JSESSIONID", "J")]], pages=[MagicMock()])
        monkeypatch.setattr(auth, "_PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(auth, "_sync_playwright", _fake_sync_playwright(ctx))
        monkeypatch.setattr(auth, "_CallbackServer", _FakeCallback)
        monkeypatch.setattr(auth, "exchange_code", MagicMock(return_value={"access_token": "t", "expires_in": 1}))
        monkeypatch.setattr(auth.time, "sleep", lambda *_: None)
        auth._run_oauth_flow_playwright("cid", "csec", 9999, str(browser_dir))
        assert browser_dir.is_dir() and _mode(browser_dir) == 0o700

    def test_headless_init_creates_profile_with_0700(self, monkeypatch, tmp_path):
        import auth
        browser_dir = tmp_path / "profile"
        ctx = _fake_context([[_cookie("li_at", "L"), _cookie("JSESSIONID", "J")]])
        monkeypatch.setattr(auth, "_sync_playwright", _fake_sync_playwright(ctx))
        auth._init_headless_profile("L", None, str(browser_dir))
        assert browser_dir.is_dir() and _mode(browser_dir) == 0o700

    def test_voyager_client_launch_tightens_existing_profile(self, monkeypatch, tmp_path):
        import client
        profile = tmp_path / "profile"
        profile.mkdir(mode=0o755)
        monkeypatch.setattr(client, "_sync_playwright", MagicMock())
        vc = client.VoyagerClient("L", "J", user_data_dir=str(profile))
        try:
            vc._ensure_context()
            assert _mode(profile) == 0o700
            launch = client._sync_playwright.return_value.__enter__.return_value.chromium.launch_persistent_context
            assert launch.call_args[0][0] == str(profile)
        finally:
            vc.close()


# ── token / session fallback files ───────────────────────────────────────────

class TestPrivateFiles:
    def test_token_file_is_0600_on_creation(self, tmp_path, monkeypatch):
        import auth
        path = tmp_path / "token_work.json"
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(path))
        auth.save_token({"access_token": "x"}, "work")
        assert _mode(path) == 0o600
        assert json.loads(path.read_text())["access_token"] == "x"

    def test_session_file_is_0600_on_creation(self, tmp_path, monkeypatch):
        import auth
        path = tmp_path / "session_work.json"
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(path))
        auth.save_web_session("li", "js", "work")
        assert _mode(path) == 0o600
        assert json.loads(path.read_text())["li_at"] == "li"

    def test_existing_loose_file_is_tightened_and_truncated(self, tmp_path, monkeypatch):
        import auth
        path = tmp_path / "token_work.json"
        path.write_text('{"access_token": "old-and-much-longer-than-the-new-payload"}')
        os.chmod(path, 0o644)
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(path))
        auth.save_token({"a": 1}, "work")
        assert _mode(path) == 0o600
        assert json.loads(path.read_text()) == {"a": 1}

    def test_write_private_json_creates_parent_dirs(self, tmp_path):
        import auth
        path = tmp_path / "nested" / "dir" / "f.json"
        auth._write_private_json(str(path), {"k": "v"})
        assert _mode(path) == 0o600 and json.loads(path.read_text()) == {"k": "v"}


# ── log file ──────────────────────────────────────────────────────────────────

class TestLogFile:
    def test_log_file_is_0600(self, tmp_path, monkeypatch):
        import log_config
        log_file = tmp_path / "logs" / "linkedin_mcp.log"
        monkeypatch.setattr(log_config, "LOG_FILE", str(log_file))
        root = logging.getLogger("linkedin_mcp")
        saved = list(root.handlers)
        root.handlers.clear()
        try:
            log_config.setup()
            assert log_file.exists() and _mode(log_file) == 0o600
        finally:
            for h in root.handlers:
                h.close()
            root.handlers[:] = saved

    def test_rollover_keeps_0600(self, tmp_path):
        import log_config
        log_file = tmp_path / "rot.log"
        h = log_config._PrivateRotatingFileHandler(str(log_file), maxBytes=64, backupCount=2, encoding="utf-8")
        try:
            assert _mode(log_file) == 0o600
            h.emit(logging.LogRecord("t", logging.INFO, __file__, 1, "x" * 100, None, None))
            h.doRollover()
            h.emit(logging.LogRecord("t", logging.INFO, __file__, 1, "after rollover", None, None))
            assert log_file.exists() and _mode(log_file) == 0o600
            assert (tmp_path / "rot.log.1").exists()
        finally:
            h.close()

    def test_existing_loose_log_is_tightened(self, tmp_path):
        import log_config
        log_file = tmp_path / "loose.log"
        log_file.write_text("old\n")
        os.chmod(log_file, 0o644)
        h = log_config._PrivateRotatingFileHandler(str(log_file), maxBytes=0, backupCount=0, encoding="utf-8")
        try:
            assert _mode(log_file) == 0o600
        finally:
            h.close()
