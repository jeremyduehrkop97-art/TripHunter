"""Low-level HTTP client for SerpApi's Google Hotels engine (engine=google_hotels).

Handles raw HTTP only - no business logic, no knowledge of AccommodationOffer.
That translation happens in serpapi_accommodation_provider.py, so the rest
of the system never has to look like SerpApi/Google Hotels. Mirrors
serpapi_client.py's structure exactly - see docs/ARCHITECTURE.md.

Reuses the FlightProvider* error classes from providers/errors.py, per the
project's "same error handling for every real provider" convention (see
providers/errors.py's own docstring). Their "Flight"-prefixed names are a
naming leftover from when SerpApi's Google Flights engine was the only real
integration - functionally generic (timeout/rate limit/HTTP/response), just
misnamed for a non-flight caller. Not renamed here to avoid touching every
existing provider file for an unrelated feature; a future generic
`ProviderTimeoutError` etc. rename is a reasonable follow-up.

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
_ENGINE = "google_hotels"


class SerpApiHotelsClient:
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

    def search_hotels(
        self,
        query: str,
        check_in_date: str,
        check_out_date: str,
        currency: str = "EUR",
        adults: int = 2,
    ) -> dict[str, Any]:
        """`query` is a free-text location (e.g. "Palma de Mallorca, Spain"),
        NOT an airport/IATA code - Google Hotels has no concept of one.
        Resolving a destination code to a query string is the CALLER's job
        (see serpapi_accommodation_provider.py); this client never guesses.
        """
        params: dict[str, Any] = {
            "engine": _ENGINE,
            "q": query,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "currency": currency,
            "adults": adults,
            "api_key": self._api_key,
        }

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
        # query or an exhausted search quota.
        error = payload.get("error")
        if error:
            raise FlightProviderResponseError(f"SerpApi returned an error: {error}")

        return payload
