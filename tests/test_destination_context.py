from __future__ import annotations

from trip_hunter.alerts.destination_context import destination_context


def test_known_destinations_return_their_own_text():
    for code in ("PMI", "BCN", "FCO", "LIS"):
        text = destination_context(code)
        assert isinstance(text, str)
        assert len(text) > 0


def test_pmi_matches_the_product_brief_example():
    assert destination_context("PMI") == (
        "Perfekt für eine spontane Auszeit: Ob Strandspaziergänge, Tapas in der "
        "Altstadt von Palma oder milde Sonnenstunden abseits der Hauptsaison – "
        "Palma bietet mediterranes Feeling bei nur gut zwei Stunden Flugzeit."
    )


def test_unknown_destination_gets_the_generic_fallback():
    assert destination_context("XXX") == (
        "Ein Ziel, das einen genaueren Blick wert ist – die Zahlen unten sprechen für sich."
    )


def test_fallback_never_claims_specific_local_knowledge():
    """The fallback must stay honestly generic - never invent details for
    a destination this project has no real content for."""
    fallback = destination_context("ZZZ")
    for known_text in (
        destination_context("PMI"),
        destination_context("BCN"),
        destination_context("FCO"),
        destination_context("LIS"),
    ):
        assert fallback != known_text


def test_every_known_destination_has_a_distinct_text():
    codes = ["PMI", "BCN", "FCO", "LIS"]
    texts = [destination_context(code) for code in codes]

    assert len(set(texts)) == len(codes)
