"""Low-level HTTP client for the Amadeus Self-Service Flight Offers Search API.

Handles OAuth2 (client-credentials) authentication and raw HTTP requests
only. It does not know about our FlightOffer model - that translation
happens in amadeus_flight_provider.py, so the rest of the system never has
to look like Amadeus. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from vacation_hunter.providers.errors import (
    FlightProviderHTTPError,
    FlightProviderRateLimitedError,
    FlightProviderResponseError,
    FlightProviderTimeoutError,
)

_TOKEN_PATH = "/v1/security/oauth2/token"
_FLIGHT_OFFERS_PATH = "/v2/shopping/flight-offers"

# Refresh a bit before actual expiry to avoid racing a token that expires
# mid-request.
_TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 30


class AmadeusClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_seconds: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def search_flight_offers(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        currency_code: str = "EUR",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": departure_date,
            "adults": 1,
            "currencyCode": currency_code,
            "max": max_results,
        }
        if return_date:
            params["returnDate"] = return_date

        response_json = self._get(_FLIGHT_OFFERS_PATH, params=params)

        data = response_json.get("data")
        if data is None:
            raise FlightProviderResponseError("Amadeus response did not contain a 'data' field.")
        return data

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        token = self._get_access_token()
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = self._session.get(
                url, headers=headers, params=params, timeout=self._timeout_seconds
            )
        except requests.exceptions.Timeout as exc:
            raise FlightProviderTimeoutError(f"Amadeus request to {path} timed out.") from exc
        except requests.exceptions.RequestException as exc:
            raise FlightProviderHTTPError(0, f"Amadeus request to {path} failed: {exc}") from exc

        self._raise_for_status(response, path)

        try:
            return response.json()
        except ValueError as exc:
            raise FlightProviderResponseError(
                f"Amadeus response from {path} was not valid JSON."
            ) from exc

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        try:
            response = self._session.post(
                f"{self._base_url}{_TOKEN_PATH}",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._api_key,
                    "client_secret": self._api_secret,
                },
                timeout=self._timeout_seconds,
            )
        except requests.exceptions.Timeout as exc:
            raise FlightProviderTimeoutError("Amadeus authentication request timed out.") from exc
        except requests.exceptions.RequestException as exc:
            raise FlightProviderHTTPError(0, f"Amadeus authentication failed: {exc}") from exc

        self._raise_for_status(response, _TOKEN_PATH)

        try:
            payload = response.json()
            access_token = payload["access_token"]
            expires_in = payload.get("expires_in", 1800)
        except (ValueError, KeyError) as exc:
            raise FlightProviderResponseError(
                "Amadeus authentication response was missing expected fields."
            ) from exc

        self._access_token = access_token
        self._token_expires_at = time.time() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS
        return access_token

    @staticmethod
    def _raise_for_status(response: Any, path: str) -> None:
        if response.status_code == 429:
            raise FlightProviderRateLimitedError(f"Amadeus rate limit exceeded on {path}.")
        if response.status_code >= 400:
            raise FlightProviderHTTPError(
                response.status_code,
                f"Amadeus request to {path} failed with status {response.status_code}: "
                f"{response.text[:500]}",
            )
