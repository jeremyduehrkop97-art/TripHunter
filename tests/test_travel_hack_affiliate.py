"""monetization/travel_hack_affiliate.py - env-driven per-tip affiliate links."""

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

_VARS = ("ESIM_AFFILIATE_URL", "FLIGHT_COMPENSATION_AFFILIATE_URL", "SKIP_LINE_TICKETS_AFFILIATE_URL")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_esim_url_prefers_the_configured_link(monkeypatch):
    monkeypatch.setenv("ESIM_AFFILIATE_URL", "https://www.airalo.com/ref/abc")
    assert esim_affiliate_url() == "https://www.airalo.com/ref/abc"


def test_esim_url_falls_back_to_the_plain_site():
    assert esim_affiliate_url() == DEFAULT_ESIM_URL
    assert DEFAULT_ESIM_URL.startswith("https://")


def test_flight_compensation_url_prefers_the_configured_link(monkeypatch):
    monkeypatch.setenv("FLIGHT_COMPENSATION_AFFILIATE_URL", "https://www.airhelp.com/ref/abc")
    assert flight_compensation_affiliate_url() == "https://www.airhelp.com/ref/abc"


def test_flight_compensation_url_falls_back_to_the_plain_site():
    assert flight_compensation_affiliate_url() == DEFAULT_FLIGHT_COMPENSATION_URL
    assert DEFAULT_FLIGHT_COMPENSATION_URL.startswith("https://")


def test_skip_line_tickets_url_prefers_the_configured_link(monkeypatch):
    monkeypatch.setenv("SKIP_LINE_TICKETS_AFFILIATE_URL", "https://www.tiqets.com/ref/abc")
    assert skip_line_tickets_affiliate_url() == "https://www.tiqets.com/ref/abc"


def test_skip_line_tickets_url_falls_back_to_the_plain_site():
    assert skip_line_tickets_affiliate_url() == DEFAULT_SKIP_LINE_TICKETS_URL
    assert DEFAULT_SKIP_LINE_TICKETS_URL.startswith("https://")


@pytest.mark.parametrize("bad", ["http://insecure.example", "javascript:alert(1)", "tiqets.com/abc", "  "])
def test_an_insecure_or_malformed_configured_link_is_ignored(monkeypatch, bad):
    monkeypatch.setenv("SKIP_LINE_TICKETS_AFFILIATE_URL", bad)
    assert skip_line_tickets_affiliate_url() == DEFAULT_SKIP_LINE_TICKETS_URL
