"""Core data models shared across providers and the deal engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class DealType(str, Enum):
    FLIGHT_DROP = "FLIGHT_DROP"
    HOTEL_DROP = "HOTEL_DROP"
    ERROR_FARE = "ERROR_FARE"
    UNUSUALLY_LOW = "UNUSUALLY_LOW"
    COMBINED_TRIP_DROP = "COMBINED_TRIP_DROP"
    # No "typical price" is known for this offer (e.g. a real search API that
    # only returns current prices, no history) - we show the offer but
    # explicitly refuse to guess whether it's cheap. See "Baseline Problem"
    # in docs/PRODUCT_SPEC.md.
    BASELINE_UNAVAILABLE = "BASELINE_UNAVAILABLE"
    # FlightOffer.price is not confirmed to represent the complete relevant
    # trip price (e.g. a round trip where only a first-step, pre-return-leg
    # price was returned) - comparing it to ANY baseline would be comparing
    # incompatible price types. See "Price Completeness" in
    # docs/PRODUCT_SPEC.md.
    PRICE_INCOMPLETE = "PRICE_INCOMPLETE"


class BaselineSource(str, Enum):
    """Where a Deal's comparison price ("expected"/baseline price) came from.

    This is deliberately separate from DealType: DealType says what kind of
    deal (if any) was found, BaselineSource says how much to trust the
    comparison behind that verdict. See "Baseline Problem" in
    docs/PRODUCT_SPEC.md.
    """

    # Our own historical price statistics for this route - either real,
    # locally observed prices (PriceHistoryRepository, see "Historical Price
    # Intelligence" in docs/PRODUCT_SPEC.md) when enough observations exist,
    # or a mock provider's hardcoded stand-in data otherwise.
    OWN_HISTORICAL_BASELINE = "OWN_HISTORICAL_BASELINE"
    # A third-party provider's own price estimate (e.g. Google Flights'
    # Price Insights via SerpApi) - useful, but not our own historical data.
    PROVIDER_PRICE_INSIGHT = "PROVIDER_PRICE_INSIGHT"
    # No comparison price of any kind is available.
    NO_BASELINE = "NO_BASELINE"


@dataclass(frozen=True)
class PriceInsight:
    """A provider-supplied price comparison for a search, independent of any
    single offer. Provider-agnostic on purpose: the deal engine reads this
    without knowing which provider (e.g. Google Flights) produced it.

    Any field may be None if the provider didn't supply it - never fill in
    a guessed value here.
    """

    # The provider's own "lowest price it's tracking" for this search - a
    # market-level figure from the provider, NOT the price of any specific
    # FlightOffer we found (those can differ - see docs/PRODUCT_SPEC.md).
    provider_lowest_price: float | None
    typical_price_low: float | None
    typical_price_high: float | None
    price_level: str | None
    source: str


class TripType(str, Enum):
    ONE_WAY = "ONE_WAY"
    ROUND_TRIP = "ROUND_TRIP"


@dataclass(frozen=True)
class FlightComparisonGroup:
    """Explicitly defines which FlightOffers are comparable to each other
    for historical price tracking - the CALLER's stated intent, never
    inferred or guessed from a list of offers (see "Explicit Comparison
    Groups" in docs/PRODUCT_SPEC.md; MVP 0.3.1 removed an earlier
    "largest group wins" heuristic that could silently pick the wrong
    travel dates out of a mixed list).

    Deliberately excludes:
    - airline: we track a single route-level market price, not a
      carrier-specific one (see "Airline" in docs/PRODUCT_SPEC.md).
    - stops: MVP tracks a single cheapest_any price, not separate
      nonstop/max-1-stop baselines (see "Stops" in docs/PRODUCT_SPEC.md).
    - cabin_class: no current provider supplies it. Once one does, it MUST
      become part of this group - Economy and Business must never share a
      baseline (see "Cabin Class" in docs/PRODUCT_SPEC.md).
    """

    origin: str
    destination: str
    departure_date: date
    return_date: date
    trip_type: TripType
    currency: str

    def __post_init__(self) -> None:
        # Our established convention (Mock/Amadeus/SerpApi normalization)
        # represents a one-way offer with return_date == departure_date.
        # Enforcing that here prevents a caller from constructing a group
        # whose trip_type contradicts its own dates - e.g. accidentally
        # matching one-way offers into what's meant to be a round-trip
        # baseline, or vice versa.
        if self.trip_type is TripType.ONE_WAY and self.return_date != self.departure_date:
            raise ValueError(
                "FlightComparisonGroup with trip_type=ONE_WAY must have "
                "return_date == departure_date (our one-way convention)."
            )
        if self.trip_type is TripType.ROUND_TRIP and self.return_date <= self.departure_date:
            raise ValueError(
                "FlightComparisonGroup with trip_type=ROUND_TRIP must have "
                "a return_date after departure_date."
            )

    def matches(self, offer: FlightOffer) -> bool:
        """Does `offer` belong to exactly this comparison group? Checks
        every field FlightOffer actually carries (origin, destination,
        departure_date, return_date, currency). `trip_type` isn't a
        FlightOffer field - the caller's own knowledge of what kind of
        search produced the offers is trusted, same as elsewhere in this
        codebase (see observation_from_flight_offer)."""
        return (
            offer.origin == self.origin
            and offer.destination == self.destination
            and offer.departure_date == self.departure_date
            and offer.return_date == self.return_date
            and offer.currency == self.currency
        )


@dataclass(frozen=True)
class PriceObservation:
    """A price we actually observed at a specific point in time.

    This represents a FACT ("we saw this price at this moment"), never an
    estimate or a guess. Provider-agnostic and independent of any specific
    search API's response schema on purpose - never store a raw provider
    response or an API-specific token (e.g. a departure_token) here.

    Semantics (MVP 0.3.1, comparison groups made explicit in MVP 0.3.2):
    one search snapshot produces AT MOST ONE PriceObservation PER EXPLICIT
    FlightComparisonGroup - the cheapest valid, complete, comparable offer
    found in that snapshot for that group. A single search can return many
    FlightOffers (9, 30, ... possibly spanning several travel-date groups);
    storing every one of them as an independent observation would bias the
    historical baseline toward whichever snapshot happened to return the
    most results, instead of tracking the market's cheapest price over
    time with each point in time weighted equally. Always build these via
    `observation_from_search_results(...)` in price_history_repository.py,
    not by looping over every offer yourself. See "Observation Semantics"
    and "Explicit Comparison Groups" in docs/PRODUCT_SPEC.md.
    """

    origin: str
    destination: str
    departure_date: date
    return_date: date
    trip_type: TripType
    price: float
    currency: str
    provider: str
    stops: int
    observed_at: datetime
    airline: str | None = None
    cabin_class: str | None = None


@dataclass(frozen=True)
class PriceStatistics:
    """Transparent summary statistics over a group of PriceObservations.
    Every field is a plain, explainable statistic - no machine learning.
    """

    observation_count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    p25: float
    p75: float
    stdev: float | None  # None when fewer than 2 observations - undefined otherwise


class HistoricalPosition(str, Enum):
    """Where a current price falls relative to our own observed history's
    interquartile range (p25-p75). Deliberately simple - not a score."""

    BELOW_HISTORY = "BELOW_HISTORY"
    WITHIN_HISTORY = "WITHIN_HISTORY"
    ABOVE_HISTORY = "ABOVE_HISTORY"


@dataclass(frozen=True)
class HistoricalBaseline:
    """Our own historical baseline for one specific route/date/currency
    group, attached to a Deal for transparency - see docs/PRODUCT_SPEC.md.
    """

    statistics: PriceStatistics
    position: HistoricalPosition
    percent_diff_from_median: float  # negative = cheaper than our median


@dataclass(frozen=True)
class FlightOffer:
    origin: str
    destination: str
    departure_date: date
    return_date: date
    price: float
    currency: str
    airline: str
    stops: int
    provider: str
    departure_time: str | None = None
    return_time: str | None = None
    booking_link: str | None = None
    # True unless a provider explicitly can't confirm `price` covers the
    # complete relevant trip (e.g. a round trip where only a first-step
    # price was returned, before selecting a return flight). Defaults to
    # True so providers without this ambiguity (mock data, Amadeus) need no
    # changes. See "Price Completeness" in docs/PRODUCT_SPEC.md.
    price_confirmed_complete: bool = True


@dataclass(frozen=True)
class AccommodationOffer:
    destination: str
    check_in: date
    check_out: date
    total_price: float
    currency: str
    name: str
    rating: float | None
    provider: str
    # The property's own listing/booking link, as returned directly by the
    # search response (e.g. SerpApi Google Hotels' `link` field) - unlike
    # FlightOffer.booking_link, this needs no second, credit-costing
    # request. None for providers that don't supply one (e.g. mock data).
    booking_link: str | None = None

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


@dataclass(frozen=True)
class AccommodationComparisonGroup:
    """Explicitly defines which AccommodationOffers are comparable to each
    other for historical price tracking - mirrors FlightComparisonGroup
    exactly (see "Explicit Comparison Groups" in docs/PRODUCT_SPEC.md).
    Never inferred or guessed from a list of offers; the caller states it.

    Deliberately strict, the same tradeoff already made for flights (see
    "Route Baseline" in docs/PRODUCT_SPEC.md): exact check_in/check_out
    only, not "same month" or "same stay length". Fewer observations will
    match per group, but it's the safest, least error-prone choice for
    now - may be loosened later.
    """

    destination: str
    check_in: date
    check_out: date
    currency: str

    def __post_init__(self) -> None:
        if self.check_out <= self.check_in:
            raise ValueError(
                "AccommodationComparisonGroup requires check_out to be after check_in."
            )

    def matches(self, offer: AccommodationOffer) -> bool:
        return (
            offer.destination == self.destination
            and offer.check_in == self.check_in
            and offer.check_out == self.check_out
            and offer.currency == self.currency
        )


@dataclass(frozen=True)
class AccommodationObservation:
    """An accommodation price we actually observed at a specific point in
    time. Mirrors PriceObservation exactly - see its docstring for the full
    rationale: one search snapshot produces AT MOST ONE
    AccommodationObservation PER EXPLICIT AccommodationComparisonGroup (the
    cheapest valid, comparable offer found in that snapshot), never one per
    offer. Always build these via
    observation_from_accommodation_search_results(...) in
    accommodation_price_history_repository.py, not by looping over every
    offer yourself.

    `name` is metadata only (which specific property was cheapest) - same
    role PriceObservation.airline plays for flights, never part of the
    comparison group. `rating` and `booking_link` are deliberately NOT
    persisted here: they are live-lookup convenience data, not durable
    "what did the market cost" facts - a rating can drift and a link can
    rot without the historical price fact having changed at all.
    """

    destination: str
    check_in: date
    check_out: date
    price: float
    currency: str
    provider: str
    observed_at: datetime
    name: str | None = None


@dataclass(frozen=True)
class Trip:
    flight: FlightOffer
    accommodation: AccommodationOffer

    @property
    def total_price(self) -> float:
        return self.flight.price + self.accommodation.total_price


@dataclass(frozen=True)
class DealScore:
    total: int
    breakdown: dict[str, float]

    def __post_init__(self) -> None:
        if not 0 <= self.total <= 100:
            raise ValueError(f"DealScore.total must be between 0 and 100, got {self.total}")


@dataclass(frozen=True)
class Deal:
    """A detected deal. `accommodation` is None for flight-only deals where no
    matching accommodation offer was found for the flight's dates.
    """

    deal_type: DealType
    flight: FlightOffer
    accommodation: AccommodationOffer | None
    expected_flight_price: float | None
    expected_accommodation_price: float | None
    score: DealScore | None
    savings_absolute: float | None
    savings_percentage: float | None
    baseline_source: BaselineSource = BaselineSource.OWN_HISTORICAL_BASELINE
    price_insight: PriceInsight | None = None
    historical_baseline: HistoricalBaseline | None = None

    @property
    def trip(self) -> Trip | None:
        if self.accommodation is None:
            return None
        return Trip(flight=self.flight, accommodation=self.accommodation)

    @property
    def actual_total_price(self) -> float:
        total = self.flight.price
        if self.accommodation is not None:
            total += self.accommodation.total_price
        return total

    @property
    def expected_total_price(self) -> float | None:
        if self.expected_flight_price is None:
            return None
        total = self.expected_flight_price
        if self.expected_accommodation_price is not None:
            total += self.expected_accommodation_price
        return total
