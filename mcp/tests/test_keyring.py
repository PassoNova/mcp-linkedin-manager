"""Tests for keyring-backed credential persistence (web session, OAuth token, app credentials)."""
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
    def test_saves_to_keyring_with_alias_key(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)

        auth.save_web_session("li_at_val", "jsess_val", "work")

        mock_kr.set_password.assert_called_once()
        args = mock_kr.set_password.call_args[0]
        assert args[0] == "linkedin-mcp"
        assert args[1] == "session:work"
        saved = json.loads(args[2])
        assert saved["li_at"] == "li_at_val"

    def test_falls_back_to_file_on_keyring_error(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = Exception("keyring unavailable")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        auth.save_web_session("li_at_val", "jsess_val", "work")

        path = tmp_path / "session_work.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["li_at"] == "li_at_val"

    def test_writes_file_when_keyring_unavailable(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        auth.save_web_session("token123", "jsess123", "personal")

        path = tmp_path / "session_personal.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["li_at"] == "token123"


# ---------------------------------------------------------------------------
# load_web_session
# ---------------------------------------------------------------------------

class TestLoadWebSession:
    def test_loads_from_keyring_with_alias_key(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        payload = json.dumps({"li_at": "from_kr", "jsessionid": "j", "_saved_at": 0})
        mock_kr.get_password.return_value = payload
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_web_session("work")
        assert result["li_at"] == "from_kr"
        mock_kr.get_password.assert_called_once_with("linkedin-mcp", "session:work")

    def test_falls_back_to_file_when_keyring_empty(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        (tmp_path / "session_work.json").write_text(
            json.dumps({"li_at": "from_file", "jsessionid": "j", "_saved_at": 0})
        )
        result = auth.load_web_session("work")
        assert result["li_at"] == "from_file"

    def test_returns_none_when_both_missing(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"no_{alias}.json"))

        result = auth.load_web_session("work")
        assert result is None

    def test_loads_from_file_when_keyring_unavailable(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        (tmp_path / "session_personal.json").write_text(
            json.dumps({"li_at": "file_token", "jsessionid": "j", "_saved_at": 0})
        )
        result = auth.load_web_session("personal")
        assert result["li_at"] == "file_token"

    def test_falls_back_to_file_on_keyring_exception(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("keyring error")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        (tmp_path / "session_work.json").write_text(
            json.dumps({"li_at": "fallback", "jsessionid": "j", "_saved_at": 0})
        )
        result = auth.load_web_session("work")
        assert result["li_at"] == "fallback"


# ---------------------------------------------------------------------------
# delete_web_session
# ---------------------------------------------------------------------------

class TestDeleteWebSession:
    def test_clears_keyring_with_alias_key_and_file(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        (tmp_path / "session_work.json").write_text("{}")
        result = auth.delete_web_session("work")

        mock_kr.delete_password.assert_called_once_with("linkedin-mcp", "session:work")
        assert not (tmp_path / "session_work.json").exists()
        assert result is True

    def test_returns_true_when_only_file_exists(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        (tmp_path / "session_work.json").write_text("{}")
        result = auth.delete_web_session("work")
        assert result is True
        assert not (tmp_path / "session_work.json").exists()

    def test_returns_false_when_nothing_exists(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("not found")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"no_{alias}.json"))

        result = auth.delete_web_session("work")
        assert result is False

    def test_keyring_exception_still_removes_file(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("keyring error")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_session_path", lambda alias: str(tmp_path / f"session_{alias}.json"))

        (tmp_path / "session_work.json").write_text("{}")
        result = auth.delete_web_session("work")
        assert not (tmp_path / "session_work.json").exists()
        assert result is True


# ---------------------------------------------------------------------------
# save_token
# ---------------------------------------------------------------------------

class TestSaveToken:
    def test_saves_to_keyring_with_alias_key(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)

        auth.save_token({"access_token": "tok", "expires_in": 5183944}, "work")

        mock_kr.set_password.assert_called_once()
        args = mock_kr.set_password.call_args[0]
        assert args[0] == "linkedin-mcp"
        assert args[1] == "oauth_token:work"
        assert json.loads(args[2])["access_token"] == "tok"

    def test_falls_back_to_file_on_keyring_error(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = Exception("backend locked")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"token_{alias}.json"))

        auth.save_token({"access_token": "tok"}, "work")

        path = tmp_path / "token_work.json"
        assert path.exists()
        assert json.loads(path.read_text())["access_token"] == "tok"

    def test_writes_file_when_keyring_unavailable(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"token_{alias}.json"))

        auth.save_token({"access_token": "tok"}, "personal")

        path = tmp_path / "token_personal.json"
        assert path.exists()
        assert json.loads(path.read_text())["access_token"] == "tok"


# ---------------------------------------------------------------------------
# load_token
# ---------------------------------------------------------------------------

class TestLoadToken:
    def test_loads_from_keyring_with_alias_key(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = json.dumps({"access_token": "from_kr"})
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_token("work")
        assert result["access_token"] == "from_kr"
        mock_kr.get_password.assert_called_once_with("linkedin-mcp", "oauth_token:work")

    def test_falls_back_to_file_when_keyring_empty(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"token_{alias}.json"))

        (tmp_path / "token_work.json").write_text(json.dumps({"access_token": "from_file"}))
        result = auth.load_token("work")
        assert result["access_token"] == "from_file"

    def test_falls_back_to_file_on_keyring_exception(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("keyring error")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"token_{alias}.json"))

        (tmp_path / "token_work.json").write_text(json.dumps({"access_token": "fallback"}))
        result = auth.load_token("work")
        assert result["access_token"] == "fallback"

    def test_returns_none_when_both_missing(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"no_{alias}.json"))

        result = auth.load_token("work")
        assert result is None


# ---------------------------------------------------------------------------
# delete_token
# ---------------------------------------------------------------------------

class TestDeleteToken:
    def test_deletes_keyring_with_alias_key_and_file(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"token_{alias}.json"))

        (tmp_path / "token_work.json").write_text("{}")
        result = auth.delete_token("work")

        mock_kr.delete_password.assert_called_once_with("linkedin-mcp", "oauth_token:work")
        assert not (tmp_path / "token_work.json").exists()
        assert result is True

    def test_returns_true_when_only_file_exists(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"token_{alias}.json"))

        (tmp_path / "token_work.json").write_text("{}")
        result = auth.delete_token("work")
        assert result is True
        assert not (tmp_path / "token_work.json").exists()

    def test_returns_false_when_nothing_exists(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("not found")
        monkeypatch.setattr(auth, "keyring", mock_kr)
        monkeypatch.setattr(auth, "_token_path", lambda alias: str(tmp_path / f"no_{alias}.json"))

        result = auth.delete_token("work")
        assert result is False


# ---------------------------------------------------------------------------
# save_credentials
# ---------------------------------------------------------------------------

class TestSaveCredentials:
    def test_saves_to_keyring_when_available(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.save_credentials("my_id", "my_secret")

        assert result is True
        mock_kr.set_password.assert_called_once()
        args = mock_kr.set_password.call_args[0]
        assert args[0] == "linkedin-mcp"
        assert args[1] == "credentials"
        saved = json.loads(args[2])
        assert saved["client_id"] == "my_id"
        assert saved["client_secret"] == "my_secret"

    def test_returns_false_when_keyring_unavailable(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)

        result = auth.save_credentials("my_id", "my_secret")
        assert result is False

    def test_returns_false_on_keyring_error(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = Exception("backend error")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.save_credentials("my_id", "my_secret")
        assert result is False


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------

class TestLoadCredentials:
    def test_loads_from_keyring(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        payload = json.dumps({"client_id": "cid", "client_secret": "csec"})
        mock_kr.get_password.return_value = payload
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_credentials()
        assert result["client_id"] == "cid"
        assert result["client_secret"] == "csec"

    def test_returns_none_when_not_stored(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_credentials()
        assert result is None

    def test_returns_none_when_keyring_unavailable(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)

        result = auth.load_credentials()
        assert result is None

    def test_returns_none_on_keyring_exception(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("keyring error")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.load_credentials()
        assert result is None


# ---------------------------------------------------------------------------
# delete_credentials
# ---------------------------------------------------------------------------

class TestDeleteCredentials:
    def test_deletes_from_keyring(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.delete_credentials()

        mock_kr.delete_password.assert_called_once_with("linkedin-mcp", "credentials")
        assert result is True

    def test_returns_false_on_keyring_error(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", True)
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("not found")
        monkeypatch.setattr(auth, "keyring", mock_kr)

        result = auth.delete_credentials()
        assert result is False

    def test_returns_false_when_keyring_unavailable(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "_HAS_KEYRING", False)

        result = auth.delete_credentials()
        assert result is False
