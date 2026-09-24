import pytest


@pytest.fixture(autouse=True)
def _no_real_feed_requests(monkeypatch):
    """The sampler's feed sensor is free but still real HTTP - never let a
    test reach it. Tests that want signals patch this again themselves."""
    monkeypatch.setattr("trip_hunter.daily_sampler.scan_feeds", lambda **kwargs: [])
