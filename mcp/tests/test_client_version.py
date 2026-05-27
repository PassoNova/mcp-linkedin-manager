"""Tests for the LINKEDIN_API_VERSION env-var override (Phase 1)."""
from __future__ import annotations

import importlib
import sys


def _reload_client(monkeypatch, version: str | None = None):
    """Reload client module with a patched env var, return the module."""
    if version is None:
        monkeypatch.delenv("LINKEDIN_API_VERSION", raising=False)
    else:
        monkeypatch.setenv("LINKEDIN_API_VERSION", version)
    # Force a clean reload so RESTLI_HEADER is rebuilt from the current env.
    import client as client_mod
    importlib.reload(client_mod)
    return client_mod


class TestRestliHeader:
    def test_default_version(self, monkeypatch):
        mod = _reload_client(monkeypatch)
        assert mod.RESTLI_HEADER["LinkedIn-Version"] == "202506"

    def test_custom_version_via_env(self, monkeypatch):
        mod = _reload_client(monkeypatch, "202501")
        assert mod.RESTLI_HEADER["LinkedIn-Version"] == "202501"

    def test_restli_protocol_version_unchanged(self, monkeypatch):
        mod = _reload_client(monkeypatch, "202601")
        assert mod.RESTLI_HEADER["X-Restli-Protocol-Version"] == "2.0.0"

    def test_custom_version_propagates_to_client_headers(self, monkeypatch):
        mod = _reload_client(monkeypatch, "202509")
        client = mod.LinkedInClient("fake-token")
        headers = client._headers()
        assert headers["LinkedIn-Version"] == "202509"

    def teardown_method(self, _method):
        # Restore the real module state after each test so other test files
        # that import client get a clean version.
        import client as client_mod
        importlib.reload(client_mod)
