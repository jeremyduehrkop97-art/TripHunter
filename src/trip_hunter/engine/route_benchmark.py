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

EXTENSIBILITY (business/first class): get_economy_benchmark is
deliberately named for the cabin class it benchmarks - DealSignal/Deal
have no cabin_class field yet, so there is nothing to branch on today,
but a future get_business_benchmark(origin_iata, destination_iata) with
its own (much higher) regional figures could sit right next to this one
without disturbing it, and feed_radar.py's pushworthiness check would
just pick whichever benchmark function matches the fare's own cabin
class once that information exists.
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
