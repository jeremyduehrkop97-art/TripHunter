from vacation_hunter.engine.scoring import score_trip


def test_score_is_capped_at_100():
    score = score_trip(
        trip_savings_percentage=1.5,
        flight_savings_percentage=1.5,
        hotel_savings_percentage=1.5,
        savings_absolute=10_000,
        flight_stops=0,
    )
    assert score.total == 100


def test_score_is_zero_for_no_savings():
    score = score_trip(
        trip_savings_percentage=0.0,
        flight_savings_percentage=0.0,
        hotel_savings_percentage=0.0,
        savings_absolute=0.0,
        flight_stops=2,
    )
    assert score.total == 0


def test_breakdown_sums_to_total_score():
    score = score_trip(
        trip_savings_percentage=0.45,
        flight_savings_percentage=0.56,
        hotel_savings_percentage=0.40,
        savings_absolute=236,
        flight_stops=0,
    )
    assert round(sum(score.breakdown.values())) == score.total


def test_higher_savings_yield_higher_score():
    low = score_trip(
        trip_savings_percentage=0.10,
        flight_savings_percentage=0.10,
        hotel_savings_percentage=0.10,
        savings_absolute=20,
        flight_stops=1,
    )
    high = score_trip(
        trip_savings_percentage=0.50,
        flight_savings_percentage=0.50,
        hotel_savings_percentage=0.50,
        savings_absolute=250,
        flight_stops=0,
    )
    assert high.total > low.total
