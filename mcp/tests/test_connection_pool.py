"""Tests for HTTP connection pooling on LinkedInClient (Phase 4)."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx


class TestConnectionPooling:
    def test_http_client_is_persistent_instance(self):
        import client as c

        with respx.mock:
            lc = c.LinkedInClient("fake-token")
            assert isinstance(lc._http, httpx.Client)

    def test_same_client_instance_reused_across_calls(self):
        import client as c

        url_a = "https://api.linkedin.com/v2/userinfo"
        url_b = "https://api.linkedin.com/v2/me"

        with respx.mock:
            respx.get(url_a).mock(return_value=httpx.Response(200, json={"sub": "x"}))
            respx.get(url_b).mock(return_value=httpx.Response(200, json={"id": "y"}))

            lc = c.LinkedInClient("fake-token")
            http_id_before = id(lc._http)

            lc._get(url_a)
            lc._get(url_b)

            assert id(lc._http) == http_id_before

    def test_client_closed_on_del(self, mocker):
        import client as c

        with respx.mock:
            lc = c.LinkedInClient("fake-token")
            spy = mocker.patch.object(lc._http, "close")
            del lc
            spy.assert_called_once()

    def test_persistent_headers_include_auth(self):
        """The pooled client must carry Authorization header for all requests."""
        import client as c

        with respx.mock:
            lc = c.LinkedInClient("my-token-xyz")
            auth_header = lc._http.headers.get("authorization", "")
            assert "my-token-xyz" in auth_header

    def test_two_instances_have_independent_clients(self):
        import client as c

        with respx.mock:
            lc1 = c.LinkedInClient("token-1")
            lc2 = c.LinkedInClient("token-2")
            assert lc1._http is not lc2._http
