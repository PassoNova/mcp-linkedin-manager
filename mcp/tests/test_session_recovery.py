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
    server._cleared_pending.clear()
    server._session_generation.clear()
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


class TestLifecycleGeneration:
    def test_clear_bumps_generation_and_late_save_is_discarded(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: True)
        gen = server._lifecycle_generation("work")
        assert server.clear_web_session().startswith("✅")
        assert server._lifecycle_generation("work") == gen + 1
        assert server._save_session_if_current("L", "J", "work", gen) is False
        assert "work" not in store
        assert server._save_session_if_current("L", "J", "work", gen + 1) is True
        assert store["work"]["li_at"] == "L"

    def test_set_web_session_discards_when_cleared_during_validation(self, srv, monkeypatch):
        server, store, tmp_path = srv

        def get_me_then_clear():
            server._bump_generation("work")  # a concurrent clear_web_session lands here
            return {"first_name": "A", "last_name": "B", "headline": "h"}

        server.VoyagerClient.return_value.get_me.side_effect = get_me_then_clear
        out = server.set_web_session("li", "js")
        assert out.startswith("❌") and "cleared" in out
        assert "work" not in store

    def test_set_web_session_saves_when_not_cleared(self, srv, monkeypatch):
        server, store, tmp_path = srv
        server.VoyagerClient.return_value.get_me.return_value = {"first_name": "A", "last_name": "B", "headline": "h"}
        out = server.set_web_session("li", "js")
        assert out.startswith("✅") and store["work"]["li_at"] == "li"

    def test_authenticate_persists_nothing_when_logged_out_mid_flight(self, srv, monkeypatch):
        import asyncio
        import auth
        server, store, tmp_path = srv
        writes = []
        monkeypatch.setattr(server, "_credentials", lambda: ("cid", "csec"))
        monkeypatch.setattr(server, "save_token", lambda data, alias: writes.append(("token", alias)))
        monkeypatch.setattr(server, "register_alias", lambda alias: writes.append(("register", alias)))

        async def fake_flow(*a, **kw):
            _make_profile(tmp_path)  # the flow writes into the profile...
            server._bump_generation("work")  # ...and logout/clear lands before it returns
            return auth.OAuthResult({"access_token": "t", "expires_in": 1}, "L", "J", None, "playwright")

        monkeypatch.setattr(server, "run_oauth_flow", fake_flow)
        out = asyncio.run(server.authenticate("work"))
        assert out.startswith("❌") and "discarded" in out
        assert writes == [] and "work" not in store
        assert not (tmp_path / "profile_work").exists()

    def test_authenticate_persists_when_not_interrupted(self, srv, monkeypatch):
        import asyncio
        import auth
        server, store, tmp_path = srv
        writes = []
        monkeypatch.setattr(server, "_credentials", lambda: ("cid", "csec"))
        monkeypatch.setattr(server, "save_token", lambda data, alias: writes.append(("token", alias)))
        monkeypatch.setattr(server, "register_alias", lambda alias: writes.append(("register", alias)))

        async def fake_flow(*a, **kw):
            return auth.OAuthResult({"access_token": "t", "expires_in": 1}, "L", "J", None, "playwright")

        monkeypatch.setattr(server, "run_oauth_flow", fake_flow)
        out = asyncio.run(server.authenticate("work"))
        assert out.startswith("✅") or "Voyager" in out
        assert writes == [("token", "work"), ("register", "work")] and store["work"]["li_at"] == "L"


class TestRecoveryRefusedAfterClear:
    def test_recovery_refused_until_session_persisted_again(self, srv, monkeypatch):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        store["work"] = {"li_at": "L", "jsessionid": "J"}
        monkeypatch.setattr(server, "delete_web_session", lambda alias, **kw: store.pop(alias, None) is not None)
        monkeypatch.setattr(server, "harvest_session_from_profile", lambda bdir: ("L2", "J2", None))
        assert server.clear_web_session().startswith("✅")
        _make_profile(tmp_path)  # an in-flight flow re-created the profile after the clear
        assert server._recover_session_from_profile("work") is None
        assert server._get_voyager_client("work") is None and "work" not in store
        # A deliberate re-authentication stores a session and lifts the block.
        assert server._save_session_if_current("L3", "J3", "work", server._lifecycle_generation("work"))
        store.pop("work")
        assert server._recover_session_from_profile("work") == {"li_at": "L2", "jsessionid": "J2"}

    def test_invalidate_all_still_clears_every_singleton(self, srv):
        server, store, tmp_path = srv
        a, b = MagicMock(), MagicMock()
        server._voyager_singletons.update({"a": a, "b": b})
        server._invalidate_voyager(None)
        assert server._voyager_singletons == {}
        a.close.assert_called_once(); b.close.assert_called_once()


class TestSetWebSessionSeedsProfile:
    """After clear_web_session removed the only profile, set_web_session must still be able
    to validate the supplied cookies: it creates a fresh private profile that VoyagerClient
    seeds with them, and removes that profile again if validation fails."""

    def test_creates_profile_when_none_exists_and_keeps_it_on_success(self, srv):
        server, store, tmp_path = srv
        profile = tmp_path / "profile_work"
        assert not profile.exists()
        server.VoyagerClient.return_value.get_me.return_value = {"first_name": "A", "last_name": "B", "headline": "h"}
        out = server.set_web_session("li", "js")
        assert out.startswith("✅") and store["work"]["li_at"] == "li"
        args, kwargs = server.VoyagerClient.call_args
        assert kwargs["user_data_dir"] == str(profile)  # never None: the client refuses that
        assert profile.is_dir()
        if os.name == "posix":
            assert (profile.stat().st_mode & 0o777) == 0o700

    def test_removes_seeded_profile_when_validation_fails(self, srv):
        server, store, tmp_path = srv
        profile = tmp_path / "profile_work"
        server.VoyagerClient.return_value.get_me.side_effect = RuntimeError("401 from LinkedIn")
        out = server.set_web_session("li", "js")
        assert out.startswith("❌") and "did not accept" in out
        assert "work" not in store
        assert not profile.exists()
        server.VoyagerClient.return_value.close.assert_called()

    def test_keeps_pre_existing_profile_when_validation_fails(self, srv):
        server, store, tmp_path = srv
        _make_profile(tmp_path)
        profile = tmp_path / "profile_work"
        server.VoyagerClient.return_value.get_me.side_effect = RuntimeError("401 from LinkedIn")
        out = server.set_web_session("li", "js")
        assert out.startswith("❌")
        assert profile.is_dir() and any(profile.iterdir())  # not ours to remove

    def test_refuses_symlinked_profile_path(self, srv, tmp_path):
        server, store, _ = srv
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = tmp_path / "profile_work"
        link.symlink_to(target)
        out = server.set_web_session("li", "js")
        assert out.startswith("❌") and "symlink" in out
        assert "work" not in store
        assert link.is_symlink() and target.is_dir()  # refused, never opened, never deleted


class TestOptionalVoyagerDiscovery:
    """An unsafe session file or profile must not break the official-API path of the
    tools that only *enrich* with Voyager; the tools that require Voyager still see it."""

    def test_optional_path_reports_unavailable_on_unsafe_session_file(self, srv, monkeypatch):
        server, store, tmp_path = srv

        def unsafe(alias):
            raise OSError("refusing to use web_session_work.json: it is a symlink")

        monkeypatch.setattr(server, "load_web_session", unsafe)
        assert server._get_voyager_client(optional=True) is None
        with pytest.raises(OSError):
            server._get_voyager_client()

    def test_optional_path_reports_unavailable_on_unsafe_profile(self, srv, monkeypatch, tmp_path):
        server, store, _ = srv
        target = tmp_path / "elsewhere"
        target.mkdir()
        (tmp_path / "profile_work").symlink_to(target)  # has_browser_profile -> ensure_private_dir raises
        assert server._get_voyager_client(optional=True) is None
        with pytest.raises(OSError):
            server._get_voyager_client()

    def test_get_profile_falls_back_to_oauth_on_unsafe_session_file(self, srv, monkeypatch):
        server, store, tmp_path = srv
        monkeypatch.setattr(server, "load_web_session", lambda alias: (_ for _ in ()).throw(OSError("unsafe")))
        client = MagicMock()
        client.get_userinfo.return_value = {"sub": "u1", "name": "A B", "email": "a@b"}
        client.get_profile.return_value = {"headline": "official", "vanityName": "ab"}
        monkeypatch.setattr(server, "_get_client", lambda: client)
        out = server.get_profile()
        assert "official" in out and not out.startswith("❌")
        server.VoyagerClient.assert_not_called()


class TestEnvFileLoading:
    def _layout(self, tmp_path, monkeypatch):
        mcp_dir = tmp_path / "mcp"
        mcp_dir.mkdir()
        import server
        monkeypatch.setattr(server, "__file__", str(mcp_dir / "server.py"))
        monkeypatch.delenv("LINKEDIN_MCP_ENV_PROBE", raising=False)
        return server, mcp_dir, tmp_path / ".env"

    def test_primary_mcp_env_wins(self, tmp_path, monkeypatch):
        server, mcp_dir, legacy = self._layout(tmp_path, monkeypatch)
        (mcp_dir / ".env").write_text("LINKEDIN_MCP_ENV_PROBE=primary\n")
        legacy.write_text("LINKEDIN_MCP_ENV_PROBE=legacy\n")
        assert server._load_env_files() == str(mcp_dir / ".env")
        assert os.environ["LINKEDIN_MCP_ENV_PROBE"] == "primary"

    def test_legacy_root_env_is_read_when_primary_is_absent(self, tmp_path, monkeypatch, caplog):
        server, mcp_dir, legacy = self._layout(tmp_path, monkeypatch)
        legacy.write_text("LINKEDIN_MCP_ENV_PROBE=legacy\n")
        with caplog.at_level("WARNING", logger="linkedin_mcp.server"):
            assert server._load_env_files() == str(legacy)
        assert os.environ["LINKEDIN_MCP_ENV_PROBE"] == "legacy"
        assert any("deprecated" in r.getMessage() for r in caplog.records)

    def test_nothing_to_load(self, tmp_path, monkeypatch):
        server, mcp_dir, legacy = self._layout(tmp_path, monkeypatch)
        assert server._load_env_files() is None
        assert "LINKEDIN_MCP_ENV_PROBE" not in os.environ
