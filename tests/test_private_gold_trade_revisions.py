"""Causal rules for private melted-gold trade revisions."""

from __future__ import annotations

import unittest

from core.market_intelligence.private_gold_trade_revisions import (
    PRIVATE_GOLD_OFFER_LIFETIME_SECONDS,
    PrivateGoldRevision,
    extract_private_gold_trade,
)


def revision(
    event_id: str,
    text: str,
    *,
    edited: str | None = None,
    available: str | None = None,
    event_type: str | None = None,
) -> PrivateGoldRevision:
    return PrivateGoldRevision(
        event_id=event_id,
        event_type=event_type or ("message_edited" if edited else "message_created"),
        published_at_utc="2026-08-25T10:00:00Z",
        available_at_utc=available or edited or "2026-08-25T10:00:01Z",
        edited_at_utc=edited,
        text=text,
    )


class PrivateGoldTradeRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.offer = "95,000,000 فروش 10 تا بدون حواله"

    def test_partial_is_pending_until_window_closes_then_uses_offer_minus_remaining(self) -> None:
        rows = (
            revision("created", self.offer),
            revision(
                "partial",
                self.offer + " باقی 6",
                edited="2026-08-25T10:00:40Z",
                available="2026-08-25T10:00:41Z",
            ),
        )
        pending = extract_private_gold_trade(rows, as_of_utc="2026-08-25T10:01:00Z")
        self.assertEqual((pending.status, pending.finalized), ("PENDING", False))
        final = extract_private_gold_trade(rows, as_of_utc="2026-08-25T10:02:01Z")
        self.assertEqual(
            (final.status, final.traded_quantity, final.remaining_quantity, final.reason),
            ("PARTIAL", 4, 6, "EXPLICIT_REMAINING_DELTA"),
        )

    def test_no_trade_closure_overrides_tentative_remaining_evidence(self) -> None:
        result = extract_private_gold_trade(
            (
                revision("created", self.offer),
                revision("partial", self.offer + " باقی 6", edited="2026-08-25T10:00:30Z"),
                revision("closed", self.offer + " ✅", edited="2026-08-25T10:01:30Z"),
            ),
            as_of_utc="2026-08-25T10:01:31Z",
        )
        self.assertEqual((result.status, result.finalized), ("NONE", True))
        self.assertEqual(result.reason, "EXPLICIT_NO_TRADE_CLOSURE")

    def test_late_remaining_edit_is_not_a_trade(self) -> None:
        result = extract_private_gold_trade(
            (
                revision("created", self.offer),
                revision("late", self.offer + " باقی 6", edited="2026-08-25T10:02:01Z"),
            ),
            as_of_utc="2026-08-25T10:03:00Z",
        )
        self.assertEqual((result.status, result.traded_quantity), ("NONE", None))

    def test_zero_remaining_is_terminal_full_trade(self) -> None:
        result = extract_private_gold_trade(
            (
                revision("created", self.offer),
                revision(
                    "full",
                    self.offer + " باقی 0",
                    edited="2026-08-25T10:00:20Z",
                    available="2026-08-25T10:00:21Z",
                ),
            ),
            as_of_utc="2026-08-25T10:00:21Z",
        )
        self.assertEqual(
            (result.status, result.finalized, result.traded_quantity, result.remaining_quantity),
            ("FULL", True, 10, 0),
        )

    def test_existing_remaining_marker_must_decrease_on_an_edit(self) -> None:
        result = extract_private_gold_trade(
            (
                revision("created", self.offer + " باقی 8"),
                revision("more", self.offer + " باقی 5", edited="2026-08-25T10:00:50Z"),
            ),
            as_of_utc="2026-08-25T10:02:01Z",
        )
        self.assertEqual((result.status, result.traded_quantity), ("PARTIAL", 5))

    def test_price_or_book_change_with_remaining_fails_closed(self) -> None:
        result = extract_private_gold_trade(
            (
                revision("created", self.offer),
                revision(
                    "ambiguous",
                    "96,000,000 فروش 10 تا بدون حواله باقی 6",
                    edited="2026-08-25T10:00:50Z",
                ),
            ),
            as_of_utc="2026-08-25T10:02:01Z",
        )
        self.assertEqual((result.status, result.finalized), ("AMBIGUOUS", True))

    def test_contract_lifetime_is_two_minutes(self) -> None:
        self.assertEqual(PRIVATE_GOLD_OFFER_LIFETIME_SECONDS, 120)


if __name__ == "__main__":
    unittest.main()
