"""Guards that the old raw GitHub Pages URL never sneaks back in anywhere
that ships to users or Telegram - the live custom domain (trip-hunter.de,
confirmed reachable and serving the real site) is the only one that
should ever appear as a hardcoded fallback."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parent.parent
_LEGACY_DOMAIN = "jeremyduehrkop97-art.github.io"

# Source, web assets and GitHub workflow files - not this test file itself
# (which legitimately names the legacy domain to check for it) and not
# other tests (many kept a historical reference in a comment/description
# while this task was being carried out, which is fine - it's the
# SHIPPED artifacts and the actual fallback constants that must be clean).
_SCAN_DIRS = ("src", "web", ".github")
_TEXT_SUFFIXES = {".py", ".html", ".yml", ".yaml", ".md"}


def _shipped_files():
    for directory in _SCAN_DIRS:
        base = _ROOT / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in _TEXT_SUFFIXES:
                yield path


def test_no_shipped_file_hardcodes_the_old_github_pages_url():
    offenders = [
        str(path.relative_to(_ROOT))
        for path in _shipped_files()
        if _LEGACY_DOMAIN in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []


def test_default_fallback_urls_are_the_live_custom_domain():
    from trip_hunter.monetization.link_builder import DEFAULT_DEAL_SHEET_URL
    from trip_hunter.monetization.upsell import LANDING_PAGE_URL

    assert LANDING_PAGE_URL == "https://trip-hunter.de/"
    assert DEFAULT_DEAL_SHEET_URL == "https://trip-hunter.de/deal.html"
