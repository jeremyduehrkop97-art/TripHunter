"""Fixed observation targets for the daily sampler (daily_sampler.py).

Resolves the "Frequency-Bias-Gefahr" and "Kein Scheduler" gaps documented
in docs/PRODUCT_SPEC.md ("Deduplikation") and the Trip Hunter handover
briefing: automatic collection needs "eine Observation pro Comparison
Group pro geplantem Messzeitpunkt, nicht 'so oft wie zufällig gesucht
wird'" - a fixed, explicit target list is exactly that fixed sampling
rhythm's input. This module owns ONLY the target list - no scheduling, no
API calls, no persistence. See daily_sampler.py for the runner.

Every target is an EXPLICIT, self-validating comparison group (see
"Explicit Comparison Groups" in docs/PRODUCT_SPEC.md) - never a route
pattern or a date range to expand. Adding a destination means adding one
more explicit entry here, not a loop parameter; that keeps the "no route
loops, no date loops" credit-safety rule (see docs/ARCHITECTURE.md "API
Credit Safety") true by construction, not by runtime discipline.

Flight and hotel targets are deliberately two separate lists, not paired
1:1 - a route and a stay are independent comparison groups (see
FlightComparisonGroup / AccommodationComparisonGroup in models.py), even
when today's real targets happen to describe the same trip.
"""

from __future__ import annotations

from datetime import date

from trip_hunter.models import AccommodationComparisonGroup, FlightComparisonGroup, TripType

# The real HAM->PMI trip this project has been sampling manually since
# MVP 0.4 (record_price_snapshot.py / record_hotel_price_snapshot.py) -
# already has real observations in data/trip_hunter.db.
FLIGHT_TARGETS: list[FlightComparisonGroup] = [
    FlightComparisonGroup(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
    # A genuine Friday->Sunday weekend getaway, matching
    # weekend_radar_demo.py's HAM->BCN scenario.
    FlightComparisonGroup(
        origin="HAM",
        destination="BCN",
        departure_date=date(2026, 10, 9),
        return_date=date(2026, 10, 11),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
    # A genuine Thursday->Sunday LONG weekend (3 nights, not the 2-night
    # Fri->Sun shape above) - a second, distinct BCN comparison group, not
    # a replacement for it. Deliberately a different week so the two BCN
    # targets track genuinely independent trips, not alternate dates for
    # "the same" one (see "Explicit Comparison Groups" in
    # docs/PRODUCT_SPEC.md - result count must never decide which group
    # "wins"; here there simply are two, on purpose).
    FlightComparisonGroup(
        origin="HAM",
        destination="BCN",
        departure_date=date(2026, 11, 5),
        return_date=date(2026, 11, 8),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
    # A genuine Thursday->Sunday long weekend to Rome.
    FlightComparisonGroup(
        origin="HAM",
        destination="FCO",
        departure_date=date(2026, 11, 19),
        return_date=date(2026, 11, 22),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
]

HOTEL_TARGETS: list[AccommodationComparisonGroup] = [
    AccommodationComparisonGroup(
        destination="PMI",
        check_in=date(2026, 10, 2),
        check_out=date(2026, 10, 7),
        currency="EUR",
    ),
    AccommodationComparisonGroup(
        destination="BCN",
        check_in=date(2026, 10, 9),
        check_out=date(2026, 10, 11),
        currency="EUR",
    ),
    AccommodationComparisonGroup(
        destination="BCN",
        check_in=date(2026, 11, 5),
        check_out=date(2026, 11, 8),
        currency="EUR",
    ),
    AccommodationComparisonGroup(
        destination="FCO",
        check_in=date(2026, 11, 19),
        check_out=date(2026, 11, 22),
        currency="EUR",
    ),
]
