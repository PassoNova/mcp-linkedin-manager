"""Tests for keyring-backed web session persistence (Phase 6)."""
from __future__ import annotations

import json
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_auth_with_keyring(enabled: bool):
    """Reload auth module with keyring availability forced on or off."""
    import importlib
    import auth
    auth._HAS_KEYRING = enabled
    if enabled and not hasattr(auth, "_kr_mock"):
        pass  # caller sets up the mock
    return auth


# ---------------------------------------------------------------------------
# save_web_session
# ---------------------------------------------------------------------------

class TestSaveWebSession:
    def test_saves_to_keyring_when_available(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)

        session_file = str(tmp_path / "session.json")
        auth.save_web_session("li_at_val", "jsess_val", path=session_file)

        mock_kr.set_password.assert_called_once()
        args = mock_kr.set_password.call_args[0]
        assert args[0] == "linkedin-mcp"
        assert args[1] == "session"
        saved = json.loads(args[2])
        assert saved["li_at"] == "li_at_val"
        # File must NOT be written when keyring succeeds
        assert not os.path.exists(session_file)

    def test_falls_back_to_file_on_keyring_error(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = Exception("keyring unavailable")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        session_file = str(tmp_path / "session.json")
        auth.save_web_session("li_at_val", "jsess_val", path=session_file)

        assert os.path.exists(session_file)
        data = json.loads(open(session_file).read())
        assert data["li_at"] == "li_at_val"

    def test_writes_file_when_keyring_unavailable(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)

        session_file = str(tmp_path / "session.json")
        auth.save_web_session("token123", "jsess123", path=session_file)

        assert os.path.exists(session_file)
        data = json.loads(open(session_file).read())
        assert data["li_at"] == "token123"


# ---------------------------------------------------------------------------
# load_web_session
# ---------------------------------------------------------------------------

class TestLoadWebSession:
    def test_loads_from_keyring_first(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        payload = json.dumps({"li_at": "from_kr", "jsessionid": "j", "_saved_at": 0})
        mock_kr.get_password.return_value = payload
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_web_session(path=str(tmp_path / "session.json"))
        assert result["li_at"] == "from_kr"

    def test_migration_file_exists_keyring_empty(self, tmp_path, monkeypatch):
        """If keyring returns None, falls back to file."""
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)

        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps({"li_at": "from_file", "jsessionid": "j", "_saved_at": 0}))

        result = auth.load_web_session(path=str(session_file))
        assert result["li_at"] == "from_file"

    def test_returns_none_when_both_missing(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_web_session(path=str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_loads_from_file_when_keyring_unavailable(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)

        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps({"li_at": "file_token", "jsessionid": "j", "_saved_at": 0}))

        result = auth.load_web_session(path=str(session_file))
        assert result["li_at"] == "file_token"

    def test_falls_back_to_file_on_keyring_exception(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("keyring error")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps({"li_at": "fallback", "jsessionid": "j", "_saved_at": 0}))

        result = auth.load_web_session(path=str(session_file))
        assert result["li_at"] == "fallback"


# ---------------------------------------------------------------------------
# delete_web_session
# ---------------------------------------------------------------------------

class TestDeleteWebSession:
    def test_clears_both_keyring_and_file(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)

        session_file = tmp_path / "session.json"
        session_file.write_text("{}")

        result = auth.delete_web_session(path=str(session_file))

        mock_kr.delete_password.assert_called_once_with("linkedin-mcp", "session")
        assert not session_file.exists()
        assert result is True

    def test_returns_true_when_only_file_exists(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)

        session_file = tmp_path / "session.json"
        session_file.write_text("{}")

        result = auth.delete_web_session(path=str(session_file))
        assert result is True
        assert not session_file.exists()

    def test_returns_false_when_nothing_exists(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("not found")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.delete_web_session(path=str(tmp_path / "nonexistent.json"))
        assert result is False

    def test_keyring_exception_still_removes_file(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("keyring error")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        session_file = tmp_path / "session.json"
        session_file.write_text("{}")

        result = auth.delete_web_session(path=str(session_file))
        assert not session_file.exists()
        assert result is True
