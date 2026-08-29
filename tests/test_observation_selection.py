"""Tests for observation_from_search_results: the safe, snapshot-aware way
to turn a real search result (many FlightOffers) into history (at most one
PriceObservation). See "Observation Semantics" in docs/PRODUCT_SPEC.md and
the MVP 0.3.1 audit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from vacation_hunter.engine.price_statistics import compute_statistics
from vacation_hunter.models import FlightOffer, TripType
from vacation_hunter.price_history_repository import (
    PriceHistoryRepository,
    observation_from_search_results,
)

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE = date(2026, 10, 2)
_RETURN = date(2026, 10, 7)
_CURRENCY = "EUR"


def _offer(
    price: float,
    *,
    origin: str = _ORIGIN,
    destination: str = _DESTINATION,
    departure_date: date = _DEPARTURE,
    return_date: date = _RETURN,
    currency: str = _CURRENCY,
    stops: int = 0,
    airline: str = "Testair",
    price_confirmed_complete: bool = True,
) -> FlightOffer:
    return FlightOffer(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        price=price,
        currency=currency,
        airline=airline,
        stops=stops,
        provider="test",
        price_confirmed_complete=price_confirmed_complete,
    )


# A) 9 Angebote -> genau eine Marktbeobachtung
def test_nine_offers_produce_exactly_one_observation():
    offers = [_offer(p) for p in [184.0, 205.0, 227.0, 249.0, 279.0, 303.0, 310.0, 320.0, 340.0]]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None


# B) 184, 205, 227 EUR -> Observation.price == 184
def test_cheapest_offer_is_selected():
    offers = [_offer(227.0), _offer(184.0), _offer(205.0)]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None
    assert observation.price == 184.0


# C) billigstes Angebot hat price_confirmed_complete=False -> ignorieren
def test_incomplete_cheapest_offer_is_ignored():
    offers = [
        _offer(99.0, price_confirmed_complete=False),  # cheapest, but invalid
        _offer(184.0),
        _offer(205.0),
    ]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None
    assert observation.price == 184.0


def test_all_offers_incomplete_yields_no_observation():
    offers = [_offer(99.0, price_confirmed_complete=False), _offer(150.0, price_confirmed_complete=False)]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is None


def test_empty_offer_list_yields_no_observation():
    assert observation_from_search_results([], TripType.ROUND_TRIP) is None


# D) unterschiedliche Currency darf nicht vermischt werden
def test_different_currency_offers_are_not_mixed_in():
    offers = [
        _offer(50.0, currency="USD"),  # cheapest overall, wrong currency, lone outlier
        _offer(184.0, currency="EUR"),
        _offer(205.0, currency="EUR"),
        _offer(227.0, currency="EUR"),
    ]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None
    assert observation.currency == "EUR"
    assert observation.price == 184.0


# E) unterschiedliche Route darf nicht vermischt werden
def test_different_route_offers_are_not_mixed_in():
    offers = [
        _offer(60.0, destination="AGP"),  # cheapest overall, wrong route, lone outlier
        _offer(184.0, destination="PMI"),
        _offer(205.0, destination="PMI"),
        _offer(227.0, destination="PMI"),
    ]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None
    assert observation.destination == "PMI"
    assert observation.price == 184.0


def test_stops_do_not_fragment_the_comparison_group():
    """MVP 0.3.1 deliberately tracks cheapest_any - a cheaper 1-stop offer
    wins over a pricier direct flight. See "Stops" in docs/PRODUCT_SPEC.md."""
    offers = [_offer(220.0, stops=0), _offer(150.0, stops=1)]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None
    assert observation.price == 150.0
    assert observation.stops == 1


def test_airline_does_not_fragment_the_comparison_group():
    offers = [_offer(220.0, airline="Lufthansa"), _offer(150.0, airline="Ryanair")]

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)

    assert observation is not None
    assert observation.price == 150.0
    assert observation.airline == "Ryanair"


# F) mehrere Search Snapshots -> N Beobachtungen, Median korrekt
def test_multiple_snapshots_produce_one_observation_each_and_correct_median(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    base_time = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    # Each "day" is its own multi-offer search snapshot.
    daily_snapshots = [
        [184.0, 205.0, 227.0],  # cheapest 184
        [191.0, 210.0],  # cheapest 191
        [178.0, 199.0, 220.0],  # cheapest 178
        [186.0, 240.0],  # cheapest 186
        [195.0, 230.0],  # cheapest 195
        [181.0, 200.0, 260.0],  # cheapest 181
    ]

    for day_offset, prices in enumerate(daily_snapshots):
        offers = [_offer(p) for p in prices]
        observed_at = base_time + timedelta(days=day_offset)
        observation = observation_from_search_results(offers, TripType.ROUND_TRIP, observed_at=observed_at)
        assert observation is not None
        repo.add_observation(observation)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)

    assert len(stored) == 6
    assert sorted(o.price for o in stored) == [178.0, 181.0, 184.0, 186.0, 191.0, 195.0]

    stats = compute_statistics([o.price for o in stored])
    assert stats.median == 185.0  # median of 178,181,184,186,191,195


# G) 30 Angebote in einem Search Snapshot zaehlen NICHT als 30 Zeitbeobachtungen
def test_large_single_snapshot_still_counts_as_one_observation(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    offers = [_offer(150.0 + i) for i in range(30)]  # 30 distinct prices, one snapshot

    observation = observation_from_search_results(offers, TripType.ROUND_TRIP)
    assert observation is not None
    repo.add_observation(observation)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)

    assert len(stored) == 1
    assert stored[0].price == 150.0  # the cheapest of the 30
