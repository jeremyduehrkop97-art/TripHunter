"""Tests for SerpApiClient. All HTTP is faked - no real network calls."""

from __future__ import annotations

import pytest
import requests

from vacation_hunter.providers.serpapi_client import SerpApiClient
from vacation_hunter.providers.errors import (
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

    def get(self, *args, **kwargs):
        if self._raise_on_get:
            raise self._raise_on_get
        return self._get_response


def _client(session: _FakeSession) -> SerpApiClient:
    return SerpApiClient("secret-key", session=session)


def test_search_flights_returns_payload():
    session = _FakeSession(get_response=_FakeResponse(status_code=200, json_data={"best_flights": []}))
    result = _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")
    assert result == {"best_flights": []}


def test_api_error_field_raises_response_error():
    session = _FakeSession(
        get_response=_FakeResponse(status_code=200, json_data={"error": "Invalid api_key."})
    )
    with pytest.raises(FlightProviderResponseError):
        _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_rate_limit_raises_rate_limited_error():
    session = _FakeSession(get_response=_FakeResponse(status_code=429, text="Too Many Requests"))
    with pytest.raises(FlightProviderRateLimitedError):
        _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_http_error_raises_http_error():
    session = _FakeSession(get_response=_FakeResponse(status_code=500, text="Internal Server Error"))
    with pytest.raises(FlightProviderHTTPError):
        _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_timeout_raises_timeout_error():
    session = _FakeSession(raise_on_get=requests.exceptions.Timeout())
    with pytest.raises(FlightProviderTimeoutError):
        _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_connection_error_raises_http_error():
    session = _FakeSession(raise_on_get=requests.exceptions.ConnectionError())
    with pytest.raises(FlightProviderHTTPError):
        _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_invalid_json_raises_response_error():
    session = _FakeSession(get_response=_FakeResponse(status_code=200, raise_json_error=True))
    with pytest.raises(FlightProviderResponseError):
        _client(session).search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_api_key_never_appears_in_raised_error_messages():
    session = _FakeSession(get_response=_FakeResponse(status_code=500, text="server error"))
    client = SerpApiClient("super-secret-key", session=session)
    with pytest.raises(FlightProviderHTTPError) as excinfo:
        client.search_flights("HAM", "PMI", "2026-10-02", "2026-10-07")
    assert "super-secret-key" not in str(excinfo.value)
