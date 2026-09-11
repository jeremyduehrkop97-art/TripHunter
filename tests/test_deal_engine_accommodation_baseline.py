"""Tests for DealEngine's accommodation baseline priority: our own observed
accommodation history first (if we have >= MIN_HISTORY_OBSERVATIONS), then
the AccommodationProvider's own get_typical_total_price(), then nothing.
Mirrors the flight-side priority tested implicitly via
price_history_repository - see "Deal Engine Integration" in
docs/PRODUCT_SPEC.md.

Deliberately uses a fake AccommodationProvider whose get_typical_total_price
always returns None - simulating the real situation
(SerpApiAccommodationProvider has no price-insight equivalent, see its
module docstring) - to prove the historical-baseline path is what actually
makes COMBINED_TRIP_DROP reachable, not a provider-supplied baseline.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.accommodation_price_history_repository import AccommodationPriceHistoryRepository
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.price_statistics import MIN_HISTORY_OBSERVATIONS
from trip_hunter.models import AccommodationObservation, AccommodationOffer, DealType
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.mock_flight_provider import MockFlightProvider

_DESTINATION = "PMI"
_CHECK_IN = date(2026, 10, 2)
_CHECK_OUT = date(2026, 10, 7)
_CURRENCY = "EUR"

# The exact "Beispiel-Szenario" from docs/PRODUCT_SPEC.md: MockFlightProvider
# already provides HAM->PMI at 79 EUR vs. a 180 EUR baseline. A real
# accommodation offer at 205 EUR, with a historical median of 340 EUR (the
# same baseline MockAccommodationProvider hardcodes for PMI), reproduces
# the documented 236 EUR / 45% combined saving.
_CHEAP_OFFER = AccommodationOffer(
    destination=_DESTINATION,
    check_in=_CHECK_IN,
    check_out=_CHECK_OUT,
    total_price=205.0,
    currency=_CURRENCY,
    name="Hotel Playa Sol",
    rating=4.2,
    provider="test",
)


class _AccommodationProviderWithNoBaseline(AccommodationProvider):
    """Simulates SerpApiAccommodationProvider: real offers, but no
    provider-side typical-price concept at all (always None)."""

    def __init__(self, offers: list[AccommodationOffer]):
        self._offers = offers
        self.get_typical_total_price_calls = 0

    def search_accommodations(
        self, destination: str, check_in: date, check_out: date
    ) -> list[AccommodationOffer]:
        return [
            offer
            for offer in self._offers
            if offer.destination == destination
            and offer.check_in == check_in
            and offer.check_out == check_out
        ]

    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float | None:
        self.get_typical_total_price_calls += 1
        return None


def _seed_history(repo: AccommodationPriceHistoryRepository, prices: list[float]) -> None:
    for i, price in enumerate(prices):
        repo.add_observation(
            AccommodationObservation(
                destination=_DESTINATION,
                check_in=_CHECK_IN,
                check_out=_CHECK_OUT,
                price=price,
                currency=_CURRENCY,
                provider="test",
                name="Historical Hotel",
                observed_at=datetime(2026, 8, 1 + i, 8, 0, tzinfo=timezone.utc),
            )
        )


def _engine(accommodation_provider, accommodation_price_history_repository=None) -> DealEngine:
    return DealEngine(
        flight_provider=MockFlightProvider(),
        accommodation_provider=accommodation_provider,
        accommodation_price_history_repository=accommodation_price_history_repository,
    )


def _find_ham_pmi_deal(engine: DealEngine):
    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
        return_date=_CHECK_OUT,
    )
    assert len(deals) == 1
    return deals[0]


def test_combined_trip_drop_triggers_from_own_accommodation_history(tmp_path):
    """The core requirement: >= 5 real hotel observations + a cheap flight
    must produce a real COMBINED_TRIP_DROP, sourced entirely from our own
    accommodation history - the provider never supplies a baseline."""
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    _seed_history(repo, [320.0, 330.0, 340.0, 350.0, 360.0])  # median = 340.0
    provider = _AccommodationProviderWithNoBaseline([_CHEAP_OFFER])

    deal = _find_ham_pmi_deal(_engine(provider, accommodation_price_history_repository=repo))

    assert deal.deal_type == DealType.COMBINED_TRIP_DROP
    assert deal.accommodation is not None
    assert deal.accommodation.total_price == 205.0
    assert deal.expected_accommodation_price == 340.0  # the historical median, not from the provider
    assert deal.savings_absolute == 236.0  # (180-79) + (340-205), matches docs/PRODUCT_SPEC.md
    assert round(deal.savings_percentage, 2) == 0.45


def test_historical_baseline_is_preferred_over_provider_typical_price(tmp_path):
    """Priority proof: once >= MIN_HISTORY_OBSERVATIONS exist, the provider's
    get_typical_total_price() must not even be consulted."""
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    _seed_history(repo, [320.0, 330.0, 340.0, 350.0, 360.0])
    provider = _AccommodationProviderWithNoBaseline([_CHEAP_OFFER])

    _find_ham_pmi_deal(_engine(provider, accommodation_price_history_repository=repo))

    assert provider.get_typical_total_price_calls == 0


def test_below_minimum_observations_falls_back_to_provider_and_finds_no_baseline(tmp_path):
    """One observation short of MIN_HISTORY_OBSERVATIONS: still no baseline
    of our own, so DealEngine falls back to the provider - which, like the
    real SerpApiAccommodationProvider, honestly has none either. The deal
    must stay flight-only, never a guessed COMBINED_TRIP_DROP."""
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    _seed_history(repo, [320.0, 330.0, 340.0, 350.0][: MIN_HISTORY_OBSERVATIONS - 1])
    provider = _AccommodationProviderWithNoBaseline([_CHEAP_OFFER])

    deal = _find_ham_pmi_deal(_engine(provider, accommodation_price_history_repository=repo))

    assert provider.get_typical_total_price_calls == 1  # fallback WAS consulted
    assert deal.expected_accommodation_price is None
    assert deal.accommodation is not None  # the real offer is still attached
    assert deal.deal_type == DealType.FLIGHT_DROP
    assert deal.deal_type is not DealType.COMBINED_TRIP_DROP


def test_no_repository_at_all_falls_back_to_provider_directly(tmp_path):
    """No accommodation_price_history_repository passed at all (the
    pre-existing default) - behavior must be unchanged: straight to the
    provider's own get_typical_total_price()."""
    provider = _AccommodationProviderWithNoBaseline([_CHEAP_OFFER])

    deal = _find_ham_pmi_deal(_engine(provider, accommodation_price_history_repository=None))

    assert provider.get_typical_total_price_calls == 1
    assert deal.expected_accommodation_price is None
    assert deal.deal_type == DealType.FLIGHT_DROP


def test_unrelated_destination_history_does_not_leak_into_baseline(tmp_path):
    """A repository that has history, but not for THIS destination/dates,
    must behave exactly like an empty one - never borrow a stranger's
    baseline."""
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    for i, price in enumerate([100.0, 110.0, 120.0, 130.0, 140.0]):
        repo.add_observation(
            AccommodationObservation(
                destination="AGP",  # different destination
                check_in=_CHECK_IN,
                check_out=_CHECK_OUT,
                price=price,
                currency=_CURRENCY,
                provider="test",
                observed_at=datetime(2026, 8, 1 + i, 8, 0, tzinfo=timezone.utc),
            )
        )
    provider = _AccommodationProviderWithNoBaseline([_CHEAP_OFFER])

    deal = _find_ham_pmi_deal(_engine(provider, accommodation_price_history_repository=repo))

    assert deal.expected_accommodation_price is None
    assert deal.deal_type == DealType.FLIGHT_DROP
