"""One-off manual dual-channel verification: sends the built-in mocked
COMBINED_TRIP_DROP deal through dispatch_deal_alert (Free/VIP routing,
see dispatch/telegram.py) so a real GitHub Actions/local Telegram setup
can be verified live end-to-end - useful right after configuring
TELEGRAM_FREE_CHAT_ID / TELEGRAM_VIP_CHAT_ID, or whenever a daily run
skipped everything ("Cache active -> skipped") and there's nothing fresh
to alert on.

Reuses dispatch/test_dispatch.py's _mock_deal() (already a
COMBINED_TRIP_DROP deal with a flight + accommodation) rather than
duplicating the mock Deal construction - see that module's docstring for
why it's the canonical docs/PRODUCT_SPEC.md example scenario.

Run with:
    python -m scripts.send_test_alert              # sends to whatever's configured
    python -m scripts.send_test_alert --dry-run     # prints both payloads, sends nothing

0 SerpApi credits either way (the deal is mocked, no provider is called).
A live (non-dry-run) run DOES send real Telegram messages to whichever of
TELEGRAM_FREE_CHAT_ID / TELEGRAM_VIP_CHAT_ID / TRIP_HUNTER_TELEGRAM_CHAT_ID
is configured - see dispatch_deal_alert's docstring for the exact routing.
"""

from __future__ import annotations

import argparse

from trip_hunter.alerts.instant_alert_formatter import format_instant_alert, format_teaser_alert
from trip_hunter.dispatch import telegram
from trip_hunter.dispatch.test_dispatch import _mock_deal


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.send_test_alert",
        description=(
            "Send the mocked COMBINED_TRIP_DROP deal through dispatch_deal_alert "
            "to verify the Free/VIP Telegram channels are wired up correctly."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print both channel payloads instead of sending them."
    )
    return parser


def run(argv: list[str] | None = None) -> bool | None:
    """Returns dispatch_deal_alert(...)'s result (True/False), or None if
    --dry-run was passed (nothing was sent)."""
    args = _build_parser().parse_args(argv)
    deal = _mock_deal()

    if args.dry_run:
        print("DRY RUN - Payloads (werden NICHT gesendet):")
        print(f"  Free-Kanal ({telegram.get_free_chat_id() or '<nicht konfiguriert>'}):")
        print(format_teaser_alert(deal))
        print()
        print(f"  VIP-Kanal ({telegram.get_vip_chat_id() or '<nicht konfiguriert>'}):")
        print(format_instant_alert(deal))
        print()
        default_chat_id = telegram.get_chat_id()
        if not telegram.get_free_chat_id() and not telegram.get_vip_chat_id():
            print(f"  Fallback-Kanal (TRIP_HUNTER_TELEGRAM_CHAT_ID): {default_chat_id or '<nicht konfiguriert>'}")
        return None

    print("Sende gemockten COMBINED_TRIP_DROP-Deal via dispatch_deal_alert ...")
    result = telegram.dispatch_deal_alert(deal)
    print(f"Ergebnis: {'mindestens ein Kanal erfolgreich' if result else 'kein Kanal erfolgreich'}")
    return result


if __name__ == "__main__":
    run()
