"""Tests for cache.py - TTLCache."""

import time
from unittest.mock import patch

from cache import TTLCache, RESPONSE_CACHE


class TestTTLCache:
    def test_get_returns_none_on_empty(self):
        cache = TTLCache()
        assert cache.get("nonexistent") is None

    def test_set_and_get_within_ttl(self):
        cache = TTLCache()
        cache.set("key1", {"data": "value"}, 300)
        assert cache.get("key1") == {"data": "value"}

    def test_get_returns_none_after_expiry(self):
        cache = TTLCache()
        base = time.monotonic()
        with patch("cache.time.monotonic", return_value=base):
            cache.set("key1", "value", 10)
        with patch("cache.time.monotonic", return_value=base + 11):
            assert cache.get("key1") is None
        assert "key1" not in cache._store

    def test_set_overwrites_existing(self):
        cache = TTLCache()
        cache.set("key1", "old", 300)
        cache.set("key1", "new", 300)
        assert cache.get("key1") == "new"

    def test_invalidate_prefix_removes_matching(self):
        cache = TTLCache()
        cache.set("host1:fw", "a", 300)
        cache.set("host1:sys", "b", 300)
        cache.set("host2:fw", "c", 300)
        removed = cache.invalidate_prefix("host1:")
        assert removed == 2
        assert cache.get("host1:fw") is None
        assert cache.get("host1:sys") is None
        assert cache.get("host2:fw") == "c"

    def test_invalidate_prefix_no_match_returns_zero(self):
        cache = TTLCache()
        cache.set("host1:fw", "a", 300)
        assert cache.invalidate_prefix("host99:") == 0

    def test_clear_removes_all(self):
        cache = TTLCache()
        cache.set("a", 1, 300)
        cache.set("b", 2, 300)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache._store) == 0

    def test_len_excludes_expired(self):
        cache = TTLCache()
        base = time.monotonic()
        with patch("cache.time.monotonic", return_value=base):
            cache.set("live", "val", 300)
            cache.set("expired", "val", 1)
        with patch("cache.time.monotonic", return_value=base + 5):
            assert len(cache) == 1

    def test_len_includes_valid(self):
        cache = TTLCache()
        cache.set("a", 1, 300)
        cache.set("b", 2, 300)
        assert len(cache) == 2

    def test_module_singleton(self):
        assert isinstance(RESPONSE_CACHE, TTLCache)
