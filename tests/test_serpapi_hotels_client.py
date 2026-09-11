"""Tests for SerpApiHotelsClient. All HTTP is faked - no real network calls."""

from __future__ import annotations

import pytest
import requests

from trip_hunter.providers.serpapi_hotels_client import SerpApiHotelsClient
from trip_hunter.providers.errors import (
    FlightProviderHTTPError,
    FlightProviderRateLimitedError,
    FlightProviderResponseError,
    FlightProviderTimeoutError,
)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", raise_json_error=False):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("invalid json")
        return self._json_data


class _FakeSession:
    def __init__(self, get_response=None, raise_on_get=None):
        self._get_response = get_response
        self._raise_on_get = raise_on_get
        self.last_params = None

    def get(self, *args, **kwargs):
        self.last_params = kwargs.get("params")
        if self._raise_on_get:
            raise self._raise_on_get
        return self._get_response


def _client(session: _FakeSession) -> SerpApiHotelsClient:
    return SerpApiHotelsClient("secret-key", session=session)


def test_search_hotels_returns_payload():
    session = _FakeSession(get_response=_FakeResponse(status_code=200, json_data={"properties": []}))
    result = _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")
    assert result == {"properties": []}


def test_search_hotels_sends_expected_query_params():
    session = _FakeSession(get_response=_FakeResponse(status_code=200, json_data={"properties": []}))
    _client(session).search_hotels(
        "Palma de Mallorca, Spain", "2026-10-02", "2026-10-07", currency="USD", adults=3
    )
    params = session.last_params
    assert params["engine"] == "google_hotels"
    assert params["q"] == "Palma de Mallorca, Spain"
    assert params["check_in_date"] == "2026-10-02"
    assert params["check_out_date"] == "2026-10-07"
    assert params["currency"] == "USD"
    assert params["adults"] == 3
    assert params["api_key"] == "secret-key"


def test_api_error_field_raises_response_error():
    session = _FakeSession(
        get_response=_FakeResponse(status_code=200, json_data={"error": "Invalid api_key."})
    )
    with pytest.raises(FlightProviderResponseError):
        _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")


def test_rate_limit_raises_rate_limited_error():
    session = _FakeSession(get_response=_FakeResponse(status_code=429, text="Too Many Requests"))
    with pytest.raises(FlightProviderRateLimitedError):
        _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")


def test_http_error_raises_http_error():
    session = _FakeSession(get_response=_FakeResponse(status_code=500, text="Internal Server Error"))
    with pytest.raises(FlightProviderHTTPError):
        _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")


def test_timeout_raises_timeout_error():
    session = _FakeSession(raise_on_get=requests.exceptions.Timeout())
    with pytest.raises(FlightProviderTimeoutError):
        _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")


def test_connection_error_raises_http_error():
    session = _FakeSession(raise_on_get=requests.exceptions.ConnectionError())
    with pytest.raises(FlightProviderHTTPError):
        _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")


def test_invalid_json_raises_response_error():
    session = _FakeSession(get_response=_FakeResponse(status_code=200, raise_json_error=True))
    with pytest.raises(FlightProviderResponseError):
        _client(session).search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")


def test_api_key_never_appears_in_raised_error_messages():
    session = _FakeSession(get_response=_FakeResponse(status_code=500, text="server error"))
    client = SerpApiHotelsClient("super-secret-key", session=session)
    with pytest.raises(FlightProviderHTTPError) as excinfo:
        client.search_hotels("Palma de Mallorca, Spain", "2026-10-02", "2026-10-07")
    assert "super-secret-key" not in str(excinfo.value)
