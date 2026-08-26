"""Combines a flight offer and an accommodation offer into a single Trip."""

from __future__ import annotations

from dataclasses import dataclass

from vacation_hunter.models import AccommodationOffer, FlightOffer, Trip


@dataclass(frozen=True)
class CombinedTripAssessment:
    trip: Trip
    expected_total_price: float
    savings_absolute: float
    savings_percentage: float


def combine(
    flight: FlightOffer,
    accommodation: AccommodationOffer,
    expected_flight_price: float,
    expected_accommodation_price: float,
) -> CombinedTripAssessment:
    trip = Trip(flight=flight, accommodation=accommodation)
    expected_total_price = expected_flight_price + expected_accommodation_price
    savings_absolute = expected_total_price - trip.total_price
    savings_percentage = (
        savings_absolute / expected_total_price if expected_total_price > 0 else 0.0
    )
    return CombinedTripAssessment(
        trip=trip,
        expected_total_price=expected_total_price,
        savings_absolute=savings_absolute,
        savings_percentage=savings_percentage,
    )
