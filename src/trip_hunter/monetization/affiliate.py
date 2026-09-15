"""Affiliate link decoration: appends a configured tracking tag to a
booking link's query string.

No real affiliate program is integrated (yet) - this deliberately stays
provider-agnostic and uses one generic query parameter and one global tag,
not per-OTA parameter names/programs. That's a known simplification for a
future multi-program setup, not built here since none is requested.

Configuration: TRIP_HUNTER_AFFILIATE_TAG (see .env.example). Unlike
VACATION_HUNTER_SERPAPI_KEY, this option was introduced after the Trip
Hunter rename, so there is no legacy VACATION_HUNTER_AFFILIATE_TAG to fall
back to (see config.py's _env() for that pattern, used only where a
pre-rename value could actually exist).

Falls back transparently to the original URL when no tag is configured -
never raises, never fabricates a tag. A formatter should always be able to
render SOME link (the real one) even with affiliate tracking unconfigured.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Importing config.py (even though we only use os.environ directly below)
# triggers its .env-loading side effect at import time, so a local .env
# entry for TRIP_HUNTER_AFFILIATE_TAG is picked up the same way every
# other TRIP_HUNTER_* setting already is - without duplicating that
# dotenv-parsing logic here.
import trip_hunter.config  # noqa: F401

_AFFILIATE_TAG_ENV_VAR = "TRIP_HUNTER_AFFILIATE_TAG"
_AFFILIATE_QUERY_PARAM = "tp_aff"


def get_affiliate_tag() -> str | None:
    """The configured affiliate tag, or None if unset/empty."""
    return os.environ.get(_AFFILIATE_TAG_ENV_VAR) or None


def add_affiliate_tag(url: str | None, *, tag: str | None = None) -> str | None:
    """Return `url` with the affiliate tag appended as a query parameter.

    `url=None` passes through as None (nothing to decorate - e.g. a
    booking link the provider never supplied). `tag` defaults to
    `get_affiliate_tag()`; pass it explicitly only to override the
    environment (mainly for tests). If no tag is configured at all, `url`
    is returned completely unchanged.

    Uses urllib.parse rather than string concatenation so this behaves
    correctly for every URL shape: an existing query string, an existing
    fragment, or neither - never a naive "just append ?tp_aff=..." that
    would produce a broken URL like "...?foo=bar?tp_aff=xyz".
    """
    if url is None:
        return None

    resolved_tag = tag if tag is not None else get_affiliate_tag()
    if not resolved_tag:
        return url

    scheme, netloc, path, query, fragment = urlsplit(url)
    query_params = parse_qsl(query, keep_blank_values=True)
    query_params.append((_AFFILIATE_QUERY_PARAM, resolved_tag))
    new_query = urlencode(query_params)
    return urlunsplit((scheme, netloc, path, new_query, fragment))
