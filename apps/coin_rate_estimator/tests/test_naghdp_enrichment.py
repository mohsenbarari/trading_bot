from __future__ import annotations

import sqlite3
import unittest

from telegram_price_collector.db import (
    infer_naghdp_trade_sides,
    initialize,
    replace_price_events,
    upsert_raw_post,
)
from telegram_price_collector.models import RawPost
from telegram_price_collector.parsers import parse_message


class NaghdpEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def add_message(self, message_id: int, published: str, text: str) -> int:
        raw_post_id = upsert_raw_post(
            self.connection,
            source_code="MELTED_FLOW",
            post=RawPost(
                message_id=message_id,
                published_at_utc=published,
                raw_text=text,
            ),
        )
        replace_price_events(
            self.connection,
            raw_post_id=raw_post_id,
            event_time_utc=published,
            events=parse_message("NaghdP", text),
        )
        return raw_post_id

    def test_trade_side_is_linked_to_latest_same_market_offer(self) -> None:
        self.add_message(
            1,
            "2026-07-21T07:00:00Z",
            "79,270,000⏳باحواله🔵خرید\nگرم: 18,299,552",
        )
        trade_post_id = self.add_message(
            2,
            "2026-07-21T07:00:30Z",
            "79,270,000⏳باحواله✅معامله\nگرم: 18,299,552",
        )

        result = infer_naghdp_trade_sides(
            self.connection,
            raw_post_id=trade_post_id,
        )
        trade = self.connection.execute(
            "SELECT side, parse_method, parse_confidence FROM price_events WHERE raw_post_id = ?",
            (trade_post_id,),
        ).fetchone()

        self.assertEqual(result, {"examined": 1, "matched": 1, "unresolved": 0})
        self.assertEqual(trade["side"], "BUY")
        self.assertEqual(trade["parse_method"], "RULE_CONTEXT")
        self.assertEqual(trade["parse_confidence"], 0.97)

    def test_trade_is_not_linked_to_stale_offer(self) -> None:
        self.add_message(
            1,
            "2026-07-21T07:00:00Z",
            "78,800,000☀️امروز🔴فروش\nگرم: 18,191,052",
        )
        trade_post_id = self.add_message(
            2,
            "2026-07-21T07:04:00Z",
            "78,800,000☀️امروز✅معامله\nگرم: 18,191,052",
        )

        result = infer_naghdp_trade_sides(
            self.connection,
            raw_post_id=trade_post_id,
        )
        side = self.connection.execute(
            "SELECT side FROM price_events WHERE raw_post_id = ?",
            (trade_post_id,),
        ).fetchone()["side"]

        self.assertEqual(result, {"examined": 1, "matched": 0, "unresolved": 1})
        self.assertEqual(side, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
