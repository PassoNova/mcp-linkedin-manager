"""Tests for _http_execute retry-with-backoff logic (Phase 3)."""
from __future__ import annotations

from unittest.mock import call, patch

import httpx
import pytest
import respx


def _client() -> httpx.Client:
    return httpx.Client()


class TestHttpExecuteRetry:
    def test_success_on_first_attempt(self):
        import client as c

        with respx.mock:
            respx.get("https://api.linkedin.com/v2/userinfo").mock(
                return_value=httpx.Response(200, json={"sub": "abc"})
            )
            with patch("client.time.sleep") as mock_sleep:
                with httpx.Client() as hc:
                    r = c._http_execute(hc, "get", "https://api.linkedin.com/v2/userinfo")
            assert r.status_code == 200
            mock_sleep.assert_not_called()
            assert len(respx.calls) == 1

    def test_retries_429_twice_then_succeeds(self):
        import client as c

        url = "https://api.linkedin.com/v2/userinfo"
        with respx.mock:
            respx.get(url).mock(
                side_effect=[
                    httpx.Response(429),
                    httpx.Response(429),
                    httpx.Response(200, json={"sub": "abc"}),
                ]
            )
            with patch("client.time.sleep") as mock_sleep:
                with httpx.Client() as hc:
                    r = c._http_execute(hc, "get", url)
            assert r.status_code == 200
            assert len(respx.calls) == 3
            assert mock_sleep.call_args_list == [call(1.0), call(2.0)]

    def test_raises_after_three_429s(self):
        import client as c

        url = "https://api.linkedin.com/v2/userinfo"
        with respx.mock:
            respx.get(url).mock(return_value=httpx.Response(429))
            with patch("client.time.sleep"):
                with httpx.Client() as hc:
                    with pytest.raises(httpx.HTTPStatusError) as exc_info:
                        c._http_execute(hc, "get", url)
            assert exc_info.value.response.status_code == 429
            assert len(respx.calls) == 3

    def test_no_retry_on_404(self):
        import client as c

        url = "https://api.linkedin.com/v2/userinfo"
        with patch("client.time.sleep") as mock_sleep:
            with respx.mock:
                respx.get(url).mock(return_value=httpx.Response(404))
                with httpx.Client() as hc:
                    with pytest.raises(httpx.HTTPStatusError):
                        c._http_execute(hc, "get", url)
                assert len(respx.calls) == 1
        mock_sleep.assert_not_called()

    def test_no_retry_on_403(self):
        import client as c

        url = "https://api.linkedin.com/v2/userinfo"
        with patch("client.time.sleep") as mock_sleep:
            with respx.mock:
                respx.get(url).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
                with httpx.Client() as hc:
                    with pytest.raises(httpx.HTTPStatusError):
                        c._http_execute(hc, "get", url)
                assert len(respx.calls) == 1
        mock_sleep.assert_not_called()

    def test_retries_503(self):
        import client as c

        url = "https://api.linkedin.com/v2/userinfo"
        with patch("client.time.sleep") as mock_sleep:
            with respx.mock:
                respx.get(url).mock(
                    side_effect=[
                        httpx.Response(503),
                        httpx.Response(200, json={"ok": 1}),
                    ]
                )
                with httpx.Client() as hc:
                    r = c._http_execute(hc, "get", url)
                assert r.status_code == 200
                assert len(respx.calls) == 2
        mock_sleep.assert_called_once_with(1.0)
