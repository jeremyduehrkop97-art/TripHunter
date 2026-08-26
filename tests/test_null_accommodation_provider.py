from datetime import date

from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider


def test_search_accommodations_always_empty():
    provider = NullAccommodationProvider()
    result = provider.search_accommodations("PMI", date(2026, 10, 2), date(2026, 10, 7))
    assert result == []
