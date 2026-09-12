"""Tests for server-side Voyager session recovery from the Playwright profile.

Covers:
  - _get_voyager_client() recovering a session from the persistent profile
    when none is saved (and persisting what it finds)
  - the refresh_web_session tool
  - set_web_session freeing the profile before validating

No Playwright, keychain, or network access: harvest_session_from_profile and
the persistence helpers are monkeypatched on the server module.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def srv(monkeypatch, tmp_path):
    import server

    store: dict[str, dict] = {}
    monkeypatch.setattr(server, "_active_alias", lambda: "work")
    monkeypatch.setattr(server, "_browser_dir", lambda alias: str(tmp_path / f"profile_{alias}"))
    monkeypatch.setattr(server, "load_web_session", lambda alias: store.get(alias))
    monkeypatch.setattr(
        server, "save_web_session",
        lambda li_at, jsessionid, alias: store.__setitem__(alias, {"li_at": li_at, "jsessionid": jsessionid}),
    )
    # Run "threaded" helpers inline so the mocks are observable.
    monkeypatch.setattr(server, "_run_in_thread", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(server, "VoyagerClient", MagicMock())
    server._voyager_singletons.clear()
    server._voyager_session_keys.clear()
    yield server, store, tmp_path
    server._voyager_singletons.clear()
    server._voyager_session_keys.clear()


def _make_profile(tmp_path, alias="work"):
    d = tmp_path / f"profile_{alias}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Default").mkdir(exist_ok=True)
    return d


class TestRecoverSessionFromProfile:
    def test_no_profile_returns_none(self, srv, monkeypatch):
        server, store, _ = srv
        harvest = MagicMock()
        monkeypatch.setattr(server, "harvest_session_from_profile", harvest)
        assert server._recover_session_from_profile("work") is None
        harvest.assert_not_called()
        assert store == {}

    def test_recovers_and_persists(self, srv, monkeypatch):
        server, store, tmp_path = srv
        profile = _make_profile(tmp_path)
        monkeypatch.setattr(server, "harvest_session_from_profile",
                            MagicMock(return_value=("L", "J", None)))
        session = server._recover_session_from_profile("work")
        assert session == {"li_at": "L", "jsessionid": "J"}
        assert store["work"]["li_at"] == "L"
        server.harvest_session_from_profile.assert_called_once_with(str(profile))

    def test_expired_profile_returns_none(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        monkeypatch.setattr(server, "harvest_session_from_profile",
                            MagicMock(return_value=(None, None, "no LinkedIn session")))
        assert server._recover_session_from_profile("work") is None
        assert store == {}

    def test_get_voyager_client_recovers_when_no_session(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        monkeypatch.setattr(server, "harvest_session_from_profile",
                            MagicMock(return_value=("L", "J", None)))
        vc = server._get_voyager_client("work")
        assert vc is not None
        server.VoyagerClient.assert_called_once()
        assert server.VoyagerClient.call_args[0][:2] == ("L", "J")
        # Second call is a plain session read: no second harvest.
        server._get_voyager_client("work")
        server.harvest_session_from_profile.assert_called_once()

    def test_get_voyager_client_none_without_session_or_profile(self, srv, monkeypatch):
        server, _, _ = srv
        monkeypatch.setattr(server, "harvest_session_from_profile", MagicMock())
        assert server._get_voyager_client("work") is None
        server.harvest_session_from_profile.assert_not_called()


class TestRefreshWebSessionTool:
    def test_no_profile(self, srv):
        server, _, _ = srv
        out = server.refresh_web_session()
        assert out.startswith("❌") and "authenticate" in out

    def test_refreshes_and_evicts_singleton(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        stale = MagicMock()
        server._voyager_singletons["work"] = stale
        server._voyager_session_keys["work"] = "old"
        monkeypatch.setattr(server, "harvest_session_from_profile",
                            MagicMock(return_value=("L2", "J2", None)))
        out = server.refresh_web_session()
        assert out.startswith("✅")
        assert store["work"] == {"li_at": "L2", "jsessionid": "J2"}
        stale.close.assert_called_once()  # profile lock released before harvesting
        assert "work" not in server._voyager_singletons

    def test_reports_harvest_failure(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        monkeypatch.setattr(server, "harvest_session_from_profile",
                            MagicMock(return_value=(None, None, "profile read failed: locked")))
        out = server.refresh_web_session()
        assert out.startswith("❌") and "locked" in out
        assert store == {}


class TestSetWebSessionFreesProfile:
    def test_singleton_closed_before_validation(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        stale = MagicMock()
        server._voyager_singletons["work"] = stale
        server._voyager_session_keys["work"] = "old"
        validator = MagicMock()
        validator.get_me.return_value = {"first_name": "A", "last_name": "B", "headline": "H"}
        server.VoyagerClient.return_value = validator
        out = server.set_web_session("li-new", "js-new")
        assert out.startswith("✅")
        stale.close.assert_called()
        validator.close.assert_called_once()
        assert store["work"]["li_at"] == "li-new"


class TestClearWebSessionRemovesProfile:
    def test_profile_deleted_so_recovery_cannot_reenable_voyager(self, srv, monkeypatch):
        server, store, tmp_path = srv
        profile = _make_profile(tmp_path)
        store["work"] = {"li_at": "L", "jsessionid": "J"}
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: store.pop(alias, None) is not None)
        out = server.clear_web_session()
        assert "profile removed" in out
        assert not profile.exists()
        assert server._recover_session_from_profile("work") is None

    def test_profile_only_still_reports_cleared(self, srv, monkeypatch):
        server, store, tmp_path = srv
        profile = _make_profile(tmp_path)
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: False)
        out = server.clear_web_session()
        assert out.startswith("✅") and not profile.exists()

    def test_nothing_to_clear(self, srv, monkeypatch):
        server, _, _ = srv
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: False)
        assert server.clear_web_session().startswith("ℹ️")

    def test_profile_removal_failure_keeps_stored_session(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        store["work"] = {"li_at": "L", "jsessionid": "J"}
        deleted = []
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: deleted.append(alias) or True)
        monkeypatch.setattr(server.shutil, "rmtree", MagicMock(side_effect=OSError("busy")))
        out = server.clear_web_session()
        assert "busy" in out
        assert deleted == [] and store["work"]["li_at"] == "L"

    def test_reports_failure_when_keychain_entry_survives(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        calls = []

        def strict_delete(alias, **kw):
            calls.append(kw)
            raise RuntimeError("keychain refused to delete the web session for 'work': locked")

        monkeypatch.setattr(server, "delete_web_session", strict_delete)
        out = server.clear_web_session()
        assert out.startswith("❌") and "keychain" in out and "locked" in out
        assert calls == [{"strict": True}]

    def test_invalid_alias_is_refused_before_rmtree(self, srv, monkeypatch):
        server, store, tmp_path = srv
        monkeypatch.setattr(server, "_active_alias", lambda: "../evil")
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: True)
        rm = MagicMock()
        monkeypatch.setattr(server.shutil, "rmtree", rm)
        out = server.clear_web_session()
        assert out.startswith("❌") and "invalid alias" in out
        rm.assert_not_called()

    def test_symlinked_profile_is_refused(self, srv, monkeypatch):
        server, store, tmp_path = srv
        target = tmp_path / "elsewhere"
        target.mkdir()
        (tmp_path / "profile_work").symlink_to(target)
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: True)
        out = server.clear_web_session()
        assert out.startswith("❌") and "symlink" in out
        assert target.exists()


class TestLogoutRemovesProfile:
    def test_logout_deletes_profile_token_and_session(self, srv, monkeypatch):
        server, store, tmp_path = srv
        profile = _make_profile(tmp_path)
        calls = []
        monkeypatch.setattr(server, "delete_token", lambda alias, **kw: calls.append(("token", alias)))
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: calls.append(("session", alias)))
        monkeypatch.setattr(server, "deregister_alias", lambda alias: calls.append(("dereg", alias)))
        out = server.logout("work")
        assert out.startswith("✅")
        assert not profile.exists()
        assert calls == [("token", "work"), ("session", "work"), ("dereg", "work")]

    def test_logout_keeps_alias_when_keychain_delete_fails(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        dereg = MagicMock()
        monkeypatch.setattr(server, "delete_token", lambda alias, **kw: True)

        def failing(alias, **kw):
            raise RuntimeError("keychain refused to delete the web session for 'work': locked")

        monkeypatch.setattr(server, "delete_web_session", failing)
        monkeypatch.setattr(server, "deregister_alias", dereg)
        out = server.logout("work")
        assert out.startswith("❌") and "locked" in out
        dereg.assert_not_called()
