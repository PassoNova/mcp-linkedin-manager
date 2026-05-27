"""Tests for SimpleCache and VoyagerClient caching behavior (Phase 5)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time


# ---------------------------------------------------------------------------
# SimpleCache unit tests
# ---------------------------------------------------------------------------

class TestSimpleCache:
    def _make(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from cache import SimpleCache
        return SimpleCache()

    def test_miss_on_empty(self):
        c = self._make()
        hit, val = c.get("x", 60.0)
        assert not hit
        assert val is None

    def test_hit_within_ttl(self):
        c = self._make()
        with freeze_time("2024-01-01 12:00:00"):
            c.set("x", {"a": 1})
        with freeze_time("2024-01-01 12:00:59"):
            hit, val = c.get("x", 60.0)
        assert hit
        assert val == {"a": 1}

    def test_miss_after_expiry(self):
        c = self._make()
        with freeze_time("2024-01-01 12:00:00"):
            c.set("x", {"a": 1})
        with freeze_time("2024-01-01 12:01:01"):
            hit, val = c.get("x", 60.0)
        assert not hit
        assert val is None

    def test_invalidate_removes_entry(self):
        c = self._make()
        with freeze_time("2024-01-01 12:00:00"):
            c.set("x", 42)
            c.invalidate("x")
            hit, _ = c.get("x", 60.0)
        assert not hit

    def test_invalidate_missing_key_is_noop(self):
        c = self._make()
        c.invalidate("nonexistent")  # must not raise

    def test_clear_removes_all_entries(self):
        c = self._make()
        with freeze_time("2024-01-01 12:00:00"):
            c.set("a", 1)
            c.set("b", 2)
        c.clear()
        with freeze_time("2024-01-01 12:00:00"):
            assert not c.get("a", 60.0)[0]
            assert not c.get("b", 60.0)[0]

    def test_overwrite_resets_timestamp(self):
        c = self._make()
        with freeze_time("2024-01-01 12:00:00"):
            c.set("x", "old")
        with freeze_time("2024-01-01 12:00:55"):
            c.set("x", "new")
        with freeze_time("2024-01-01 12:01:50"):
            hit, val = c.get("x", 60.0)
        assert hit
        assert val == "new"


# ---------------------------------------------------------------------------
# VoyagerClient.get_me caching
# ---------------------------------------------------------------------------

class TestVoyagerClientGetMeCache:
    def _make_voyager(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import client as c
        return c.VoyagerClient(li_at="x", jsessionid="y", user_data_dir="/tmp/fake")

    def test_get_me_not_called_twice(self):
        vc = self._make_voyager()
        fake_profile = {"first_name": "A", "last_name": "B", "headline": "", "public_id": "ab", "entity_urn": "", "picture_url": ""}

        with patch.object(vc, "_get", return_value={"included": [], "data": {}}) as mock_get:
            # Seed the cache directly so the raw _get result doesn't need to parse
            vc._cache.set("get_me", fake_profile)
            result1 = vc.get_me()
            result2 = vc.get_me()
            mock_get.assert_not_called()
        assert result1 == fake_profile
        assert result2 == fake_profile

    def test_get_me_fetches_on_cache_miss(self):
        vc = self._make_voyager()
        fake_raw = {
            "included": [{"occupation": "Dev", "firstName": "Jo", "lastName": "Smith", "publicIdentifier": "jo", "entityUrn": "urn:x", "picture": {}}],
            "data": {},
        }
        with patch.object(vc, "_get", return_value=fake_raw) as mock_get:
            result = vc.get_me()
        mock_get.assert_called_once_with("/me")
        assert result["first_name"] == "Jo"

    def test_get_me_refetched_after_ttl(self):
        vc = self._make_voyager()
        fake_raw = {
            "included": [{"occupation": "Dev", "firstName": "Jo", "lastName": "Smith", "publicIdentifier": "jo", "entityUrn": "urn:x", "picture": {}}],
            "data": {},
        }
        with freeze_time("2024-01-01 12:00:00"):
            with patch.object(vc, "_get", return_value=fake_raw) as mock_get:
                vc.get_me()

        with freeze_time("2024-01-01 12:05:01"):  # 301 s later, TTL=300
            with patch.object(vc, "_get", return_value=fake_raw) as mock_get2:
                vc.get_me()
            mock_get2.assert_called_once_with("/me")


# ---------------------------------------------------------------------------
# VoyagerClient.get_recent_posts caching
# ---------------------------------------------------------------------------

class TestVoyagerClientPostsCache:
    def _make_voyager(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import client as c
        return c.VoyagerClient(li_at="x", jsessionid="y", user_data_dir="/tmp/fake")

    def test_posts_served_from_cache(self):
        vc = self._make_voyager()
        fake_posts = [{"urn": "u1", "text": "Hello", "time": ""}]
        vc._cache.set("posts:john:10", fake_posts)

        with patch.object(vc, "_browser_scrape_posts") as mock_scrape:
            result = vc.get_recent_posts("john", 10)
            mock_scrape.assert_not_called()
        assert result == fake_posts

    def test_posts_cached_after_first_fetch(self):
        vc = self._make_voyager()
        fake_posts = [{"urn": "u2", "text": "World", "time": ""}]

        with patch.object(vc, "_browser_scrape_posts", return_value=fake_posts) as mock_scrape:
            vc.get_recent_posts("jane", 5)
            vc.get_recent_posts("jane", 5)
        mock_scrape.assert_called_once_with("jane", 5)

    def test_different_count_different_cache_key(self):
        vc = self._make_voyager()
        posts_5 = [{"urn": "a", "text": "Five", "time": ""}]
        posts_10 = [{"urn": "b", "text": "Ten", "time": ""}]

        def fake_scrape(pid, cnt):
            return posts_5 if cnt == 5 else posts_10

        with patch.object(vc, "_browser_scrape_posts", side_effect=fake_scrape) as mock_scrape:
            r5 = vc.get_recent_posts("alice", 5)
            r10 = vc.get_recent_posts("alice", 10)
        assert r5 == posts_5
        assert r10 == posts_10
        assert mock_scrape.call_count == 2
