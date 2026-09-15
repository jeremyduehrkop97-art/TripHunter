from __future__ import annotations

from trip_hunter.monetization.affiliate import add_affiliate_tag, get_affiliate_tag


def test_get_affiliate_tag_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)

    assert get_affiliate_tag() is None


def test_get_affiliate_tag_returns_none_when_empty_string(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "")

    assert get_affiliate_tag() is None


def test_get_affiliate_tag_returns_configured_value(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "triphunter123")

    assert get_affiliate_tag() == "triphunter123"


def test_none_url_passes_through_as_none(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "triphunter123")

    assert add_affiliate_tag(None) is None


def test_no_tag_configured_returns_url_unchanged(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)

    url = "https://example.com/book/flight"
    assert add_affiliate_tag(url) == url


def test_tag_is_appended_to_a_plain_url(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)

    result = add_affiliate_tag("https://example.com/book/flight", tag="triphunter123")

    assert result == "https://example.com/book/flight?tp_aff=triphunter123"


def test_tag_is_appended_to_a_url_with_existing_query_params(monkeypatch):
    result = add_affiliate_tag(
        "https://example.com/book?flight=HAM-PMI&date=2026-10-02", tag="triphunter123"
    )

    assert result.startswith("https://example.com/book?")
    assert "flight=HAM-PMI" in result
    assert "date=2026-10-02" in result
    assert "tp_aff=triphunter123" in result


def test_tag_is_appended_before_a_url_fragment(monkeypatch):
    result = add_affiliate_tag("https://example.com/book#details", tag="triphunter123")

    assert result == "https://example.com/book?tp_aff=triphunter123#details"


def test_tag_value_is_url_encoded(monkeypatch):
    result = add_affiliate_tag("https://example.com/book", tag="a b&c")

    assert "tp_aff=a+b%26c" in result


def test_explicit_tag_overrides_environment(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "env-tag")

    result = add_affiliate_tag("https://example.com/book", tag="explicit-tag")

    assert "explicit-tag" in result
    assert "env-tag" not in result


def test_env_tag_used_when_no_explicit_tag_given(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "env-tag")

    result = add_affiliate_tag("https://example.com/book")

    assert "tp_aff=env-tag" in result
