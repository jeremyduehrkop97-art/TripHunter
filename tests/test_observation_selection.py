"""Tests for observation_from_search_results: the safe, snapshot-aware way
to turn a real search result (many FlightOffers, possibly spanning several
travel-date groups) into history (at most one PriceObservation for an
EXPLICITLY given FlightComparisonGroup). See "Observation Semantics" and
"Explicit Comparison Groups" in docs/PRODUCT_SPEC.md (MVP 0.3.1 / 0.3.2).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from trip_hunter.engine.price_statistics import compute_statistics
from trip_hunter.models import FlightComparisonGroup, FlightOffer, TripType
from trip_hunter.price_history_repository import (
    PriceHistoryRepository,
    observation_from_search_results,
)

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE = date(2026, 10, 2)
_RETURN = date(2026, 10, 7)
_CURRENCY = "EUR"

_GROUP = FlightComparisonGroup(
    origin=_ORIGIN,
    destination=_DESTINATION,
    departure_date=_DEPARTURE,
    return_date=_RETURN,
    trip_type=TripType.ROUND_TRIP,
    currency=_CURRENCY,
)


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


def test_nine_offers_produce_exactly_one_observation():
    offers = [_offer(p) for p in [184.0, 205.0, 227.0, 249.0, 279.0, 303.0, 310.0, 320.0, 340.0]]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None


def test_cheapest_offer_is_selected():
    offers = [_offer(227.0), _offer(184.0), _offer(205.0)]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.price == 184.0


# C) billigstes Angebot hat price_confirmed_complete=False -> ignorieren
def test_incomplete_cheapest_offer_is_ignored():
    offers = [
        _offer(99.0, price_confirmed_complete=False),  # cheapest, but invalid
        _offer(184.0),
        _offer(205.0),
    ]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.price == 184.0


def test_all_offers_incomplete_yields_no_observation():
    offers = [_offer(99.0, price_confirmed_complete=False), _offer(150.0, price_confirmed_complete=False)]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is None


def test_empty_offer_list_yields_no_observation():
    assert observation_from_search_results([], _GROUP) is None


# A) Eine größere fremde Gruppe darf nicht gewinnen - der Caller entscheidet
# per FlightComparisonGroup, nicht die Anzahl der Ergebnisse.
def test_explicit_group_wins_over_larger_unrelated_group():
    target_group_offers = [_offer(184.0), _offer(205.0), _offer(227.0)]  # 3 offers, our group
    other_date_offers = [
        _offer(p, departure_date=date(2026, 10, 3), return_date=date(2026, 10, 8))
        for p in [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0]  # 7 offers, larger group!
    ]
    offers = other_date_offers + target_group_offers

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.price == 184.0  # from the smaller, explicitly requested group
    assert observation.departure_date == _DEPARTURE
    assert observation.return_date == _RETURN


# B) Keine passende Gruppe -> None
def test_no_matching_group_yields_none():
    offers = [
        _offer(p, departure_date=date(2026, 11, 1), return_date=date(2026, 11, 8))
        for p in [100.0, 120.0]
    ]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is None


# C/D) Falsche Currency -> ignorieren
def test_different_currency_offers_are_not_mixed_in():
    offers = [
        _offer(50.0, currency="USD"),  # cheapest overall, wrong currency
        _offer(184.0, currency="EUR"),
        _offer(205.0, currency="EUR"),
        _offer(227.0, currency="EUR"),
    ]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.currency == "EUR"
    assert observation.price == 184.0


def test_only_wrong_currency_offers_yields_none():
    offers = [_offer(50.0, currency="USD"), _offer(60.0, currency="USD")]

    assert observation_from_search_results(offers, _GROUP) is None


# D/E) Falsche Route -> ignorieren
def test_different_route_offers_are_not_mixed_in():
    offers = [
        _offer(60.0, destination="AGP"),  # cheapest overall, wrong route
        _offer(184.0, destination="PMI"),
        _offer(205.0, destination="PMI"),
        _offer(227.0, destination="PMI"),
    ]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.destination == "PMI"
    assert observation.price == 184.0


# E) Falsches departure_date -> ignorieren
def test_different_departure_date_offers_are_not_mixed_in():
    offers = [
        _offer(60.0, departure_date=date(2026, 10, 3), return_date=date(2026, 10, 8)),
        _offer(184.0),
        _offer(205.0),
    ]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.departure_date == _DEPARTURE
    assert observation.price == 184.0


# F) Falsches return_date -> ignorieren
def test_different_return_date_offers_are_not_mixed_in():
    offers = [
        _offer(60.0, return_date=date(2026, 10, 9)),
        _offer(184.0),
        _offer(205.0),
    ]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.return_date == _RETURN
    assert observation.price == 184.0


def test_stops_do_not_fragment_the_comparison_group():
    """MVP tracks cheapest_any - a cheaper 1-stop offer wins over a pricier
    direct flight. See "Stops" in docs/PRODUCT_SPEC.md."""
    offers = [_offer(220.0, stops=0), _offer(150.0, stops=1)]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.price == 150.0
    assert observation.stops == 1


def test_airline_does_not_fragment_the_comparison_group():
    offers = [_offer(220.0, airline="Lufthansa"), _offer(150.0, airline="Ryanair")]

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.price == 150.0
    assert observation.airline == "Ryanair"


# I) 30 fremde Angebote + 2 passende -> die zwei passenden bestimmen die Observation
def test_two_matching_offers_among_thirty_unrelated_ones_determine_the_observation():
    unrelated = [
        _offer(p, departure_date=date(2026, 12, 24), return_date=date(2026, 12, 31))
        for p in range(50, 80)
    ]
    matching = [_offer(184.0), _offer(205.0)]
    offers = unrelated + matching

    observation = observation_from_search_results(offers, _GROUP)

    assert observation is not None
    assert observation.price == 184.0
    assert observation.departure_date == _DEPARTURE


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
        observation = observation_from_search_results(offers, _GROUP, observed_at=observed_at)
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

    observation = observation_from_search_results(offers, _GROUP)
    assert observation is not None
    repo.add_observation(observation)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)

    assert len(stored) == 1
    assert stored[0].price == 150.0  # the cheapest of the 30
