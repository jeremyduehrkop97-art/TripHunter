"""Reads Vacation Hunter configuration from environment variables.

All configuration - especially secrets like API keys - comes from
VACATION_HUNTER_* environment variables, never from source code. Copy
.env.example to .env and fill in real values; .env is git-ignored and is
loaded automatically (if present) the first time this module is imported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_PREFIX = "VACATION_HUNTER_"


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
    """Required VACATION_HUNTER_* environment variables are missing."""


@dataclass(frozen=True)
class FlightApiConfig:
    api_key: str
    api_secret: str
    base_url: str
    cache_ttl_seconds: int
    request_timeout_seconds: float


def load_flight_api_config() -> FlightApiConfig:
    api_key = os.environ.get(f"{_ENV_PREFIX}FLIGHT_API_KEY")
    api_secret = os.environ.get(f"{_ENV_PREFIX}FLIGHT_API_SECRET")

    if not api_key or not api_secret:
        raise MissingConfigError(
            f"{_ENV_PREFIX}FLIGHT_API_KEY and {_ENV_PREFIX}FLIGHT_API_SECRET must be set "
            "(e.g. via a .env file - see .env.example)."
        )

    base_url = os.environ.get(
        f"{_ENV_PREFIX}FLIGHT_API_BASE_URL", "https://test.api.amadeus.com"
    )
    cache_ttl_seconds = int(
        os.environ.get(f"{_ENV_PREFIX}CACHE_TTL_SECONDS", str(24 * 60 * 60))
    )
    request_timeout_seconds = float(
        os.environ.get(f"{_ENV_PREFIX}REQUEST_TIMEOUT_SECONDS", "10")
    )

    return FlightApiConfig(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        cache_ttl_seconds=cache_ttl_seconds,
        request_timeout_seconds=request_timeout_seconds,
    )
