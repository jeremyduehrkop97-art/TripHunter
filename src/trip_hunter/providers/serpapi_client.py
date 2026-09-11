"""Low-level HTTP client for SerpApi's Google Flights engine (engine=google_flights).

Handles raw HTTP only - no business logic, no knowledge of FlightOffer or
PriceInsight. That translation happens in serpapi_flight_provider.py, so
the rest of the system never has to look like SerpApi/Google Flights. See
docs/ARCHITECTURE.md.

SerpApi authenticates via an `api_key` query parameter rather than a header
- this client never logs or includes `params` in an exception message, so
the key never ends up in a stack trace or terminal output.
"""

from __future__ import annotations

from typing import Any

import requests

from trip_hunter.providers.errors import (
    FlightProviderHTTPError,
    FlightProviderRateLimitedError,
    FlightProviderResponseError,
    FlightProviderTimeoutError,
)

_DEFAULT_BASE_URL = "https://serpapi.com/search"
_ENGINE = "google_flights"
_TYPE_ROUND_TRIP = "1"
_TYPE_ONE_WAY = "2"


class SerpApiClient:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 10.0,
        session: requests.Session | None = None,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._base_url = base_url

    def search_flights(
        self,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: str | None = None,
        currency: str = "EUR",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": _ENGINE,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound_date,
            "currency": currency,
            "api_key": self._api_key,
        }
        if return_date:
            params["return_date"] = return_date
            params["type"] = _TYPE_ROUND_TRIP
        else:
            params["type"] = _TYPE_ONE_WAY

        try:
            response = self._session.get(
                self._base_url, params=params, timeout=self._timeout_seconds
            )
        except requests.exceptions.Timeout as exc:
            raise FlightProviderTimeoutError("SerpApi request timed out.") from exc
        except requests.exceptions.RequestException as exc:
            raise FlightProviderHTTPError(0, f"SerpApi request failed: {exc}") from exc

        if response.status_code == 429:
            raise FlightProviderRateLimitedError("SerpApi rate limit exceeded.")
        if response.status_code >= 400:
            raise FlightProviderHTTPError(
                response.status_code,
                f"SerpApi request failed with status {response.status_code}: "
                f"{response.text[:500]}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise FlightProviderResponseError("SerpApi response was not valid JSON.") from exc

        # SerpApi can return HTTP 200 with a soft error, e.g. an invalid
        # airport code or an exhausted search quota.
        error = payload.get("error")
        if error:
            raise FlightProviderResponseError(f"SerpApi returned an error: {error}")

        return payload
