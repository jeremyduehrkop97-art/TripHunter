"""Tests for AmadeusClient. All HTTP is faked - no real network calls."""

from __future__ import annotations

import pytest
import requests

from trip_hunter.providers.amadeus_client import AmadeusClient
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
    def __init__(self, post_response=None, get_response=None, raise_on_get=None, raise_on_post=None):
        self._post_response = post_response
        self._get_response = get_response
        self._raise_on_get = raise_on_get
        self._raise_on_post = raise_on_post

    def post(self, *args, **kwargs):
        if self._raise_on_post:
            raise self._raise_on_post
        return self._post_response

    def get(self, *args, **kwargs):
        if self._raise_on_get:
            raise self._raise_on_get
        return self._get_response


def _token_response() -> _FakeResponse:
    return _FakeResponse(status_code=200, json_data={"access_token": "tok123", "expires_in": 1800})


def _client(session: _FakeSession) -> AmadeusClient:
    return AmadeusClient("key", "secret", "https://test.api.amadeus.com", session=session)


def test_search_flight_offers_returns_data():
    session = _FakeSession(
        post_response=_token_response(),
        get_response=_FakeResponse(status_code=200, json_data={"data": [{"id": "1"}]}),
    )
    result = _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")
    assert result == [{"id": "1"}]


def test_missing_data_field_raises_response_error():
    session = _FakeSession(
        post_response=_token_response(),
        get_response=_FakeResponse(status_code=200, json_data={"unexpected": "shape"}),
    )
    with pytest.raises(FlightProviderResponseError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_rate_limit_raises_rate_limited_error():
    session = _FakeSession(
        post_response=_token_response(),
        get_response=_FakeResponse(status_code=429, text="Too Many Requests"),
    )
    with pytest.raises(FlightProviderRateLimitedError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_http_error_raises_http_error():
    session = _FakeSession(
        post_response=_token_response(),
        get_response=_FakeResponse(status_code=500, text="Internal Server Error"),
    )
    with pytest.raises(FlightProviderHTTPError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_timeout_raises_timeout_error():
    session = _FakeSession(post_response=_token_response(), raise_on_get=requests.exceptions.Timeout())
    with pytest.raises(FlightProviderTimeoutError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_invalid_json_raises_response_error():
    session = _FakeSession(
        post_response=_token_response(),
        get_response=_FakeResponse(status_code=200, raise_json_error=True),
    )
    with pytest.raises(FlightProviderResponseError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_auth_failure_raises_http_error():
    session = _FakeSession(post_response=_FakeResponse(status_code=401, text="Unauthorized"))
    with pytest.raises(FlightProviderHTTPError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")


def test_auth_timeout_raises_timeout_error():
    session = _FakeSession(raise_on_post=requests.exceptions.Timeout())
    with pytest.raises(FlightProviderTimeoutError):
        _client(session).search_flight_offers("HAM", "PMI", "2026-10-02", "2026-10-07")
