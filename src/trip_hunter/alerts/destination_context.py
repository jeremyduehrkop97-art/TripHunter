"""Short, atmospheric destination blurbs for Telegram alerts.

A plain bulleted price breakdown tells you WHAT the deal is; this adds a
couple of sentences of WHY the destination itself is worth going for -
shared by both instant_alert_formatter.py's VIP (format_instant_alert)
and Free (format_teaser_alert) messages via _alert_body_lines(), so
neither channel can drift from the other on tone.

Deliberately a small, explicit IATA-code -> text mapping, not a
generated/templated description - same pattern as
serpapi_accommodation_provider.py's _DESTINATION_QUERY and
error_fare_floor.py's MID_HAUL_DESTINATIONS: hand-written, reviewable
copy for the destinations this project actually samples (see
sampling_targets.py), with a honest, generic fallback for anything not
yet covered - never a guessed or generated description of a place this
project has no real content for.
"""

from __future__ import annotations

_DESTINATION_CONTEXT: dict[str, str] = {
    "PMI": (
        "Perfekt für eine spontane Auszeit: Ob Strandspaziergänge, Tapas in der "
        "Altstadt von Palma oder milde Sonnenstunden abseits der Hauptsaison – "
        "Palma bietet mediterranes Feeling bei nur gut zwei Stunden Flugzeit."
    ),
    "BCN": (
        "Barcelona verbindet Strand und Stadt wie kaum ein anderes Ziel: "
        "Gaudís Architektur am Vormittag, Tapas-Bars in El Born am Abend, "
        "und dazwischen jederzeit ein Sprung ans Mittelmeer."
    ),
    "FCO": (
        "Rom lohnt sich zu jeder Jahreszeit: Antike auf Schritt und Tritt, "
        "Espresso an der Bar statt im Sitzen, und abends eine Trattoria in "
        "einer der unzähligen Seitengassen abseits der Touristenpfade."
    ),
    "LIS": (
        "Lissabon überrascht mit seinen Hügeln, knarrenden Straßenbahnen und "
        "Fado-Klängen in den Gassen von Alfama – dazu Pastéis de Nata an "
        "praktisch jeder Ecke und Tagesausflüge an den Atlantik nach Cascais."
    ),
    "BGY": (
        "Mailand liegt vom Flughafen Bergamo aus nur eine Busfahrt entfernt: "
        "Dom und Galleria am Vormittag, Aperitivo an den Navigli am Abend – "
        "und mit dem Zug sind Comer See oder Bergamos Altstadt schnell erreicht."
    ),
    "VCE": (
        "Venedig ist ein Ziel wie aus einer anderen Zeit: Kanäle statt Straßen, "
        "Cicchetti-Bars in den Seitengassen und eine Lagune, die man am besten "
        "früh morgens erlebt, bevor die Tagesgäste kommen."
    ),
    "VIE": (
        "Wien ist Kaffeehauskultur, Kaiserpracht und Musik in einer "
        "Stadt: ein Stück Sachertorte im Traditionscafé, ein Spaziergang "
        "um die Ringstraße und abends ein Heuriger am Stadtrand."
    ),
    "STN": (
        "London ist ein Klassiker für den Kurztrip: Museen mit freiem Eintritt, "
        "Märkte wie Borough und Camden, Spaziergänge an der Themse – "
        "und der Stansted Express bringt dich in unter einer Stunde ins Zentrum."
    ),
    "FAO": (
        "Faro ist das Tor zur Algarve: goldene Felsküsten, ruhige Lagunen im "
        "Ria-Formosa-Naturpark und eine kleine Altstadt, in der abends "
        "frischer Fisch auf den Tisch kommt."
    ),
    "OPO": (
        "Porto besticht mit bunten Fassaden am Douro, Azulejo-Bahnhof und "
        "Portwein-Kellern am anderen Ufer – eine Stadt, die man wunderbar "
        "zu Fuß und mit einem Pastel de Nata in der Hand entdeckt."
    ),
}

# Never claims local knowledge it doesn't have - deliberately generic
# rather than inventing specifics for a destination not yet in the
# mapping above.
_FALLBACK_CONTEXT = (
    "Ein Ziel, das einen genaueren Blick wert ist – die Zahlen unten "
    "sprechen für sich."
)


def destination_context(destination: str) -> str:
    """A short (2-3 sentence) atmospheric blurb for `destination` (IATA
    code), or a neutral fallback if this destination has no hand-written
    entry yet."""
    return _DESTINATION_CONTEXT.get(destination, _FALLBACK_CONTEXT)
