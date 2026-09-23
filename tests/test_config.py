import pytest

from trip_hunter.config import MissingConfigError, load_flight_api_config, load_origins, load_serpapi_config


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


# --- load_origins() (multi-origin / flexible Abflughäfen) -----------------


@pytest.fixture(autouse=True)
def _clean_origins_env(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_ORIGINS", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_ORIGINS", raising=False)


def test_load_origins_defaults_to_the_primary_german_hub_cluster():
    assert load_origins() == ["HAM", "BER", "FRA", "MUC", "DUS"]


def test_load_origins_can_be_overridden_via_environment(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_ORIGINS", "VIE,ZRH")

    assert load_origins() == ["VIE", "ZRH"]


def test_load_origins_strips_whitespace_and_uppercases(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_ORIGINS", " ham , ber ,fra ")

    assert load_origins() == ["HAM", "BER", "FRA"]


def test_load_origins_falls_back_to_default_when_environment_value_is_blank(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_ORIGINS", "   ")

    assert load_origins() == ["HAM", "BER", "FRA", "MUC", "DUS"]


def test_load_origins_ignores_empty_entries_between_commas(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_ORIGINS", "HAM,,BER,")

    assert load_origins() == ["HAM", "BER"]


def test_load_origins_returns_a_fresh_list_each_call():
    """Callers may safely mutate the returned list without corrupting the
    default cluster for the next call."""
    origins = load_origins()
    origins.append("XXX")

    assert load_origins() == ["HAM", "BER", "FRA", "MUC", "DUS"]
