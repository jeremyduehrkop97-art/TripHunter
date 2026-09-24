from trip_hunter.alerts.destination_images import FALLBACK_IMAGE_URL, destination_image_url


def test_known_destinations_have_distinct_https_unsplash_images():
    urls = {code: destination_image_url(code) for code in ("PMI", "BCN", "FCO", "LIS")}
    assert len(set(urls.values())) == 4
    assert FALLBACK_IMAGE_URL not in urls.values()
    assert all(u.startswith("https://images.unsplash.com/") for u in urls.values())


def test_unknown_destination_returns_the_fallback():
    assert destination_image_url("ZZZ") == FALLBACK_IMAGE_URL
