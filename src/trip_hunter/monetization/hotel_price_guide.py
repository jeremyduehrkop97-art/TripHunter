"""A hotel price GUIDE (Richtpreis) for feed-radar signals: a
representative nightly EUR rate for a mid-range/4-star hotel room (2
guests, matching instant_alert_formatter.HOTEL_GUESTS), used only to build
the illustrative flight+hotel combo total in the "Urlaubspiraten model"
flexible-date teaser (see instant_alert_formatter.signal_combo_lines).

WHAT THIS IS NOT: a live quote, a specific hotel, or a SerpApi-verified
price. A feed-radar signal has no real accommodation data at all (unlike
the daily sampler's Deals, which DO carry a real, SerpApi-priced
Accommodation) - and running a real Google Hotels search for every
flexible signal x 4 example windows would blow through this project's
fixed 250-searches/month SerpApi budget in days (the feed radar runs
hourly specifically BECAUSE it spends zero SerpApi credits - see
feed_radar.py's module docstring). So instead of fabricating a fake
"live" price or silently skipping the combo teaser for every flexible
signal, this is an openly documented, reviewable ESTIMATE grouped by
destination price TIER (not hand-tuned per city, which would look more
precise than it actually is) - always shown to the end user labelled
"Richtwert"/"ca.", never as a confirmed booking price. A destination with
no tier here simply gets no combo teaser at all (instant_alert_formatter
falls back to the plain single-price signal layout) - never a guessed
number for a place we have no basis to estimate.

To add a destination: pick the closest matching tier below by general
price level (a well-known, publicly documented fact about a place - "a
mid-range hotel in Bangkok costs less than one in Zurich" - not a
specific invented number), or add a new tier if none fits.
"""

from __future__ import annotations

# EUR/night for a mid-range (~4-star) hotel room for 2 - a deliberately
# coarse, documented estimate per PRICE TIER (not per city), so a
# wrong-looking number is easy to spot and the precision never overstates
# what this actually is: a rough guide, not a quote.
_TIER_NIGHTLY_EUR: dict[str, int] = {
    "eu_capital": 115,          # Western/Northern European capitals & major cities
    "eu_budget_city": 75,       # Central/Southeastern European cities
    "eu_beach": 80,             # Mediterranean/European beach & island resorts
    "canary_madeira": 85,       # Canary Islands / Madeira
    "middle_east_gulf": 140,    # Dubai, Doha, Abu Dhabi
    "africa": 90,               # Cairo, Marrakesh, Cape Town
    "sea_asia_value": 45,       # Thailand, Bali, Vietnam etc. - strong value for money
    "south_asia_value": 50,     # India - strong value for money
    "east_asia": 105,           # Tokyo, Seoul, Hong Kong, Singapore, Taipei
    "indian_ocean_resort": 150, # Maldives, Seychelles - resort-heavy destinations
    "caribbean_mexico": 120,    # Cancún, Punta Cana, Mexico City, Havana
    "north_america": 165,       # major US/Canada cities
    "south_america": 85,        # Bogotá, Lima, Santiago, São Paulo, Buenos Aires
    "oceania": 135,             # Sydney, Melbourne, Auckland
}

# Explicit IATA -> tier allowlist, never inferred from geography (same
# pattern as error_fare_floor.py's MID_HAUL_DESTINATIONS). A code not
# listed here has no guide price - see module docstring.
_DESTINATION_TIER: dict[str, str] = {
    # Western/Northern Europe
    "CDG": "eu_capital", "AMS": "eu_capital", "LON": "eu_capital", "LHR": "eu_capital",
    "STN": "eu_capital", "MAD": "eu_capital", "VIE": "eu_capital", "DUB": "eu_capital",
    "CPH": "eu_capital", "FCO": "eu_capital", "MXP": "eu_capital", "BGY": "eu_capital",
    "ZRH": "eu_capital", "GVA": "eu_capital", "BSL": "eu_capital", "SZG": "eu_capital",
    "INN": "eu_capital", "LIS": "eu_capital", "OPO": "eu_capital", "BCN": "eu_capital",
    # Central/Southeastern Europe
    "PRG": "eu_budget_city", "BUD": "eu_budget_city", "IST": "eu_budget_city",
    # Mediterranean / European beach & island
    "PMI": "eu_beach", "IBZ": "eu_beach", "FAO": "eu_beach", "NCE": "eu_beach",
    "HER": "eu_beach", "RHO": "eu_beach", "VLC": "eu_beach", "AGP": "eu_beach",
    "SVQ": "eu_beach", "VCE": "eu_beach", "ATH": "eu_beach", "AYT": "eu_beach",
    # Canary Islands / Madeira
    "TFS": "canary_madeira", "LPA": "canary_madeira", "FUE": "canary_madeira", "ACE": "canary_madeira",
    # Middle East / Gulf
    "DXB": "middle_east_gulf",
    # Africa
    "CAI": "africa", "RAK": "africa", "CPT": "africa",
    # Southeast Asia
    "BKK": "sea_asia_value", "HKT": "sea_asia_value", "DPS": "sea_asia_value",
    "CNX": "sea_asia_value", "KBV": "sea_asia_value",
    # East Asia
    "SIN": "east_asia", "HKG": "east_asia", "TYO": "east_asia", "SEL": "east_asia", "TPE": "east_asia",
    # Indian Ocean resort destinations
    "MLE": "indian_ocean_resort", "SEZ": "indian_ocean_resort",
    # Caribbean / Mexico / Cuba
    "CUN": "caribbean_mexico", "PUJ": "caribbean_mexico", "MEX": "caribbean_mexico", "HAV": "caribbean_mexico",
    # North America
    "JFK": "north_america", "EWR": "north_america", "MIA": "north_america", "LAX": "north_america",
    "SFO": "north_america", "BOS": "north_america", "CHI": "north_america", "YYZ": "north_america",
    "YYC": "north_america",
    # South Asia
    "DEL": "south_asia_value", "BOM": "south_asia_value",
    # South America
    "GRU": "south_america", "EZE": "south_america", "BOG": "south_america",
    "SCL": "south_america", "LIM": "south_america",
    # Oceania
    "SYD": "oceania",
}


def hotel_nightly_guide_price(destination_iata: str | None) -> int | None:
    """EUR/night guide price for a mid-range hotel room (2 guests) at
    `destination_iata`, or None if the destination isn't in the explicit
    allowlist above - never a guessed number for an uncovered place."""
    if not destination_iata:
        return None
    tier = _DESTINATION_TIER.get(destination_iata)
    return _TIER_NIGHTLY_EUR.get(tier) if tier else None
