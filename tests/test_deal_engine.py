from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.error_fare_floor import FLOOR_TRIGGER_SCORE
from trip_hunter.models import (
    AccommodationOffer,
    BaselineSource,
    DealType,
    FlightOffer,
    PriceInsight,
    PriceObservation,
    TripType,
)
from trip_hunter.price_history_repository import PriceHistoryRepository
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.providers.mock_accommodation_provider import MockAccommodationProvider
from trip_hunter.providers.mock_flight_provider import MockFlightProvider
from trip_hunter.providers.null_accommodation_provider import NullAccommodationProvider


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


def _seed_history(repo: PriceHistoryRepository, prices: list[float]) -> None:
    for i, price in enumerate(prices):
        repo.add_observation(
            PriceObservation(
                origin="HAM",
                destination="PMI",
                departure_date=date(2026, 10, 2),
                return_date=date(2026, 10, 7),
                trip_type=TripType.ROUND_TRIP,
                price=price,
                currency="EUR",
                provider="test",
                stops=0,
                airline="Testair",
                observed_at=datetime(2026, 8, 1 + i, 8, 0, tzinfo=timezone.utc),
            )
        )


class _CallCountingRepository(PriceHistoryRepository):
    """Wraps PriceHistoryRepository and counts get_route_statistics calls -
    used to prove PRICE_INCOMPLETE short-circuits before any baseline
    lookup, including our own history."""

    def __init__(self, db_path):
        super().__init__(db_path=db_path)
        self.get_route_statistics_call_count = 0

    def get_route_statistics(self, *args, **kwargs):
        self.get_route_statistics_call_count += 1
        return super().get_route_statistics(*args, **kwargs)


def test_own_historical_baseline_takes_priority_over_price_insight(tmp_path):
    """Priority order: OWN_HISTORICAL_BASELINE > PROVIDER_PRICE_INSIGHT >
    NO_BASELINE. With enough of our own history, the provider's price
    insight must never even be consulted."""
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    _seed_history(repo, [180.0, 175.0, 190.0, 185.0, 178.0, 182.0])  # median 181

    insight = PriceInsight(
        provider_lowest_price=999.0,
        typical_price_low=900.0,
        typical_price_high=1000.0,
        price_level="typical",
        source="google_flights",
    )
    provider = _CallCountingPriceInsightFlightProvider([_flight(119.0)], insight)
    engine = DealEngine(
        flight_provider=provider,
        accommodation_provider=NullAccommodationProvider(),
        price_history_repository=repo,
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
    assert deal.baseline_source == BaselineSource.OWN_HISTORICAL_BASELINE
    assert deal.deal_type == DealType.FLIGHT_DROP
    assert deal.expected_flight_price == 181.0  # our median, not the price insight
    assert deal.historical_baseline is not None
    assert deal.historical_baseline.statistics.observation_count == 6
    # The price insight must never have been consulted at all.
    assert provider.get_price_insight_call_count == 0


def test_falls_back_to_price_insight_when_history_insufficient(tmp_path):
    """Fewer than MIN_HISTORY_OBSERVATIONS of our own data: the engine
    falls back to the provider's price insight, same as before MVP 0.3."""
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    _seed_history(repo, [180.0, 175.0])  # too few

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
        price_history_repository=repo,
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
    assert deal.baseline_source == BaselineSource.PROVIDER_PRICE_INSIGHT
    assert deal.historical_baseline is None


def test_price_incomplete_takes_priority_over_historical_baseline(tmp_path):
    """PRICE_INCOMPLETE must win even when plenty of matching own history
    exists - the repository must never even be queried."""
    repo = _CallCountingRepository(db_path=tmp_path / "history.db")
    _seed_history(repo, [180.0, 175.0, 190.0, 185.0, 178.0, 182.0])

    flight = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=119.0,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
        price_confirmed_complete=False,
    )
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
        price_history_repository=repo,
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
    assert deal.historical_baseline is None
    assert deal.baseline_source == BaselineSource.NO_BASELINE
    assert repo.get_route_statistics_call_count == 0


# --- absolute error-fare floor trigger (no baseline needed) ------------------


def _floor_flight(price: float, *, destination: str = "PMI") -> FlightOffer:
    return FlightOffer(
        origin="HAM",
        destination=destination,
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=price,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
    )


class _FixedAccommodationProvider(AccommodationProvider):
    """Always returns one fixed offer, regardless of destination/dates -
    used to control the per-night price the floor trigger's hotel check
    sees, without depending on MockAccommodationProvider's own fixture
    data (which varies by route)."""

    def __init__(self, total_price: float | None):
        self._total_price = total_price

    def search_accommodations(self, destination, check_in, check_out):
        if self._total_price is None:
            return []
        return [
            AccommodationOffer(
                destination=destination,
                check_in=check_in,
                check_out=check_out,
                total_price=self._total_price,
                currency="EUR",
                name="Test Hotel",
                rating=4.0,
                provider="test",
            )
        ]

    def get_typical_total_price(self, destination, nights, month):
        return None


def test_short_haul_floor_trigger_fires_with_zero_history():
    """No baseline of any kind exists (own history empty, mock provider's
    get_typical_price returns None) - an absurdly cheap short-haul round
    trip must still be flagged, on day one."""
    flight = _floor_flight(29.0, destination="PMI")  # PMI: short-haul, floor 35 EUR
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=_FixedAccommodationProvider(50.0 * 5),  # 5 nights @ 50 EUR/night
    )

    deals = engine.find_trip_deals(
        origin="HAM", destination="PMI",
        earliest_departure=date(2026, 10, 2), latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type == DealType.ERROR_FARE
    assert deal.baseline_source == BaselineSource.ABSOLUTE_FLOOR_TRIGGER
    assert deal.score is not None
    assert deal.score.total == FLOOR_TRIGGER_SCORE
    assert deal.expected_flight_price is None  # honestly: no baseline was used
    assert deal.savings_percentage is None
    assert deal.accommodation is not None


def test_short_haul_price_just_above_the_floor_does_not_trigger():
    flight = _floor_flight(35.01, destination="PMI")
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM", destination="PMI",
        earliest_departure=date(2026, 10, 2), latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    assert deals[0].deal_type == DealType.BASELINE_UNAVAILABLE


def test_mid_haul_destination_uses_the_higher_floor():
    """A price that would fail the short-haul floor still triggers for an
    explicit mid-haul destination (Kanaren/Griechenland allowlist)."""
    flight = _floor_flight(65.0, destination="LPA")  # Gran Canaria - mid-haul, floor 70
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM", destination="LPA",
        earliest_departure=date(2026, 10, 2), latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    assert deals[0].deal_type == DealType.ERROR_FARE


def test_floor_trigger_fires_despite_an_expensive_hotel():
    """A real error fare is always Tier 1, whatever the hotel costs."""
    flight = _floor_flight(29.0, destination="PMI")
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=_FixedAccommodationProvider(100.0 * 5),  # 100 EUR/night, 5 nights
    )

    deals = engine.find_trip_deals(
        origin="HAM", destination="PMI",
        earliest_departure=date(2026, 10, 2), latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    assert deals[0].deal_type == DealType.ERROR_FARE


def test_floor_trigger_fires_without_any_accommodation_data():
    """A missing hotel offer is never a reason to block the trigger - nor
    is an expensive one."""
    flight = _floor_flight(29.0, destination="PMI")
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM", destination="PMI",
        earliest_departure=date(2026, 10, 2), latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type == DealType.ERROR_FARE
    assert deal.accommodation is None


def test_floor_trigger_never_overrides_a_real_baseline(tmp_path):
    """The absolute floor is only a fallback for a MISSING baseline - once
    real own history exists, that history's verdict wins, even if the
    price would also have cleared the absolute floor."""
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    _seed_history(repo, [30.0, 31.0, 29.0, 30.0, 30.0])  # median ~30, own baseline exists

    flight = _floor_flight(29.0, destination="PMI")  # clears the 35 EUR floor too
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
        price_history_repository=repo,
    )

    deals = engine.find_trip_deals(
        origin="HAM", destination="PMI",
        earliest_departure=date(2026, 10, 2), latest_departure=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
    )

    # 29 EUR vs. a ~30 EUR own-history median is not a notable saving at
    # all (< UNUSUALLY_LOW_THRESHOLD) - the real baseline says "nothing
    # interesting here", and the flight never even reaches the floor
    # trigger fallback (which only runs after a baseline comes up empty).
    assert deals == []
