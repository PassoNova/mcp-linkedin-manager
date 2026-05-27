"""Tests for user registry (alias management) functions."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# load_user_registry
# ---------------------------------------------------------------------------

class TestLoadUserRegistry:
    def test_returns_empty_registry_when_file_missing(self, tmp_path):
        import auth
        result = auth.load_user_registry(path=str(tmp_path / "users.json"))
        assert result == {"active": None, "aliases": []}

    def test_returns_registry_from_file(self, tmp_path):
        import auth
        reg_file = tmp_path / "users.json"
        reg_file.write_text(json.dumps({"active": "work", "aliases": ["work", "personal"]}))
        result = auth.load_user_registry(path=str(reg_file))
        assert result["active"] == "work"
        assert result["aliases"] == ["work", "personal"]


# ---------------------------------------------------------------------------
# save_user_registry
# ---------------------------------------------------------------------------

class TestSaveUserRegistry:
    def test_creates_file(self, tmp_path):
        import auth
        reg_file = tmp_path / "users.json"
        auth.save_user_registry({"active": "work", "aliases": ["work"]}, path=str(reg_file))
        assert reg_file.exists()
        data = json.loads(reg_file.read_text())
        assert data["active"] == "work"

    def test_overwrites_existing(self, tmp_path):
        import auth
        reg_file = tmp_path / "users.json"
        reg_file.write_text(json.dumps({"active": "old", "aliases": ["old"]}))
        auth.save_user_registry({"active": "new", "aliases": ["new"]}, path=str(reg_file))
        data = json.loads(reg_file.read_text())
        assert data["active"] == "new"


# ---------------------------------------------------------------------------
# register_alias
# ---------------------------------------------------------------------------

class TestRegisterAlias:
    def test_adds_new_alias(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.register_alias("work", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert "work" in reg["aliases"]

    def test_first_alias_becomes_active(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.register_alias("work", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert reg["active"] == "work"

    def test_subsequent_alias_does_not_override_active(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.register_alias("work", path=reg_file)
        auth.register_alias("personal", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert reg["active"] == "work"
        assert "personal" in reg["aliases"]

    def test_duplicate_alias_not_added_twice(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.register_alias("work", path=reg_file)
        auth.register_alias("work", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert reg["aliases"].count("work") == 1


# ---------------------------------------------------------------------------
# deregister_alias
# ---------------------------------------------------------------------------

class TestDeregisterAlias:
    def test_removes_alias(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work", "personal"]}, path=reg_file)
        auth.deregister_alias("personal", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert "personal" not in reg["aliases"]
        assert reg["active"] == "work"

    def test_removes_active_alias_promotes_next(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work", "personal"]}, path=reg_file)
        auth.deregister_alias("work", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert "work" not in reg["aliases"]
        assert reg["active"] == "personal"

    def test_removes_last_alias_sets_active_none(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work"]}, path=reg_file)
        auth.deregister_alias("work", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert reg["aliases"] == []
        assert reg["active"] is None

    def test_deregistering_nonexistent_alias_is_noop(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work"]}, path=reg_file)
        auth.deregister_alias("ghost", path=reg_file)
        reg = auth.load_user_registry(path=reg_file)
        assert reg["aliases"] == ["work"]
        assert reg["active"] == "work"


# ---------------------------------------------------------------------------
# get_active_alias
# ---------------------------------------------------------------------------

class TestGetActiveAlias:
    def test_returns_active(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work"]}, path=reg_file)
        assert auth.get_active_alias(path=reg_file) == "work"

    def test_returns_none_when_no_users(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        assert auth.get_active_alias(path=reg_file) is None


# ---------------------------------------------------------------------------
# set_active_alias
# ---------------------------------------------------------------------------

class TestSetActiveAlias:
    def test_sets_active(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work", "personal"]}, path=reg_file)
        auth.set_active_alias("personal", path=reg_file)
        assert auth.get_active_alias(path=reg_file) == "personal"

    def test_raises_for_unknown_alias(self, tmp_path):
        import auth
        reg_file = str(tmp_path / "users.json")
        auth.save_user_registry({"active": "work", "aliases": ["work"]}, path=reg_file)
        with pytest.raises(ValueError, match="Unknown alias"):
            auth.set_active_alias("ghost", path=reg_file)


# ---------------------------------------------------------------------------
# validate_alias
# ---------------------------------------------------------------------------

class TestValidateAlias:
    @pytest.mark.parametrize("alias", ["work", "personal-1", "my_account", "A" * 32])
    def test_valid_aliases(self, alias):
        import auth
        auth.validate_alias(alias)  # should not raise

    @pytest.mark.parametrize("alias", ["", "has space", "too" + "x" * 30, "bad!char"])
    def test_invalid_aliases(self, alias):
        import auth
        with pytest.raises(ValueError):
            auth.validate_alias(alias)
