from __future__ import annotations

from datetime import date

from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import BaselineSource, DealType, FlightOffer, PriceInsight
from vacation_hunter.providers.flight_provider import FlightProvider
from vacation_hunter.providers.mock_accommodation_provider import MockAccommodationProvider
from vacation_hunter.providers.mock_flight_provider import MockFlightProvider
from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider


def _engine() -> DealEngine:
    return DealEngine(
        flight_provider=MockFlightProvider(),
        accommodation_provider=MockAccommodationProvider(),
    )


def test_ham_pmi_trip_is_detected_as_combined_trip_drop():
    """Reproduces the example scenario from docs/PRODUCT_SPEC.md."""
    deals = _engine().find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )

    assert len(deals) == 1
    deal = deals[0]

    assert deal.deal_type == DealType.COMBINED_TRIP_DROP
    assert deal.flight.price == 79.0
    assert deal.accommodation is not None
    assert deal.accommodation.total_price == 205.0
    assert deal.actual_total_price == 284.0
    assert deal.expected_total_price == 520.0
    assert deal.savings_absolute == 236.0
    assert round(deal.savings_percentage * 100) == 45
    assert 0 <= deal.score.total <= 100


def test_route_with_normal_prices_yields_no_deal():
    deals = _engine().find_trip_deals(
        origin="MUC",
        destination="LIS",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )
    assert deals == []


def test_partial_deal_route_is_flagged_but_not_combined():
    deals = _engine().find_trip_deals(
        origin="BER",
        destination="BCN",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )
    assert len(deals) == 1
    assert deals[0].deal_type != DealType.COMBINED_TRIP_DROP


def test_unknown_route_yields_no_deals_and_does_not_raise():
    deals = _engine().find_trip_deals(
        origin="XXX",
        destination="YYY",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )
    assert deals == []


class _NoBaselineFlightProvider(FlightProvider):
    """Stand-in for a real search API: returns offers but has no historical
    baseline price for any route."""

    def __init__(self, offers: list[FlightOffer]):
        self._offers = offers

    def search_flights(
        self, origin, destination, earliest_departure, latest_departure, return_date=None
    ) -> list[FlightOffer]:
        return self._offers

    def get_typical_price(self, origin, destination, month):
        return None


def test_flight_without_baseline_is_marked_unavailable_not_fabricated():
    """Central product requirement: a real current price with no historical
    comparison must never be classified as a price drop or error fare."""
    flight = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=89.0,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
    )
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type == DealType.BASELINE_UNAVAILABLE
    assert deal.deal_type not in (
        DealType.ERROR_FARE,
        DealType.FLIGHT_DROP,
        DealType.UNUSUALLY_LOW,
        DealType.COMBINED_TRIP_DROP,
    )
    assert deal.score is None
    assert deal.savings_absolute is None
    assert deal.expected_flight_price is None
    assert deal.actual_total_price == 89.0
    assert deal.baseline_source == BaselineSource.NO_BASELINE
    assert deal.price_insight is None


class _PriceInsightFlightProvider(FlightProvider):
    """Stand-in for a provider with no historical baseline of its own (like
    _NoBaselineFlightProvider) but that does supply a provider price
    insight for the route - e.g. Google Flights via SerpApi."""

    def __init__(self, offers: list[FlightOffer], insight: PriceInsight | None):
        self._offers = offers
        self._insight = insight

    def search_flights(
        self, origin, destination, earliest_departure, latest_departure, return_date=None
    ) -> list[FlightOffer]:
        return self._offers

    def get_typical_price(self, origin, destination, month):
        return None

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return self._insight


def _flight(price: float) -> FlightOffer:
    return FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=price,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
    )


def test_price_insight_showing_a_drop_is_used_as_provider_baseline():
    """When our own baseline is unavailable but the provider supplies a
    usable typical price range, the engine may use it - capped below
    ERROR_FARE, and clearly labeled as a provider (not our own) baseline."""
    insight = PriceInsight(
        provider_lowest_price=89.0,
        typical_price_low=160.0,
        typical_price_high=220.0,
        price_level="low",
        source="google_flights",
    )
    engine = DealEngine(
        flight_provider=_PriceInsightFlightProvider([_flight(89.0)], insight),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type in (DealType.FLIGHT_DROP, DealType.UNUSUALLY_LOW)
    assert deal.deal_type != DealType.ERROR_FARE
    assert deal.baseline_source == BaselineSource.PROVIDER_PRICE_INSIGHT
    assert deal.price_insight == insight
    assert deal.expected_flight_price == 190.0  # midpoint of 160/220
    assert deal.savings_absolute is not None
    assert deal.score is not None


def test_price_insight_present_but_normal_price_keeps_baseline_unavailable_deal_type():
    """A provider baseline that exists but shows nothing notable must not be
    hidden or fabricated into a deal - it stays BASELINE_UNAVAILABLE, but the
    insight data is still attached transparently."""
    insight = PriceInsight(
        provider_lowest_price=89.0,
        typical_price_low=90.0,
        typical_price_high=95.0,
        price_level="typical",
        source="google_flights",
    )
    engine = DealEngine(
        flight_provider=_PriceInsightFlightProvider([_flight(89.0)], insight),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type == DealType.BASELINE_UNAVAILABLE
    assert deal.baseline_source == BaselineSource.PROVIDER_PRICE_INSIGHT
    assert deal.price_insight == insight
    assert deal.score is None
    # Transparency: the comparison numbers are still shown even though they
    # didn't clear our deal bar.
    assert deal.expected_flight_price == 92.5
    assert deal.savings_absolute is not None


class _CallCountingPriceInsightFlightProvider(_PriceInsightFlightProvider):
    """Same as _PriceInsightFlightProvider, but records whether
    get_price_insight() was ever called - used to prove PRICE_INCOMPLETE
    short-circuits before any baseline lookup is attempted."""

    def __init__(self, offers, insight):
        super().__init__(offers, insight)
        self.get_price_insight_call_count = 0

    def get_price_insight(self, origin, destination, departure_date, return_date):
        self.get_price_insight_call_count += 1
        return super().get_price_insight(origin, destination, departure_date, return_date)


def test_incomplete_round_trip_price_is_never_compared_to_any_baseline():
    """PRICE_INCOMPLETE is a generic, provider-agnostic safeguard: any
    FlightOffer with price_confirmed_complete=False - regardless of which
    provider set that, or why - must never be compared against any
    baseline, own or provider-supplied. A controlled departure_token live
    test has since verified that SerpApiGoogleFlightsProvider itself now
    sets price_confirmed_complete=True for this exact 184 EUR / 205-385 EUR
    scenario (see test_price_confirmed_complete_flight_is_classified_normally
    below and "Price Completeness" in docs/PRODUCT_SPEC.md) - this test
    keeps the safeguard itself covered using a generic fake provider."""
    flight = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=184.0,
        currency="EUR",
        airline="Vueling",
        stops=1,
        provider="serpapi_google_flights",
        price_confirmed_complete=False,
    )
    insight = PriceInsight(
        provider_lowest_price=232.0,
        typical_price_low=205.0,
        typical_price_high=385.0,
        price_level="typical",
        source="google_flights",
    )
    provider = _CallCountingPriceInsightFlightProvider([flight], insight)
    engine = DealEngine(
        flight_provider=provider, accommodation_provider=NullAccommodationProvider()
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type == DealType.PRICE_INCOMPLETE
    assert deal.deal_type not in (
        DealType.FLIGHT_DROP,
        DealType.UNUSUALLY_LOW,
        DealType.ERROR_FARE,
        DealType.COMBINED_TRIP_DROP,
    )
    assert deal.score is None
    assert deal.savings_absolute is None
    assert deal.savings_percentage is None
    assert deal.expected_flight_price is None
    assert deal.price_insight is None
    assert deal.baseline_source == BaselineSource.NO_BASELINE
    # The baseline lookup must never even be attempted for an incomplete price.
    assert provider.get_price_insight_call_count == 0


def test_price_confirmed_complete_flight_is_classified_normally():
    """The real HAM->PMI scenario, now resolved: a controlled, user-approved
    departure_token live test (2 SerpApi credits) verified that the step-1
    price (184 EUR) already equals the cheapest matching round-trip total
    (also 184 EUR) for this response format - see "Price Completeness" in
    docs/PRODUCT_SPEC.md. With price_confirmed_complete=True, this offer is
    compared against the price insight as normal, and FLIGHT_DROP is now a
    valid result rather than something we have to suppress."""
    flight = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=184.0,
        currency="EUR",
        airline="Vueling",
        stops=1,
        provider="serpapi_google_flights",
        price_confirmed_complete=True,
    )
    insight = PriceInsight(
        provider_lowest_price=232.0,
        typical_price_low=205.0,
        typical_price_high=385.0,
        price_level="typical",
        source="google_flights",
    )
    engine = DealEngine(
        flight_provider=_PriceInsightFlightProvider([flight], insight),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
    )

    assert len(deals) == 1
    assert deals[0].deal_type == DealType.FLIGHT_DROP
    assert deals[0].baseline_source == BaselineSource.PROVIDER_PRICE_INSIGHT
