"""Reads Trip Hunter configuration from environment variables.

All configuration - especially secrets like API keys - comes from
TRIP_HUNTER_* environment variables, never from source code. Copy
.env.example to .env and fill in real values; .env is git-ignored and is
loaded automatically (if present) the first time this module is imported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_PREFIX = "TRIP_HUNTER_"
# Trip Hunter was renamed from "Vacation Hunter" (see docs/PRODUCT_SPEC.md).
# A local .env written before the rename still has VACATION_HUNTER_* keys -
# _env() below falls back to those so existing setups keep working without
# forcing an immediate .env edit. New setups should use TRIP_HUNTER_* only;
# this fallback is not documented in .env.example on purpose.
_LEGACY_ENV_PREFIX = "VACATION_HUNTER_"


def _env(name: str, default: str | None = None) -> str | None:
    """Read one config value, preferring TRIP_HUNTER_<name>. Falls back to
    the legacy VACATION_HUNTER_<name> only if the new variable isn't set.
    """
    value = os.environ.get(f"{_ENV_PREFIX}{name}")
    if value is not None:
        return value
    return os.environ.get(f"{_LEGACY_ENV_PREFIX}{name}", default)


def _load_dotenv_if_present() -> None:
    """Minimal .env loader: no extra dependency needed for a handful of
    KEY=VALUE lines. Only fills in variables not already set in the real
    environment, so real environment variables always take precedence.
    """
    dotenv_path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(dotenv_path):
        return
    with open(dotenv_path, encoding="utf-8") as dotenv_file:
        for line in dotenv_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv_if_present()


class MissingConfigError(RuntimeError):
    """Required TRIP_HUNTER_* environment variables are missing."""


@dataclass(frozen=True)
class FlightApiConfig:
    api_key: str
    api_secret: str
    base_url: str
    cache_ttl_seconds: int
    request_timeout_seconds: float


@dataclass(frozen=True)
class SerpApiConfig:
    api_key: str
    cache_ttl_seconds: int
    request_timeout_seconds: float
    currency: str


def load_flight_api_config() -> FlightApiConfig:
    api_key = _env("FLIGHT_API_KEY")
    api_secret = _env("FLIGHT_API_SECRET")

    if not api_key or not api_secret:
        raise MissingConfigError(
            f"{_ENV_PREFIX}FLIGHT_API_KEY and {_ENV_PREFIX}FLIGHT_API_SECRET must be set "
            "(e.g. via a .env file - see .env.example)."
        )

    base_url = _env("FLIGHT_API_BASE_URL", "https://test.api.amadeus.com")
    cache_ttl_seconds = int(_env("CACHE_TTL_SECONDS", str(24 * 60 * 60)))
    request_timeout_seconds = float(_env("REQUEST_TIMEOUT_SECONDS", "10"))

    return FlightApiConfig(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        cache_ttl_seconds=cache_ttl_seconds,
        request_timeout_seconds=request_timeout_seconds,
    )


_DEFAULT_ORIGINS = ("HAM", "BER", "FRA", "MUC", "DUS")


def load_origins() -> list[str]:
    """The configured departure-airport rotation: TRIP_HUNTER_ORIGINS,
    comma-separated IATA codes (e.g. "HAM,BER,FRA,MUC,DUS") - falls back
    to the primary German hub cluster (_DEFAULT_ORIGINS) when unset or
    empty/blank. Order matters: sampling_targets.py's origin_of_the_day()
    rotates through the returned list in this exact order.
    """
    raw = _env("ORIGINS")
    if not raw:
        return list(_DEFAULT_ORIGINS)
    origins = [code.strip().upper() for code in raw.split(",") if code.strip()]
    return origins or list(_DEFAULT_ORIGINS)


def load_serpapi_config() -> SerpApiConfig:
    api_key = _env("SERPAPI_KEY")

    if not api_key:
        raise MissingConfigError(
            f"{_ENV_PREFIX}SERPAPI_KEY must be set (e.g. via a .env file - see .env.example)."
        )

    cache_ttl_seconds = int(_env("CACHE_TTL_SECONDS", str(24 * 60 * 60)))
    request_timeout_seconds = float(_env("REQUEST_TIMEOUT_SECONDS", "10"))
    currency = _env("SERPAPI_CURRENCY", "EUR")

    return SerpApiConfig(
        api_key=api_key,
        cache_ttl_seconds=cache_ttl_seconds,
        request_timeout_seconds=request_timeout_seconds,
        currency=currency,
    )
