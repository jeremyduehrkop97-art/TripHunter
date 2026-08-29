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
class PriceObservation:
    """A price we actually observed at a specific point in time.

    This represents a FACT ("we saw this price at this moment"), never an
    estimate or a guess. Provider-agnostic and independent of any specific
    search API's response schema on purpose - never store a raw provider
    response or an API-specific token (e.g. a departure_token) here.

    Semantics (MVP 0.3.1): one search snapshot produces AT MOST ONE
    PriceObservation - the cheapest valid, complete, comparable offer found
    in that snapshot. A single search can return many FlightOffers (9, 30,
    ...); storing every one of them as an independent observation would
    bias the historical baseline toward whichever snapshot happened to
    return the most results, instead of tracking the market's cheapest
    price over time with each point in time weighted equally. Always build
    these via `observation_from_search_results(...)` in
    price_history_repository.py, not by looping over every offer yourself.
    See "Observation Semantics" in docs/PRODUCT_SPEC.md.
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

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


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
