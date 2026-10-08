"""A realistic "normal market price" benchmark per route, and a generic
percentage-discount gate against it - the replacement for feed_radar.py's
old static per-destination-tier price caps (80/280/620 EUR). Those caps
answered "is this price absolutely low" with one fixed number per
distance class; this module instead answers "is this price LOW RELATIVE
TO what this route normally costs", which scales honestly from a 140 EUR
Mallorca weekend up through a 950 EUR Sydney economy fare, rather than
forcing every long-haul destination through one shared long-haul number.

get_economy_benchmark(origin_iata, destination_iata) -> float: this
project's OWN observed history for the exact route (any dates, see
PriceHistoryRepository.route_price_history) if there are at least
MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK (3 - deliberately lower than
engine/price_statistics.MIN_HISTORY_OBSERVATIONS' stricter 5, since this
is a coarse route-level sanity benchmark, not a rigorous per-trip
baseline) - the median of those real prices, exactly like every other
baseline in this project prefers median over mean (occasional outliers,
e.g. one mixed-in premium fare, shouldn't skew what "normal" means here
either). Otherwise a documented, reviewable REGIONAL estimate - a
destination not in any of the explicit region allowlists below (never
inferred from geography) gets the cheapest, closest-to-home tier rather
than a guessed, more expensive one, same "when in doubt, don't assume
further" convention as every other such fallback in this project (see
feed_radar.price_cap_for's old docstring, flight_price_guide_for, etc.) -
origin_iata plays no part in the regional estimate (every DACH airport is
roughly equidistant from any one far destination), only in which route's
own history is looked up.

is_deal_price(price, benchmark, min_discount_percent=30.0) -> (bool,
float): whether `price` clears `min_discount_percent` below `benchmark`,
and the actual discount percentage either way - a generic, reusable gate,
not specific to flights (deliberately no project-specific policy
constant lives here - feed_radar.py decides and names its OWN applied
threshold, exactly like it already does for MIN_HOTEL_DISCOUNT_PERCENT).

BUSINESS CLASS: get_business_benchmark(origin_iata, destination_iata) ->
float is get_economy_benchmark's Business-Class counterpart, exactly the
extension this module's docstring originally predicted - its own, much
higher regional figures (see BUSINESS_*_BENCHMARK_EUR below), always the
regional estimate (no own-history branch: no provider populates
PriceObservation.cabin_class yet - see price_history_repository.py's own
comment on that field - so there is no genuinely Business-Class-only
history to take a median of; mixing in ordinary Economy rows under a
Business label would be exactly the kind of silent fabrication this
project never does). get_route_benchmark(origin_iata, destination_iata,
cabin_class="economy") is the single entry point feed_radar.py actually
calls: it dispatches to get_business_benchmark for "business", and -
since this task named no separate First-Class or Premium-Economy figures
- reuses get_business_benchmark for "first" and get_economy_benchmark for
"premium_economy" as a deliberately CONSERVATIVE floor in both cases
(a real First-Class fare realistically costs even more than Business, and
a real Premium-Economy fare more than plain Economy, so judging either
against the next tier down's benchmark only makes the discount gate
STRICTER, never more permissive - the same "when in doubt, don't assume
further" convention as every other fallback in this module, just applied
to a cabin class instead of a destination).
"""

from __future__ import annotations

from trip_hunter.engine.flexible_dates import MID_HAUL_DESTINATIONS
from trip_hunter.engine.price_statistics import compute_statistics
from trip_hunter.price_history_repository import PriceHistoryRepository

# Deliberately lower than engine/price_statistics.MIN_HISTORY_OBSERVATIONS
# (5) - see module docstring for why a route-level benchmark can afford a
# smaller bar than a rigorous per-trip baseline.
MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK = 3

# EUR, one adult, round trip, economy - a documented, reviewable REGIONAL
# estimate (never a specific invented price for one city), used only when
# this project has no real history for the exact route yet.
EUROPE_SHORT_HAUL_BENCHMARK_EUR = 140.0
MIDHAUL_MENA_BENCHMARK_EUR = 420.0  # Nordafrika/Nahost/Kanaren
TRANSATLANTIC_BENCHMARK_EUR = 530.0  # Nordamerika/Karibik
FAR_EAST_SEA_BENCHMARK_EUR = 820.0  # Fernost/Südostasien/Südasien/Indischer Ozean
OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR = 950.0  # Ozeanien/Südamerika/südliches & östliches Afrika

# Reuses engine/flexible_dates.py's existing, already-curated Gulf/Orient/
# Canary-Islands/North-Africa allowlist directly - this project's own
# established "Mittelstrecke" definition, not a second, independently
# maintained copy of the same idea.
_MIDHAUL_MENA_DESTINATIONS = MID_HAUL_DESTINATIONS

_TRANSATLANTIC_DESTINATIONS: frozenset[str] = frozenset(
    {"JFK", "EWR", "MIA", "LAX", "SFO", "BOS", "CHI", "YYZ", "YYC", "CUN", "PUJ", "MEX", "HAV"}
)

_FAR_EAST_SEA_DESTINATIONS: frozenset[str] = frozenset(
    {
        "HKT", "DPS", "BKK", "KBV", "CNX", "SIN", "HKG", "TPE", "SAI", "KTI",
        "MLE", "SEZ", "TYO", "SEL", "DEL", "BOM", "KTM", "CMB",
    }
)

# Named "oceania_samerica" after the task's own two example regions;
# Southern/East Africa (CPT/NBO/ZNZ) has no closer-fitting one of the 5
# named tiers (clearly not Europe/MENA/transatlantic/Far East) and is a
# comparably distant, premium long-haul fare from DACH, so it's grouped
# here rather than invented a 6th tier nobody asked for - a deliberate,
# documented judgement call, not a geographic claim.
_OCEANIA_SOUTH_AMERICA_DESTINATIONS: frozenset[str] = frozenset(
    {"SYD", "CPT", "NBO", "ZNZ", "GRU", "EZE", "BOG", "SCL", "LIM"}
)


def _regional_benchmark(destination_iata: str | None) -> float:
    if destination_iata in _MIDHAUL_MENA_DESTINATIONS:
        return MIDHAUL_MENA_BENCHMARK_EUR
    if destination_iata in _TRANSATLANTIC_DESTINATIONS:
        return TRANSATLANTIC_BENCHMARK_EUR
    if destination_iata in _FAR_EAST_SEA_DESTINATIONS:
        return FAR_EAST_SEA_BENCHMARK_EUR
    if destination_iata in _OCEANIA_SOUTH_AMERICA_DESTINATIONS:
        return OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR
    return EUROPE_SHORT_HAUL_BENCHMARK_EUR


def get_economy_benchmark(
    origin_iata: str, destination_iata: str, *, repo: PriceHistoryRepository | None = None
) -> float:
    """A realistic round-trip, one-adult, economy "normal price" in EUR
    for `origin_iata` -> `destination_iata` - this project's own real
    price history for the route if there's enough of it
    (MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK, median), else a documented
    regional estimate (see module docstring). `repo` is injectable for
    tests; defaults to the real PriceHistoryRepository (default db path)."""
    resolved_repo = repo if repo is not None else PriceHistoryRepository()
    prices = resolved_repo.route_price_history(origin_iata, destination_iata)
    if len(prices) >= MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK:
        return compute_statistics(prices).median
    return _regional_benchmark(destination_iata)


# EUR, one adult, round trip, BUSINESS class - this task's own named
# figures, documented regional estimates exactly like the Economy ones
# above (never a specific invented price for one city).
BUSINESS_EUROPE_SHORT_HAUL_BENCHMARK_EUR = 350.0
BUSINESS_MIDHAUL_MENA_BENCHMARK_EUR = 950.0
BUSINESS_TRANSATLANTIC_BENCHMARK_EUR = 1850.0  # Nordamerika/Karibik
BUSINESS_FAR_EAST_SEA_BENCHMARK_EUR = 2200.0  # Fernost/Südostasien/Südasien/Indischer Ozean
BUSINESS_OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR = 2600.0  # Ozeanien/Südamerika/südliches & östliches Afrika


def _regional_business_benchmark(destination_iata: str | None) -> float:
    """Business-Class counterpart of _regional_benchmark - same region
    allowlists, this tier's own (much higher) figures."""
    if destination_iata in _MIDHAUL_MENA_DESTINATIONS:
        return BUSINESS_MIDHAUL_MENA_BENCHMARK_EUR
    if destination_iata in _TRANSATLANTIC_DESTINATIONS:
        return BUSINESS_TRANSATLANTIC_BENCHMARK_EUR
    if destination_iata in _FAR_EAST_SEA_DESTINATIONS:
        return BUSINESS_FAR_EAST_SEA_BENCHMARK_EUR
    if destination_iata in _OCEANIA_SOUTH_AMERICA_DESTINATIONS:
        return BUSINESS_OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR
    return BUSINESS_EUROPE_SHORT_HAUL_BENCHMARK_EUR


def get_business_benchmark(
    origin_iata: str, destination_iata: str, *, repo: PriceHistoryRepository | None = None
) -> float:
    """A realistic round-trip, one-adult, BUSINESS-class "normal price" in
    EUR for `origin_iata` -> `destination_iata` - always the documented
    regional estimate (see module docstring's "BUSINESS CLASS" section for
    why this one has no own-history branch unlike get_economy_benchmark).
    `origin_iata` and `repo` are accepted only to keep an identical call
    shape to get_economy_benchmark (and for the same future own-history
    extension once a provider populates cabin_class) - neither is used
    today."""
    del origin_iata, repo  # unused today - see docstring
    return _regional_business_benchmark(destination_iata)


def get_route_benchmark(
    origin_iata: str, destination_iata: str, *, cabin_class: str = "economy",
    repo: PriceHistoryRepository | None = None,
) -> float:
    """The single entry point feed_radar.py actually calls: the benchmark
    for `origin_iata` -> `destination_iata` in `cabin_class`'s own terms -
    "business" -> get_business_benchmark; "first" and "premium_economy"
    reuse the next tier down as a deliberately conservative floor (no
    separate figures were ever named for either - see module docstring);
    "economy" (default, and anything else unrecognised) ->
    get_economy_benchmark."""
    if cabin_class in ("business", "first"):
        return get_business_benchmark(origin_iata, destination_iata, repo=repo)
    return get_economy_benchmark(origin_iata, destination_iata, repo=repo)


def is_deal_price(price: float, benchmark: float, min_discount_percent: float = 30.0) -> tuple[bool, float]:
    """Whether `price` clears `min_discount_percent` below `benchmark`,
    and the actual discount percentage (can be negative - `price` above
    `benchmark` - or exceed 100 only if `price` is negative, which never
    happens for a real fare). `benchmark <= 0` can never produce a deal
    (nothing to honestly compute a percentage off of)."""
    if benchmark <= 0:
        return False, 0.0
    discount_percent = (benchmark - price) / benchmark * 100
    return discount_percent >= min_discount_percent, discount_percent
