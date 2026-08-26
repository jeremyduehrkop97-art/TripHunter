import time

from vacation_hunter.caching import FileCache, flight_search_cache_key


def test_cache_miss_returns_none(tmp_path):
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    assert cache.get("missing-key") is None


def test_cache_hit_returns_stored_value(tmp_path):
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    cache.set("key", {"hello": "world"})
    assert cache.get("key") == {"hello": "world"}


def test_cache_entry_expires_after_ttl(tmp_path):
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=0)
    cache.set("key", {"hello": "world"})
    time.sleep(0.01)
    assert cache.get("key") is None


def test_corrupted_cache_file_is_treated_as_miss(tmp_path):
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    cache.set("key", {"hello": "world"})
    cache._path_for("key").write_text("not valid json", encoding="utf-8")
    assert cache.get("key") is None


def test_flight_search_cache_key_includes_route_and_dates():
    key_a = flight_search_cache_key("HAM", "PMI", "2026-10-02", "2026-10-07")
    key_b = flight_search_cache_key("HAM", "AGP", "2026-10-02", "2026-10-07")
    assert key_a != key_b
