"""Orchestrates the full pipeline: flights -> flight deals -> matching
accommodation -> hotel deals -> combined trip -> score -> Deal.

See docs/ARCHITECTURE.md for a step-by-step diagram of this flow.
"""

from __future__ import annotations

from datetime import date

from vacation_hunter.engine.flight_deal_detector import assess_flight
from vacation_hunter.engine.hotel_deal_detector import HotelDealAssessment, assess_accommodation
from vacation_hunter.engine.scoring import score_trip
from vacation_hunter.engine.trip_combiner import combine
from vacation_hunter.models import AccommodationOffer, Deal, DealType, FlightOffer
from vacation_hunter.providers.accommodation_provider import AccommodationProvider
from vacation_hunter.providers.flight_provider import FlightProvider

# A flight deal is only upgraded to the flagship COMBINED_TRIP_DROP if the
# combined trip clears both bars: a small percentage saving on an already
# cheap trip isn't interesting, and a big percentage saving on a tiny
# amount of money isn't either. See docs/PRODUCT_SPEC.md.
COMBINED_TRIP_DROP_PCT_THRESHOLD = 0.30
COMBINED_TRIP_DROP_MIN_ABSOLUTE_SAVINGS = 100.0


class DealEngine:
    def __init__(
        self, flight_provider: FlightProvider, accommodation_provider: AccommodationProvider
    ) -> None:
        self._flight_provider = flight_provider
        self._accommodation_provider = accommodation_provider

    def find_trip_deals(
        self,
        origin: str,
        destination: str,
        earliest_departure: date,
        latest_departure: date,
        return_date: date | None = None,
    ) -> list[Deal]:
        flight_offers = self._flight_provider.search_flights(
            origin, destination, earliest_departure, latest_departure, return_date=return_date
        )
        deals = (self._evaluate_flight(flight) for flight in flight_offers)
        return [deal for deal in deals if deal is not None]

    def _evaluate_flight(self, flight: FlightOffer) -> Deal | None:
        typical_flight_price = self._flight_provider.get_typical_price(
            flight.origin, flight.destination, flight.departure_date.month
        )
        flight_assessment = assess_flight(flight, typical_flight_price)
        if flight_assessment.deal_type is None:
            return None

        if flight_assessment.deal_type is DealType.BASELINE_UNAVAILABLE:
            # We genuinely don't know if this price is good. Show the flight,
            # but never fabricate a baseline just to produce a verdict.
            return Deal(
                deal_type=DealType.BASELINE_UNAVAILABLE,
                flight=flight,
                accommodation=None,
                expected_flight_price=None,
                expected_accommodation_price=None,
                score=None,
                savings_absolute=None,
                savings_percentage=None,
            )

        deal_type = flight_assessment.deal_type
        savings_absolute = flight_assessment.savings_absolute
        savings_percentage = flight_assessment.savings_percentage
        hotel_savings_percentage = 0.0

        accommodation, expected_accommodation_price, hotel_assessment = (
            self._best_accommodation_for(flight)
        )

        if accommodation is not None and expected_accommodation_price is not None:
            combined = combine(
                flight, accommodation, typical_flight_price, expected_accommodation_price
            )
            hotel_savings_percentage = hotel_assessment.savings_percentage
            savings_absolute = combined.savings_absolute
            savings_percentage = combined.savings_percentage

            if (
                combined.savings_percentage >= COMBINED_TRIP_DROP_PCT_THRESHOLD
                and combined.savings_absolute >= COMBINED_TRIP_DROP_MIN_ABSOLUTE_SAVINGS
            ):
                deal_type = DealType.COMBINED_TRIP_DROP

        score = score_trip(
            trip_savings_percentage=savings_percentage,
            flight_savings_percentage=flight_assessment.savings_percentage,
            hotel_savings_percentage=hotel_savings_percentage,
            savings_absolute=savings_absolute,
            flight_stops=flight.stops,
        )

        return Deal(
            deal_type=deal_type,
            flight=flight,
            accommodation=accommodation,
            expected_flight_price=typical_flight_price,
            expected_accommodation_price=expected_accommodation_price,
            score=score,
            savings_absolute=round(savings_absolute, 2),
            savings_percentage=round(savings_percentage, 4),
        )

    def _best_accommodation_for(
        self, flight: FlightOffer
    ) -> tuple[AccommodationOffer | None, float | None, HotelDealAssessment | None]:
        offers = self._accommodation_provider.search_accommodations(
            flight.destination, flight.departure_date, flight.return_date
        )
        if not offers:
            return None, None, None

        cheapest = min(offers, key=lambda offer: offer.total_price)
        nights = (flight.return_date - flight.departure_date).days
        typical_price = self._accommodation_provider.get_typical_total_price(
            flight.destination, nights, flight.departure_date.month
        )
        assessment = assess_accommodation(cheapest, typical_price)
        return cheapest, typical_price, assessment
