"""Errors raised by real (network-backed) provider integrations.

Kept intentionally small and flat - callers only need to distinguish a
handful of cases (config missing, timeout, rate limit, bad HTTP status,
unparseable response), not navigate a deep exception hierarchy. A real
provider must never let one of these escape as a raw, unhandled exception
that crashes the whole deal-detection run.
"""

from __future__ import annotations


class FlightProviderError(Exception):
    """Base class for all real flight-provider failures."""


class FlightProviderTimeoutError(FlightProviderError):
    """The provider did not respond in time."""


class FlightProviderRateLimitedError(FlightProviderError):
    """The provider rejected the request due to rate limiting."""


class FlightProviderHTTPError(FlightProviderError):
    """The provider returned an HTTP error status."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class FlightProviderResponseError(FlightProviderError):
    """The provider's response could not be parsed or was missing required fields."""
