"""IATA code -> German city name + country flag, for the alert header
("🇪🇸 Hamburg nach Palma de Mallorca").

Same explicit-mapping pattern as destination_context.py: hand-written
for the airports this project actually deals with (the five German
origins, the sampling/rotation destinations, and the destinations the
feed sensor can name - see engine/feed_sensor.py's _CITY_TO_IATA). A code
not listed keeps its raw IATA code and a neutral ✈️ instead of a guessed
city or flag.
"""

from __future__ import annotations

_UNKNOWN_FLAG = "✈️"

# code: (German city name, flag of the country the airport is in)
_AIRPORTS: dict[str, tuple[str, str]] = {
    # German origins
    "HAM": ("Hamburg", "🇩🇪"),
    "BER": ("Berlin", "🇩🇪"),
    "FRA": ("Frankfurt", "🇩🇪"),
    "MUC": ("München", "🇩🇪"),
    "DUS": ("Düsseldorf", "🇩🇪"),
    # Sampling / rotation destinations
    "PMI": ("Palma de Mallorca", "🇪🇸"),
    "BCN": ("Barcelona", "🇪🇸"),
    "FCO": ("Rom", "🇮🇹"),
    "LIS": ("Lissabon", "🇵🇹"),
    "BGY": ("Mailand (Bergamo)", "🇮🇹"),
    "VCE": ("Venedig", "🇮🇹"),
    "VIE": ("Wien", "🇦🇹"),
    "STN": ("London", "🇬🇧"),
    "FAO": ("Faro (Algarve)", "🇵🇹"),
    "OPO": ("Porto", "🇵🇹"),
    # Further destinations the feed sensor can name
    "MAD": ("Madrid", "🇪🇸"),
    "AGP": ("Málaga", "🇪🇸"),
    "SVQ": ("Sevilla", "🇪🇸"),
    "VLC": ("Valencia", "🇪🇸"),
    "IBZ": ("Ibiza", "🇪🇸"),
    "TFS": ("Teneriffa", "🇪🇸"),
    "LPA": ("Gran Canaria", "🇪🇸"),
    "FUE": ("Fuerteventura", "🇪🇸"),
    "ACE": ("Lanzarote", "🇪🇸"),
    "CDG": ("Paris", "🇫🇷"),
    "NCE": ("Nizza", "🇫🇷"),
    "LON": ("London", "🇬🇧"),
    "LHR": ("London", "🇬🇧"),
    "AMS": ("Amsterdam", "🇳🇱"),
    "MXP": ("Mailand", "🇮🇹"),
    "DUB": ("Dublin", "🇮🇪"),
    "CPH": ("Kopenhagen", "🇩🇰"),
    "PRG": ("Prag", "🇨🇿"),
    "BUD": ("Budapest", "🇭🇺"),
    "ATH": ("Athen", "🇬🇷"),
    "HER": ("Kreta (Heraklion)", "🇬🇷"),
    "RHO": ("Rhodos", "🇬🇷"),
    "IST": ("Istanbul", "🇹🇷"),
    "AYT": ("Antalya", "🇹🇷"),
    "CAI": ("Kairo", "🇪🇬"),
    "RAK": ("Marrakesch", "🇲🇦"),
    "DXB": ("Dubai", "🇦🇪"),
    "BKK": ("Bangkok", "🇹🇭"),
    "JFK": ("New York", "🇺🇸"),
}


def city_name(code: str) -> str:
    """German city name for `code`, or the code itself if unknown."""
    return _AIRPORTS.get(code, (code, _UNKNOWN_FLAG))[0]


def flag_emoji(code: str) -> str:
    """Flag of the airport's country, or ✈️ if the code isn't mapped."""
    return _AIRPORTS.get(code, (code, _UNKNOWN_FLAG))[1]
