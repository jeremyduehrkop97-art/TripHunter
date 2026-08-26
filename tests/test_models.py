from datetime import date

import pytest

from vacation_hunter.models import AccommodationOffer, DealScore


def test_accommodation_offer_nights_property():
    offer = AccommodationOffer(
        destination="PMI",
        check_in=date(2026, 10, 2),
        check_out=date(2026, 10, 7),
        total_price=205.0,
        currency="EUR",
        name="Test Hotel",
        rating=4.0,
        provider="test",
    )
    assert offer.nights == 5


def test_deal_score_rejects_out_of_range_total():
    with pytest.raises(ValueError):
        DealScore(total=150, breakdown={})
