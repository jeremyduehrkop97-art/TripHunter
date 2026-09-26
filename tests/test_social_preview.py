"""Open Graph / Twitter preview tags of the landing page and the deal sheet."""

from __future__ import annotations

import re
import struct
from html.parser import HTMLParser
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "web"
_IMAGE_URL = "https://jeremyduehrkop97-art.github.io/TripHunter/assets/trip-hunter-bot.jpg"


class _Metas(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        key = d.get("property") or d.get("name")
        if tag == "meta" and key and key.startswith(("og:", "twitter:")):
            self.tags[key] = d.get("content", "")


def _metas(page: str) -> dict[str, str]:
    parser = _Metas()
    parser.feed((_WEB / page).read_text(encoding="utf-8"))
    return parser.tags


@pytest.mark.parametrize("page", ["index.html", "deal.html"])
def test_open_graph_and_twitter_tags_are_complete(page):
    tags = _metas(page)

    assert tags["og:type"] == "website"
    assert tags["og:title"] == "Trip Hunter – Flug- & Hotel-Deals automatisch geprüft"
    assert tags["og:description"].startswith("Kombi-Radar für echte Flug- & Hotel-Schnäppchen.")
    assert tags["og:image"] == tags["twitter:image"] == _IMAGE_URL
    assert (tags["og:image:width"], tags["og:image:height"]) == ("1024", "1024")
    assert tags["twitter:card"] == "summary_large_image"


@pytest.mark.parametrize("page", ["index.html", "deal.html"])
def test_preview_image_urls_are_absolute_https(page):
    tags = _metas(page)
    for key in ("og:image", "twitter:image", "og:url"):
        assert tags[key].startswith("https://"), key  # WhatsApp/Telegram ignore relative paths


def test_the_preview_image_exists_with_the_declared_size_and_is_small_enough_for_whatsapp():
    image = _WEB / "assets" / "trip-hunter-bot.jpg"
    data = image.read_bytes()

    assert data[:3] == b"\xff\xd8\xff"  # JPEG
    assert len(data) < 300 * 1024  # larger og:images are dropped by WhatsApp
    # JPEG dimensions from the SOF marker
    i = 2
    while i < len(data):
        marker = data[i + 1]
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            break
        i += 2 + length
    assert (width, height) == (1024, 1024)


def test_the_image_url_points_at_the_file_that_is_deployed_from_web_assets():
    assert _IMAGE_URL.endswith("/TripHunter/assets/trip-hunter-bot.jpg")
    assert (_WEB / "assets" / "trip-hunter-bot.jpg").exists()
