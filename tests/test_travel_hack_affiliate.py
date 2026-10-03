"""monetization/travel_hack_affiliate.py - env-driven per-tip affiliate
links, defaulting to a Travelpayouts-marker-wrapped link (Kiwi.com,
Airalo, Yesim, AirHelp, Tiqets and Klook are all real Travelpayouts
programs - see the module's own docstring)."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

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


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


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


# --- no override, no marker: plain, untracked default ---------------------------


def test_esim_url_falls_back_to_the_plain_site_with_no_marker():
    assert esim_affiliate_url() == DEFAULT_ESIM_URL
    assert DEFAULT_ESIM_URL.startswith("https://")


def test_flight_compensation_url_falls_back_to_the_plain_site_with_no_marker():
    assert flight_compensation_affiliate_url() == DEFAULT_FLIGHT_COMPENSATION_URL


def test_skip_line_tickets_url_falls_back_to_the_plain_site_with_no_marker():
    assert skip_line_tickets_affiliate_url() == DEFAULT_SKIP_LINE_TICKETS_URL


# --- no override, a marker IS set: wrapped via Travelpayouts ---------------------


def test_esim_url_is_wrapped_via_travelpayouts_when_a_marker_is_set(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    url = esim_affiliate_url()

    assert urlsplit(url).netloc == "c111.travelpayouts.com"
    query = _query(url)
    assert query["shmarker"] == ["781828"]
    assert unquote(query["custom_url"][0]) == DEFAULT_ESIM_URL


def test_flight_compensation_url_is_wrapped_via_travelpayouts_when_a_marker_is_set(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    url = flight_compensation_affiliate_url()

    assert urlsplit(url).netloc == "c111.travelpayouts.com"
    assert unquote(_query(url)["custom_url"][0]) == DEFAULT_FLIGHT_COMPENSATION_URL


def test_skip_line_tickets_url_is_wrapped_via_travelpayouts_when_a_marker_is_set(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    url = skip_line_tickets_affiliate_url()

    assert urlsplit(url).netloc == "c111.travelpayouts.com"
    assert unquote(_query(url)["custom_url"][0]) == DEFAULT_SKIP_LINE_TICKETS_URL


def test_all_three_tips_share_the_same_marker(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    markers = {
        _query(esim_affiliate_url())["shmarker"][0],
        _query(flight_compensation_affiliate_url())["shmarker"][0],
        _query(skip_line_tickets_affiliate_url())["shmarker"][0],
    }
    assert markers == {"781828"}
