import pytest

from trip_hunter.config import MissingConfigError, load_flight_api_config, load_serpapi_config


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_FLIGHT_API_KEY", raising=False)
    monkeypatch.delenv("TRIP_HUNTER_FLIGHT_API_SECRET", raising=False)

    with pytest.raises(MissingConfigError):
        load_flight_api_config()


def test_missing_api_secret_raises(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_FLIGHT_API_KEY", "key123")
    monkeypatch.delenv("TRIP_HUNTER_FLIGHT_API_SECRET", raising=False)

    with pytest.raises(MissingConfigError):
        load_flight_api_config()


def test_loads_config_from_environment(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_FLIGHT_API_KEY", "key123")
    monkeypatch.setenv("TRIP_HUNTER_FLIGHT_API_SECRET", "secret456")
    monkeypatch.setenv("TRIP_HUNTER_CACHE_TTL_SECONDS", "60")

    config = load_flight_api_config()

    assert config.api_key == "key123"
    assert config.api_secret == "secret456"
    assert config.cache_ttl_seconds == 60
    assert config.base_url == "https://test.api.amadeus.com"


def test_base_url_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_FLIGHT_API_KEY", "key123")
    monkeypatch.setenv("TRIP_HUNTER_FLIGHT_API_SECRET", "secret456")
    monkeypatch.setenv("TRIP_HUNTER_FLIGHT_API_BASE_URL", "https://api.amadeus.com")

    config = load_flight_api_config()

    assert config.base_url == "https://api.amadeus.com"


def test_missing_serpapi_key_raises(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_SERPAPI_KEY", raising=False)  # legacy fallback

    with pytest.raises(MissingConfigError):
        load_serpapi_config()


def test_loads_serpapi_config_from_environment(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "serp-key-123")
    monkeypatch.setenv("TRIP_HUNTER_CACHE_TTL_SECONDS", "60")

    config = load_serpapi_config()

    assert config.api_key == "serp-key-123"
    assert config.cache_ttl_seconds == 60
    assert config.currency == "EUR"


def test_serpapi_currency_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "serp-key-123")
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_CURRENCY", "USD")

    config = load_serpapi_config()

    assert config.currency == "USD"


# --- Legacy VACATION_HUNTER_* fallback (Trip Hunter rename) --------------
# A local .env written before the rename still has VACATION_HUNTER_* keys;
# config.py must keep reading it without forcing an immediate .env edit.


def test_legacy_vacation_hunter_key_is_used_when_new_key_is_absent(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_SERPAPI_KEY", raising=False)
    monkeypatch.setenv("VACATION_HUNTER_SERPAPI_KEY", "legacy-serp-key")

    config = load_serpapi_config()

    assert config.api_key == "legacy-serp-key"


def test_new_key_takes_precedence_over_legacy_key(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "new-serp-key")
    monkeypatch.setenv("VACATION_HUNTER_SERPAPI_KEY", "legacy-serp-key")

    config = load_serpapi_config()

    assert config.api_key == "new-serp-key"
