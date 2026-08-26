"""Core data models shared across providers and the deal engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
