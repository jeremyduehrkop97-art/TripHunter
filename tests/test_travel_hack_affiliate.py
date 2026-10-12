"""monetization/travel_hack_affiliate.py - env-driven per-tip affiliate
links. Each tip is EITHER its configured override URL, used exactly as
given, OR its plain, always-working public site - TRAVELPAYOUTS_MARKER
has no effect here at all any more (see the module's own "CORRECTION #2"
docstring: a real click on the generic Travelpayouts click-redirect this
used to wrap these in failed live with "promo_id is empty")."""

from __future__ import annotations

import pytest

from trip_hunter.monetization.travel_hack_affiliate import (
    DEFAULT_ESIM_URL,
    DEFAULT_FLIGHT_COMPENSATION_URL,
    DEFAULT_SKIP_LINE_TICKETS_URL,
    esim_affiliate_url,
    flight_compensation_affiliate_url,
    skip_line_tickets_affiliate_url,
)

_OVERRIDE_VARS = ("ESIM_AFFILIATE_URL", "FLIGHT_COMPENSATION_AFFILIATE_URL", "SKIP_LINE_TICKETS_AFFILIATE_URL")
_ALL_VARS = (*_OVERRIDE_VARS, "TRAVELPAYOUTS_MARKER")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


# --- explicit override always wins, never re-wrapped ----------------------------


def test_esim_url_prefers_the_configured_link(monkeypatch):
    monkeypatch.setenv("ESIM_AFFILIATE_URL", "https://www.airalo.com/ref/abc")
    assert esim_affiliate_url() == "https://www.airalo.com/ref/abc"


def test_flight_compensation_url_prefers_the_configured_link(monkeypatch):
    monkeypatch.setenv("FLIGHT_COMPENSATION_AFFILIATE_URL", "https://www.airhelp.com/ref/abc")
    assert flight_compensation_affiliate_url() == "https://www.airhelp.com/ref/abc"


def test_skip_line_tickets_url_prefers_the_configured_link(monkeypatch):
    monkeypatch.setenv("SKIP_LINE_TICKETS_AFFILIATE_URL", "https://www.tiqets.com/ref/abc")
    assert skip_line_tickets_affiliate_url() == "https://www.tiqets.com/ref/abc"


def test_configured_override_is_not_wrapped_even_with_a_marker_set(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    monkeypatch.setenv("SKIP_LINE_TICKETS_AFFILIATE_URL", "https://www.tiqets.com/ref/abc")
    assert skip_line_tickets_affiliate_url() == "https://www.tiqets.com/ref/abc"


@pytest.mark.parametrize("bad", ["http://insecure.example", "javascript:alert(1)", "tiqets.com/abc", "  "])
def test_an_insecure_or_malformed_configured_link_is_ignored(monkeypatch, bad):
    monkeypatch.setenv("SKIP_LINE_TICKETS_AFFILIATE_URL", bad)
    assert skip_line_tickets_affiliate_url() == DEFAULT_SKIP_LINE_TICKETS_URL


# --- no override: always the plain, always-working default - with or without a marker ---


def test_esim_url_falls_back_to_the_plain_site_with_no_marker():
    assert esim_affiliate_url() == DEFAULT_ESIM_URL
    assert DEFAULT_ESIM_URL.startswith("https://")


def test_flight_compensation_url_falls_back_to_the_plain_site_with_no_marker():
    assert flight_compensation_affiliate_url() == DEFAULT_FLIGHT_COMPENSATION_URL


def test_skip_line_tickets_url_falls_back_to_the_plain_site_with_no_marker():
    assert skip_line_tickets_affiliate_url() == DEFAULT_SKIP_LINE_TICKETS_URL


def test_a_travelpayouts_marker_has_no_effect_on_any_of_the_three_tips(monkeypatch):
    """REMOVED (verified live, 2026-10): these used to wrap the plain
    site in Travelpayouts' generic click-redirect under this marker - a
    real click on that redirect failed with "promo_id is empty", so it
    was removed entirely. A marker being set must never change any of
    these three tips' URLs any more."""
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")

    assert esim_affiliate_url() == DEFAULT_ESIM_URL
    assert flight_compensation_affiliate_url() == DEFAULT_FLIGHT_COMPENSATION_URL
    assert skip_line_tickets_affiliate_url() == DEFAULT_SKIP_LINE_TICKETS_URL
    for url in (esim_affiliate_url(), flight_compensation_affiliate_url(), skip_line_tickets_affiliate_url()):
        assert "travelpayouts" not in url.lower() and "781828" not in url
