"""Destination photos for Telegram alerts (sent via sendPhoto, see
dispatch/telegram.py).

Same explicit IATA-code -> value pattern as destination_context.py: a
hand-picked, reviewable mapping for the destinations this project
actually surfaces (see sampling_targets.py and engine/feed_sensor.py's
_CITY_TO_IATA), with a generic travel photo as the fallback for anything
not yet covered. Every URL is a public Unsplash image (free under the
Unsplash License) that Telegram's servers fetch themselves - nothing is
downloaded or stored by this project. The Free channel shows the very
same photo, blurred via Telegram's own `has_spoiler` flag rather than a
separately generated image.

WHY NOT A "DYNAMIC" UNSPLASH LOOKUP: `source.unsplash.com` (the old
"random photo for a search term" redirect service this was tempted to
use) was discontinued by Unsplash - it now answers every request with
HTTP 503, confirmed live while building this table. A real dynamic photo
search needs Unsplash's authenticated Search API (an Access Key this
project doesn't have configured, plus rate limits and non-deterministic
results - the very opposite of "Telegram can just cache this URL"). So
the actual, reliable way to shrink how often a destination falls back to
the generic photo is the boring one: hand-pick more destinations - this
table now covers 40+ instead of the original 10. Genuinely uncovered
destinations still get FALLBACK_IMAGE_URL, which is honest (never
misrepresents a place) rather than a fragile "maybe it 404s" call.

To add a destination: pick a photo on unsplash.com, resolve
https://unsplash.com/photos/<id>/download?force=true to its
images.unsplash.com URL, and add it below.

NOTE (2026-09-29): this table's 45 entries were each individually sourced
and visually verified this way. A further expansion towards 60+ was
attempted for this task but had to be deferred - the dev sandbox's own
network blocks unsplash.com outright (a captive-portal content filter,
category "Media Sharing"; confirmed via both curl and WebFetch, both
returning the filter's block page/cert error instead of Unsplash).
images.unsplash.com (the CDN that actually serves the photos below) is
NOT blocked - already-added photos keep working - but new ones can't be
found or verified without unsplash.com itself. Do this from an
environment where unsplash.com is reachable.
"""

from __future__ import annotations

_PARAMS = "?auto=format&fit=crop&w=1280&q=80"

_DESTINATION_IMAGES: dict[str, str] = {
    # --- Europe (short/mid-haul) ---
    # Palma Cathedral (La Seu) - Yves Alarie
    "PMI": f"https://images.unsplash.com/photo-1566993850067-bb8df9c9807e{_PARAMS}",
    # Sagrada Familia - Sol Ponce
    "BCN": f"https://images.unsplash.com/photo-1745091723338-cbe6561908ea{_PARAMS}",
    # Colosseum - Mathew Schwartz
    "FCO": f"https://images.unsplash.com/photo-1515542483964-5e8c63d7d89b{_PARAMS}",
    # Lisbon tram - Theodor Vasile
    "LIS": f"https://images.unsplash.com/photo-1575373350254-9ab842370a47{_PARAMS}",
    # Duomo di Milano (Bergamo/Orio al Serio is Milan's low-cost airport)
    "BGY": f"https://images.unsplash.com/photo-1566662961381-8ff13ac24766{_PARAMS}",
    # Canal near the Bridge of Sighs, Venice
    "VCE": f"https://images.unsplash.com/photo-1767199289290-010e7caf8240{_PARAMS}",
    # Stephansdom, Vienna
    "VIE": f"https://images.unsplash.com/photo-1578400889704-bbd63485d516{_PARAMS}",
    # Tower Bridge at sunset (Stansted is a London airport)
    "STN": f"https://images.unsplash.com/photo-1567705925544-5634c0114dd9{_PARAMS}",
    # Algarve coast
    "FAO": f"https://images.unsplash.com/photo-1779485070200-a33a369afe5a{_PARAMS}",
    # Dom Luis I bridge over the Douro, Porto
    "OPO": f"https://images.unsplash.com/photo-1762294946283-6921938e9937{_PARAMS}",
    # Plaza Mayor, Madrid - Kristijan Arsov
    "MAD": f"https://images.unsplash.com/photo-1658922184767-d5335cb2a9d2{_PARAMS}",
    # Ibiza sunset over the sea
    "IBZ": f"https://images.unsplash.com/photo-1602212356541-b9b31ea96a42{_PARAMS}",
    # Mount Teide, Tenerife
    "TFS": f"https://images.unsplash.com/photo-1610302521145-3376354f1735{_PARAMS}",
    # Elafonissi pink-sand beach, Crete
    "HER": f"https://images.unsplash.com/photo-1654163170293-aa3b529f669f{_PARAMS}",
    # Old town alley, Rhodes
    "RHO": f"https://images.unsplash.com/photo-1680205415325-3ceebea06319{_PARAMS}",
    # Eiffel Tower, Paris - Anthony Delanoix
    "CDG": f"https://images.unsplash.com/photo-1511739001486-6bfe10ce785f{_PARAMS}",
    # Canal houses, Amsterdam - Tobias Reich
    "AMS": f"https://images.unsplash.com/photo-1754835143820-bcf20e2e1a35{_PARAMS}",
    # Charles Bridge, Prague
    "PRG": f"https://images.unsplash.com/photo-1755532883700-2dd663854b3b{_PARAMS}",
    # Parliament building over the Danube, Budapest
    "BUD": f"https://images.unsplash.com/photo-1761249747656-d0dadc498220{_PARAMS}",
    # Nyhavn, Copenhagen
    "CPH": f"https://images.unsplash.com/photo-1526436177729-efd2fa1172a2{_PARAMS}",
    # Ha'penny Bridge, Dublin
    "DUB": f"https://images.unsplash.com/photo-1663509851482-56ffbd1cf076{_PARAMS}",
    # Acropolis at golden hour, Athens
    "ATH": f"https://images.unsplash.com/photo-1603565816030-6b389eeb23cb{_PARAMS}",
    # Blue Mosque, Istanbul
    "IST": f"https://images.unsplash.com/photo-1552481253-e414b9976548{_PARAMS}",
    # Düden waterfalls into the sea, Antalya
    "AYT": f"https://images.unsplash.com/photo-1651468326114-10c7a93ba589{_PARAMS}",
    # Koutoubia Mosque with the snow-capped Atlas mountains, Marrakesh
    "RAK": f"https://images.unsplash.com/photo-1597212618440-806262de4f6b{_PARAMS}",

    # --- Long-haul / top holiday destinations ---
    # Burj Khalifa skyline, Dubai
    "DXB": f"https://images.unsplash.com/photo-1748626083682-611d0a66cb50{_PARAMS}",
    # Wat Arun on the Chao Phraya river, Bangkok
    "BKK": f"https://images.unsplash.com/photo-1755251042986-91270ffd76f5{_PARAMS}",
    # Longtail boat on the beach, Phuket
    "HKT": f"https://images.unsplash.com/photo-1743781509285-5a379ea1e9b2{_PARAMS}",
    # Tegallalang rice terrace, Bali
    "DPS": f"https://images.unsplash.com/photo-1557093793-d149a38a1be8{_PARAMS}",
    # Overwater bungalows, Maldives
    "MLE": f"https://images.unsplash.com/photo-1753939223042-872934ffda15{_PARAMS}",
    # Granite-rock beach, Seychelles
    "SEZ": f"https://images.unsplash.com/photo-1506405211174-1c41d7bc9ed6{_PARAMS}",
    # Manhattan skyline across the Hudson, New York (also EWR/Newark)
    "JFK": f"https://images.unsplash.com/photo-1781033966124-3539e5a3c2d1{_PARAMS}",
    "EWR": f"https://images.unsplash.com/photo-1781033966124-3539e5a3c2d1{_PARAMS}",
    # South Beach aerial, Miami
    "MIA": f"https://images.unsplash.com/photo-1530071291164-537d481750f4{_PARAMS}",
    # Santa Monica Pier, Los Angeles
    "LAX": f"https://images.unsplash.com/photo-1459258350879-34886319a3c9{_PARAMS}",
    # Hotel-zone beach, Cancún
    "CUN": f"https://images.unsplash.com/photo-1612454882173-3d6c04b82131{_PARAMS}",
    # Beach at sunrise, Punta Cana
    "PUJ": f"https://images.unsplash.com/photo-1549109768-246d46377b25{_PARAMS}",
    # Angel of Independence, Mexico City
    "MEX": f"https://images.unsplash.com/photo-1747620612231-c598ff8abc65{_PARAMS}",
    # Classic car on a colonial street, Havana
    "HAV": f"https://images.unsplash.com/photo-1752702338537-901c0b6295c6{_PARAMS}",
    # Table Mountain over Cape Town
    "CPT": f"https://images.unsplash.com/photo-1770988966522-4eea7f83dbe9{_PARAMS}",
    # Sydney Opera House at night
    "SYD": f"https://images.unsplash.com/photo-1528072164453-f4e8ef0d475a{_PARAMS}",
    # Marina Bay Sands and the Singapore Flyer at dusk
    "SIN": f"https://images.unsplash.com/photo-1774075884764-be7319c06e08{_PARAMS}",
    # Victoria Harbour skyline, Hong Kong
    "HKG": f"https://images.unsplash.com/photo-1746872269585-d85a03ef2ad2{_PARAMS}",
    # Tokyo skyline with Mount Fuji at dusk
    "TYO": f"https://images.unsplash.com/photo-1741230127615-8334deb6b463{_PARAMS}",
    # Gyeongbokgung Palace, Seoul
    "SEL": f"https://images.unsplash.com/photo-1566800890932-e89159daf3dc{_PARAMS}",
}

# Generic sunset-beach photo - deliberately not tied to any one place, so
# it never misrepresents a destination we have no real photo for.
FALLBACK_IMAGE_URL = f"https://images.unsplash.com/photo-1507525428034-b723cf961d3e{_PARAMS}"


def destination_image_url(destination: str) -> str:
    """Photo URL for `destination` (IATA code), or FALLBACK_IMAGE_URL -
    always a valid, https, Telegram-fetchable image/jpeg URL, never
    None."""
    return _DESTINATION_IMAGES.get(destination, FALLBACK_IMAGE_URL)
