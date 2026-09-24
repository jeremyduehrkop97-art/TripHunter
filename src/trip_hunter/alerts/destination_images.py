"""Destination photos for Telegram alerts (sent via sendPhoto, see
dispatch/telegram.py).

Same explicit IATA-code -> value pattern as destination_context.py: a
small, hand-picked, reviewable mapping for the destinations this project
actually samples (see sampling_targets.py), with a generic travel photo
as the fallback for anything not yet covered. Every URL is a public
Unsplash image (free under the Unsplash License) that Telegram's servers
fetch themselves - nothing is downloaded or stored by this project. The
Free channel shows the very same photo, blurred via Telegram's own
`has_spoiler` flag rather than a separately generated image.

To add a destination: pick a photo on unsplash.com, resolve
https://unsplash.com/photos/<id>/download?force=true to its
images.unsplash.com URL, and add it below.
"""

from __future__ import annotations

_PARAMS = "?auto=format&fit=crop&w=1280&q=80"

_DESTINATION_IMAGES: dict[str, str] = {
    # Palma Cathedral (La Seu) - Yves Alarie
    "PMI": f"https://images.unsplash.com/photo-1566993850067-bb8df9c9807e{_PARAMS}",
    # Sagrada Familia - Sol Ponce
    "BCN": f"https://images.unsplash.com/photo-1745091723338-cbe6561908ea{_PARAMS}",
    # Colosseum - Mathew Schwartz
    "FCO": f"https://images.unsplash.com/photo-1515542483964-5e8c63d7d89b{_PARAMS}",
    # Lisbon tram - Theodor Vasile
    "LIS": f"https://images.unsplash.com/photo-1575373350254-9ab842370a47{_PARAMS}",
}

# Generic sunset-beach photo - deliberately not tied to any one place, so
# it never misrepresents a destination we have no real photo for.
FALLBACK_IMAGE_URL = f"https://images.unsplash.com/photo-1507525428034-b723cf961d3e{_PARAMS}"


def destination_image_url(destination: str) -> str:
    """Photo URL for `destination` (IATA code), or FALLBACK_IMAGE_URL."""
    return _DESTINATION_IMAGES.get(destination, FALLBACK_IMAGE_URL)
