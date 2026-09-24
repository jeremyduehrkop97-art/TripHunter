"""Orchestrates the full pipeline: flights -> flight deals -> matching
accommodation -> hotel deals -> combined trip -> score -> Deal.

See docs/ARCHITECTURE.md for a step-by-step diagram of this flow.
"""

from __future__ import annotations

from datetime import date

from trip_hunter.accommodation_price_history_repository import AccommodationPriceHistoryRepository
from trip_hunter.engine.error_fare_floor import FLOOR_TRIGGER_SCORE, error_fare_floor_triggered
from trip_hunter.engine.flight_deal_detector import assess_flight, assess_flight_price_insight
from trip_hunter.engine.hotel_deal_detector import HotelDealAssessment, assess_accommodation
from trip_hunter.engine.price_statistics import (
    get_accommodation_historical_baseline,
    get_historical_baseline,
)
from trip_hunter.engine.quality_gate import passes_quality_gate
from trip_hunter.engine.scoring import score_trip
from trip_hunter.engine.trip_combiner import combine
from trip_hunter.models import (
    AccommodationOffer,
    BaselineSource,
    Deal,
    DealScore,
    DealType,
    FlightOffer,
    HistoricalBaseline,
    PriceInsight,
    TripType,
)
from trip_hunter.price_history_repository import PriceHistoryRepository
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider

# A flight deal is only upgraded to the flagship COMBINED_TRIP_DROP if the
# combined trip clears both bars: a small percentage saving on an already
# cheap trip isn't interesting, and a big percentage saving on a tiny
# amount of money isn't either. See docs/PRODUCT_SPEC.md.
COMBINED_TRIP_DROP_PCT_THRESHOLD = 0.30
COMBINED_TRIP_DROP_MIN_ABSOLUTE_SAVINGS = 100.0


class DealEngine:
    def __init__(
        self,
        flight_provider: FlightProvider,
        accommodation_provider: AccommodationProvider,
        price_history_repository: PriceHistoryRepository | None = None,
        accommodation_price_history_repository: AccommodationPriceHistoryRepository | None = None,
    ) -> None:
        self._flight_provider = flight_provider
        self._accommodation_provider = accommodation_provider
        self._price_history_repository = price_history_repository
        self._accommodation_price_history_repository = accommodation_price_history_repository

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
        trip_type = TripType.ROUND_TRIP if return_date is not None else TripType.ONE_WAY
        deals = (self._evaluate_flight(flight, trip_type) for flight in flight_offers)
        return [deal for deal in deals if deal is not None]

    def _evaluate_flight(self, flight: FlightOffer, trip_type: TripType) -> Deal | None:
        if not flight.price_confirmed_complete:
            # We are not sure flight.price covers the complete relevant trip
            # (e.g. an unconfirmed round-trip price). Comparing it against
            # ANY baseline - our own or a provider's - would compare
            # incompatible price types, so we stop before even looking one
            # up. This precedes every baseline mechanism, including our own
            # historical data below. See "Price Completeness" in
            # docs/PRODUCT_SPEC.md.
            return Deal(
                deal_type=DealType.PRICE_INCOMPLETE,
                flight=flight,
                accommodation=None,
                expected_flight_price=None,
                expected_accommodation_price=None,
                score=None,
                savings_absolute=None,
                savings_percentage=None,
                baseline_source=BaselineSource.NO_BASELINE,
                price_insight=None,
                historical_baseline=None,
            )

        # Baseline priority: our own real observed history first (if we
        # have enough of it), then a provider's own "typical price" concept
        # (mock data today; real APIs return None here), then a provider
        # price insight further below, then nothing. See "Deal Engine
        # Integration" in docs/PRODUCT_SPEC.md.
        historical_baseline: HistoricalBaseline | None = None
        if self._price_history_repository is not None:
            historical_baseline = get_historical_baseline(
                self._price_history_repository,
                flight.origin,
                flight.destination,
                flight.departure_date,
                flight.return_date,
                trip_type,
                flight.currency,
                flight.price,
            )

        if historical_baseline is not None:
            typical_flight_price = historical_baseline.statistics.median
        else:
            typical_flight_price = self._flight_provider.get_typical_price(
                flight.origin, flight.destination, flight.departure_date.month
            )

        flight_assessment = assess_flight(flight, typical_flight_price)
        if flight_assessment.deal_type is None:
            # Our own historical baseline explicitly says: not interesting.
            return None

        baseline_source = BaselineSource.OWN_HISTORICAL_BASELINE
        price_insight: PriceInsight | None = None

        if flight_assessment.deal_type is DealType.BASELINE_UNAVAILABLE:
            # No historical baseline of our own. Fall back to a
            # provider-supplied price insight (e.g. Google Flights via
            # SerpApi) if the provider has one - still never fabricated.
            price_insight = self._flight_provider.get_price_insight(
                flight.origin, flight.destination, flight.departure_date, flight.return_date
            )
            insight_assessment = assess_flight_price_insight(flight, price_insight)

            if insight_assessment is not None:
                baseline_source = BaselineSource.PROVIDER_PRICE_INSIGHT
                typical_flight_price = flight.price + insight_assessment.savings_absolute
                if insight_assessment.deal_type is not None:
                    flight_assessment = insight_assessment
                # else: the insight exists but doesn't show a notable saving.
                # We keep deal_type as BASELINE_UNAVAILABLE below, but still
                # attach the insight/typical price for a transparent result
                # instead of hiding what we actually compared against.
            else:
                baseline_source = BaselineSource.NO_BASELINE

            if flight_assessment.deal_type is DealType.BASELINE_UNAVAILABLE:
                # Last resort before giving up entirely: neither our own
                # history nor a provider price insight gave us anything to
                # compare against - but an absurdly cheap absolute price
                # (a "someone fat-fingered a fare" price) is still worth
                # catching on day one, not just once 5 observations exist.
                # See engine/error_fare_floor.py for the full reasoning;
                # this never overrides a real baseline - only fires when
                # every real baseline path has already come up empty.
                accommodation, _, _ = self._best_accommodation_for(flight)
                if error_fare_floor_triggered(flight):
                    return Deal(
                        deal_type=DealType.ERROR_FARE,
                        flight=flight,
                        accommodation=accommodation,
                        expected_flight_price=None,
                        expected_accommodation_price=None,
                        score=DealScore(
                            total=FLOOR_TRIGGER_SCORE,
                            breakdown={"absolute_floor_trigger": float(FLOOR_TRIGGER_SCORE)},
                        ),
                        savings_absolute=None,
                        savings_percentage=None,
                        baseline_source=BaselineSource.ABSOLUTE_FLOOR_TRIGGER,
                        price_insight=None,
                        historical_baseline=None,
                    )

                has_insight_numbers = baseline_source is BaselineSource.PROVIDER_PRICE_INSIGHT
                return Deal(
                    deal_type=DealType.BASELINE_UNAVAILABLE,
                    flight=flight,
                    accommodation=None,
                    expected_flight_price=typical_flight_price if has_insight_numbers else None,
                    expected_accommodation_price=None,
                    score=None,
                    savings_absolute=insight_assessment.savings_absolute if has_insight_numbers else None,
                    savings_percentage=(
                        insight_assessment.savings_percentage if has_insight_numbers else None
                    ),
                    baseline_source=baseline_source,
                    price_insight=price_insight,
                    historical_baseline=None,
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

        deal = Deal(
            deal_type=deal_type,
            flight=flight,
            accommodation=accommodation,
            expected_flight_price=typical_flight_price,
            expected_accommodation_price=expected_accommodation_price,
            score=score,
            savings_absolute=round(savings_absolute, 2),
            savings_percentage=round(savings_percentage, 4),
            baseline_source=baseline_source,
            price_insight=price_insight,
            historical_baseline=historical_baseline,
        )
        # Tier 2/3 quality filters (hotel rating, flight times); Tier 1
        # is exempt - see engine/quality_gate.py.
        return deal if passes_quality_gate(deal) else None

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

        # Baseline priority: our own real observed accommodation history
        # first (if we have enough of it), then the provider's own
        # "typical price" concept (honestly None for SerpApiAccommodation
        # Provider - see its module docstring), then nothing. Mirrors the
        # flight-side priority in _evaluate_flight exactly. See "Deal
        # Engine Integration" in docs/PRODUCT_SPEC.md.
        accommodation_baseline: HistoricalBaseline | None = None
        if self._accommodation_price_history_repository is not None:
            accommodation_baseline = get_accommodation_historical_baseline(
                self._accommodation_price_history_repository,
                flight.destination,
                flight.departure_date,
                flight.return_date,
                cheapest.currency,
                cheapest.total_price,
            )

        if accommodation_baseline is not None:
            typical_price = accommodation_baseline.statistics.median
        else:
            typical_price = self._accommodation_provider.get_typical_total_price(
                flight.destination, nights, flight.departure_date.month
            )

        assessment = assess_accommodation(cheapest, typical_price)
        return cheapest, typical_price, assessment
