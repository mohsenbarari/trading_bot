from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.conversation_quality import (
    annotate_database,
    negotiated_tail_on_anchored_offer,
)


class ConversationQualityTests(unittest.TestCase):
    def test_linked_negotiated_tail_is_eligible_only_near_an_anchored_offer(self) -> None:
        linked = {"price": 183_100, "price_method": "full"}
        self.assertTrue(
            negotiated_tail_on_anchored_offer(
                {"price": 182_800, "price_method": "reply_contextual_tail"},
                linked,
            )
        )
        self.assertFalse(
            negotiated_tail_on_anchored_offer(
                {"price": 172_000, "price_method": "reply_contextual_tail"},
                linked,
            )
        )

    def test_annotation_keeps_linked_negotiation_and_rejects_settlement_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            market_path = Path(directory) / "market.sqlite3"
            conversation = sqlite3.connect(conversation_path)
            conversation.executescript(
                """
                CREATE TABLE messages(
                    import_id INTEGER, message_id INTEGER, event_time_utc TEXT,
                    sender_hash TEXT, text TEXT
                );
                CREATE TABLE offers(
                    id INTEGER PRIMARY KEY, import_id INTEGER, message_id INTEGER,
                    offer_index INTEGER, commodity TEXT, price INTEGER, quantity INTEGER,
                    side TEXT, settlement TEXT, trade_form TEXT, confidence REAL,
                    price_method TEXT, source_text TEXT
                );
                CREATE TABLE confirmed_trades(
                    id INTEGER PRIMARY KEY, import_id INTEGER, offer_message_id INTEGER,
                    event_time_utc TEXT, commodity TEXT, price INTEGER, quantity INTEGER,
                    side TEXT, settlement TEXT, trade_form TEXT, price_method TEXT,
                    training_eligible INTEGER
                );
                INSERT INTO messages VALUES
                    (1, 10, '2026-08-04T10:00:00Z', 'owner-a', 'خ ن ف امام 10 تا 183100'),
                    (1, 20, '2026-08-04T10:01:00Z', 'owner-b', '10 تا نق خ 183200');
                INSERT INTO offers VALUES
                    (1, 1, 10, 0, 'امام', 183100, 10, 'BUY', 'TOMORROW', 'PHYSICAL', 0.99, 'full', NULL),
                    (2, 1, 20, 0, 'امام', 183200, 10, 'BUY', 'TOMORROW', 'PHYSICAL', 0.99, 'full', NULL);
                INSERT INTO confirmed_trades VALUES
                    (1, 1, 10, '2026-08-04T10:00:05Z', 'امام', 182800, 5, 'BUY', 'TOMORROW', 'PHYSICAL', 'reply_contextual_tail', 1),
                    (2, 1, 20, '2026-08-04T10:01:05Z', 'امام', 183200, 5, 'BUY', 'TOMORROW', 'PHYSICAL', 'full', 1);
                """
            )
            conversation.commit()
            conversation.close()
            market = sqlite3.connect(market_path)
            market.executescript(
                """
                CREATE TABLE price_events(
                    id INTEGER PRIMARY KEY, instrument TEXT, event_time_utc TEXT,
                    price_num REAL, settlement_term TEXT, trade_form TEXT
                );
                CREATE TABLE external_market_observations(
                    id INTEGER PRIMARY KEY, instrument_code TEXT, quote_kind TEXT,
                    observed_at_utc TEXT, normalized_price_num REAL
                );
                """
            )
            market.commit()
            market.close()

            annotate_database(conversation_path, market_path)
            checked = sqlite3.connect(conversation_path)
            checked.row_factory = sqlite3.Row
            trades = checked.execute(
                "SELECT trade_id, training_eligible, exclusion_reason FROM trade_market_quality ORDER BY trade_id"
            ).fetchall()
            offers = checked.execute(
                "SELECT offer_id, training_eligible, exclusion_reason FROM offer_market_quality ORDER BY offer_id"
            ).fetchall()
            checked.close()
            self.assertEqual(
                [(row["trade_id"], row["training_eligible"], row["exclusion_reason"]) for row in trades],
                [(1, 1, None), (2, 0, "SETTLEMENT_LABEL_TOMORROW_BUT_TEXT_CASH")],
            )
            # The first offer is superseded by its confirmed trade; the second
            # is excluded because «نق» is cash, not tomorrow.
            self.assertEqual(offers[0]["exclusion_reason"], "SUPERSEDED_BY_CONFIRMED_TRADE")
            self.assertEqual(
                offers[1]["exclusion_reason"],
                "SETTLEMENT_LABEL_TOMORROW_BUT_TEXT_CASH",
            )
        self.assertFalse(
            negotiated_tail_on_anchored_offer(
                {"price": 182_800, "price_method": "reply_contextual_tail"},
                None,
            )
        )


if __name__ == "__main__":
    unittest.main()
