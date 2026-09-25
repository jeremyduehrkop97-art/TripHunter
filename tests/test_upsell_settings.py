"""monetization/upsell.py - env-driven settings of the Free-channel funnel."""

from __future__ import annotations

import pytest

from trip_hunter.monetization.upsell import (
    DEFAULT_DELAY_HOURS,
    LANDING_PAGE_URL,
    faq_url,
    free_channel_delay_hours,
    free_channel_mode,
    vip_subscription_url,
)

_VARS = ("VIP_SUBSCRIPTION_URL", "TELEGRAM_BOT_USERNAME", "FAQ_URL", "FREE_CHANNEL_MODE", "FREE_CHANNEL_DELAY_HOURS")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_vip_url_prefers_the_configured_url(monkeypatch):
    monkeypatch.setenv("VIP_SUBSCRIPTION_URL", "https://buy.stripe.com/abc")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "TripHunterBot")
    assert vip_subscription_url() == "https://buy.stripe.com/abc"


def test_vip_url_falls_back_to_the_bot_start_link(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@TripHunterBot")
    assert vip_subscription_url() == "https://t.me/TripHunterBot?start=vip"


def test_vip_url_last_fallback_is_the_landing_page_pricing():
    assert vip_subscription_url() == LANDING_PAGE_URL + "#pricing"


@pytest.mark.parametrize("bad", ["http://insecure.example", "javascript:alert(1)", "buy.stripe.com/abc", "https://a b.example", "  "])
def test_non_https_or_malformed_vip_urls_are_ignored(monkeypatch, bad):
    monkeypatch.setenv("VIP_SUBSCRIPTION_URL", bad)
    assert vip_subscription_url() == LANDING_PAGE_URL + "#pricing"


@pytest.mark.parametrize("bad", ["", "abc", "has space", "x" * 40, "bad-char!"])
def test_invalid_bot_names_are_ignored(monkeypatch, bad):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", bad)
    assert vip_subscription_url() == LANDING_PAGE_URL + "#pricing"


def test_faq_url_configured_and_default(monkeypatch):
    assert faq_url() == LANDING_PAGE_URL + "#how"
    monkeypatch.setenv("FAQ_URL", "https://example.org/faq")
    assert faq_url() == "https://example.org/faq"
    monkeypatch.setenv("FAQ_URL", "http://example.org/faq")
    assert faq_url() == LANDING_PAGE_URL + "#how"


def test_mode_defaults_to_teaser_and_only_accepts_the_two_values(monkeypatch):
    assert free_channel_mode() == "teaser"
    monkeypatch.setenv("FREE_CHANNEL_MODE", " Delayed_Full ")
    assert free_channel_mode() == "delayed_full"
    monkeypatch.setenv("FREE_CHANNEL_MODE", "teaser")
    assert free_channel_mode() == "teaser"
    monkeypatch.setenv("FREE_CHANNEL_MODE", "nonsense")
    assert free_channel_mode() == "teaser"


@pytest.mark.parametrize("value, expected", [("6", 6), ("48", 48), ("0", 1), ("-3", 1), ("abc", DEFAULT_DELAY_HOURS), ("", DEFAULT_DELAY_HOURS)])
def test_delay_hours(monkeypatch, value, expected):
    monkeypatch.setenv("FREE_CHANNEL_DELAY_HOURS", value)
    assert free_channel_delay_hours() == expected


def test_default_delay_is_24_hours():
    assert free_channel_delay_hours() == 24
