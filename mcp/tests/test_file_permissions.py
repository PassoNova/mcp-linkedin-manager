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

    def test_has_browser_profile_tightens_before_listing(self, tmp_path):
        import auth
        profile = tmp_path / "profile"
        profile.mkdir(mode=0o755)
        (profile / "Default").mkdir()
        assert auth.has_browser_profile(str(profile)) is True
        assert _mode(profile) == 0o700
        assert auth.has_browser_profile(str(tmp_path / "missing")) is False
        assert not (tmp_path / "missing").exists()

    def test_symlinked_profile_dir_is_refused(self, tmp_path):
        import auth
        target = tmp_path / "elsewhere"
        target.mkdir(mode=0o755)
        link = tmp_path / "profile"
        link.symlink_to(target)
        with pytest.raises(OSError, match="symlink"):
            auth.ensure_private_dir(str(link))
        assert _mode(target) == 0o755  # untouched

    def test_closed_voyager_client_never_reopens_profile(self, monkeypatch, tmp_path):
        import client
        profile = tmp_path / "profile"
        profile.mkdir()
        monkeypatch.setattr(client, "_sync_playwright", MagicMock())
        vc = client.VoyagerClient("L", "J", user_data_dir=str(profile))
        vc.close()
        import shutil
        shutil.rmtree(profile)
        with pytest.raises(RuntimeError, match="closed"):
            vc._ensure_context()
        assert not profile.exists()

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

    def test_loose_files_are_tightened_when_read(self, tmp_path, monkeypatch):
        import auth
        tok = tmp_path / "token_work.json"
        ses = tmp_path / "session_work.json"
        tok.write_text('{"access_token": "x"}')
        ses.write_text('{"li_at": "L", "jsessionid": "J"}')
        for f in (tok, ses):
            os.chmod(f, 0o644)
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tok))
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(ses))
        assert auth.load_token("work")["access_token"] == "x"
        assert auth.load_web_session("work")["li_at"] == "L"
        assert _mode(tok) == 0o600 and _mode(ses) == 0o600

    def test_loose_file_is_tightened_even_on_keychain_hit(self, tmp_path, monkeypatch):
        import auth
        tok = tmp_path / "token_work.json"
        tok.write_text('{"access_token": "stale"}')
        os.chmod(tok, 0o644)
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        kr = MagicMock()
        kr.get_password.return_value = '{"access_token": "from-keychain"}'
        monkeypatch.setattr(auth, "keyring", kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tok))
        assert auth.load_token("work")["access_token"] == "from-keychain"
        assert _mode(tok) == 0o600

    def test_load_fails_closed_when_file_cannot_be_tightened(self, tmp_path, monkeypatch):
        import auth
        tok = tmp_path / "token_work.json"
        tok.write_text('{"access_token": "x"}')
        os.chmod(tok, 0o644)
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tok))
        monkeypatch.setattr(os, "chmod", MagicMock(side_effect=PermissionError("ro")))
        with pytest.raises(OSError, match="owner-only"):
            auth.load_token("work")

    def test_symlinked_fallback_file_is_refused(self, tmp_path, monkeypatch):
        import auth
        real = tmp_path / "elsewhere.json"
        real.write_text('{"li_at": "L"}')
        link = tmp_path / "session_work.json"
        link.symlink_to(real)
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(link))
        with pytest.raises(OSError, match="symlink"):
            auth.load_web_session("work")

    def test_write_refuses_symlinked_target(self, tmp_path):
        import auth
        real = tmp_path / "elsewhere.json"
        real.write_text("keep")
        link = tmp_path / "token.json"
        link.symlink_to(real)
        with pytest.raises(OSError, match="symlink"):
            auth._write_private_json(str(link), {"a": 1})
        assert real.read_text() == "keep"

    def test_save_tightens_stale_file_even_when_keychain_wins(self, tmp_path, monkeypatch):
        import auth
        tok = tmp_path / "token_work.json"
        tok.write_text('{"access_token": "stale"}')
        os.chmod(tok, 0o644)
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        monkeypatch.setattr(auth, "keyring", MagicMock())
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tok))
        auth.save_token({"access_token": "new"}, "work")
        assert _mode(tok) == 0o600
        assert json.loads(tok.read_text())["access_token"] == "stale"  # keychain won; file untouched

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

    def test_existing_loose_backups_are_tightened_at_setup_and_rollover(self, tmp_path):
        import log_config
        log_file = tmp_path / "up.log"
        for i in (1, 2):
            b = tmp_path / f"up.log.{i}"
            b.write_text("old\n")
            os.chmod(b, 0o644)
        h = log_config._PrivateRotatingFileHandler(str(log_file), maxBytes=16, backupCount=3, encoding="utf-8")
        try:
            assert _mode(tmp_path / "up.log.1") == 0o600 and _mode(tmp_path / "up.log.2") == 0o600
            h.emit(logging.LogRecord("t", logging.INFO, __file__, 1, "x" * 40, None, None))
            h.doRollover()
            for name in ("up.log", "up.log.1", "up.log.2", "up.log.3"):
                assert _mode(tmp_path / name) == 0o600, name
        finally:
            h.close()

    def test_setup_fails_closed_when_log_cannot_be_tightened(self, tmp_path, monkeypatch):
        import log_config
        log_file = tmp_path / "ro.log"
        monkeypatch.setattr(log_config, "LOG_FILE", str(log_file))
        monkeypatch.setattr(os, "fchmod", MagicMock(side_effect=PermissionError("nope")))
        root = logging.getLogger("linkedin_mcp")
        saved = list(root.handlers)
        root.handlers.clear()
        try:
            with pytest.raises(OSError, match="owner-only"):
                log_config.setup()
            assert root.handlers == []
        finally:
            root.handlers[:] = saved

    def test_symlinked_log_file_is_refused(self, tmp_path):
        import log_config
        real = tmp_path / "real.log"
        real.write_text("keep\n")
        link = tmp_path / "linkedin_mcp.log"
        link.symlink_to(real)
        with pytest.raises(OSError, match="symlink"):
            log_config._PrivateRotatingFileHandler(str(link), maxBytes=0, backupCount=0, encoding="utf-8")
        assert real.read_text() == "keep\n"

    def test_symlinked_log_backup_is_refused(self, tmp_path):
        import log_config
        real = tmp_path / "victim"
        real.write_text("x")
        os.chmod(real, 0o644)
        (tmp_path / "s.log.1").symlink_to(real)
        with pytest.raises(OSError, match="symlink"):
            log_config._PrivateRotatingFileHandler(str(tmp_path / "s.log"), maxBytes=0, backupCount=2, encoding="utf-8")
        assert _mode(real) == 0o644
