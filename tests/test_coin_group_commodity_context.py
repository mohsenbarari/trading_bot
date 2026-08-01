from __future__ import annotations

import unittest
import json
import sqlite3

from core.market_intelligence.group_commodity_context import (
    commodity_context_requires_abstention,
    resolve_offer_commodity,
)
from scripts.coin_intelligence_private_ingest.run_rules_extractor import (
    apply_contextual_resolution,
)


def offer(
    commodity: str,
    price: int,
    *,
    method: str = "price_inference",
    at: float = 0.0,
) -> dict:
    return {
        "commodity": commodity,
        "commodity_method": method,
        "price": price,
        "price_method": "full",
        "settlement": "TOMORROW",
        "trade_form": "PHYSICAL",
        "confidence": 0.98 if method == "explicit" else 0.92,
        "event_epoch": at,
    }


class GroupCommodityContextTests(unittest.TestCase):
    def test_reply_inherits_compatible_explicit_parent(self) -> None:
        current = offer("امام", 181_900)
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            parent_offers=[offer("بهار", 181_500, method="explicit", at=100)],
        )
        self.assertEqual(resolved["commodity"], "بهار")
        self.assertEqual(resolved["commodity_method"], "reply_parent_explicit")

    def test_distant_reply_parent_cannot_override_price_context(self) -> None:
        current = offer("امام", 181_900)
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            parent_offers=[offer("امام", 186_000, method="explicit", at=100)],
            prior_offers=[offer("بهار", 181_500, method="explicit", at=100)],
        )
        self.assertEqual(resolved["commodity"], "بهار")
        self.assertEqual(resolved["commodity_method"], "local_market_price_anchor")

    def test_price_selects_bahar_without_reply_from_live_market_anchors(self) -> None:
        current = offer("امام", 181_900)
        anchors = [
            offer("بهار", 181_500, method="explicit", at=100),
            offer("امام", 185_800, at=80),
            offer("امام", 186_100, at=90),
            offer("امام", 185_900, at=110),
        ]
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            prior_offers=anchors,
        )
        self.assertEqual(resolved["commodity"], "بهار")
        self.assertEqual(resolved["commodity_method"], "local_market_price_anchor")
        self.assertEqual(resolved["commodity_evidence"]["center"], 181_500)

    def test_nearby_imam_cluster_keeps_an_unnamed_imam_offer(self) -> None:
        current = offer("امام", 185_950)
        anchors = [
            offer("بهار", 181_500, method="explicit", at=100),
            offer("امام", 185_800, at=80),
            offer("امام", 186_100, at=90),
            offer("امام", 185_900, at=110),
        ]
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            prior_offers=anchors,
        )
        self.assertEqual(resolved["commodity"], "امام")
        self.assertEqual(resolved["commodity_method"], "local_market_price_anchor")

    def test_future_anchor_is_never_used(self) -> None:
        current = offer("امام", 181_900)
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            prior_offers=[offer("بهار", 181_900, method="explicit", at=201)],
        )
        self.assertEqual(resolved["commodity"], "امام")
        self.assertEqual(resolved["commodity_method"], "price_inference")

    def test_single_implicit_anchor_cannot_override_static_fallback(self) -> None:
        current = offer("امام", 181_900)
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            prior_offers=[offer("بهار", 181_900, at=100)],
        )
        self.assertEqual(resolved["commodity"], "امام")

    def test_explicit_typo_is_not_rewritten_but_is_blocked(self) -> None:
        current = offer("امام", 181_900, method="explicit")
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            prior_offers=[
                offer("بهار", 181_500, method="explicit", at=100),
                offer("امام", 185_800, at=80),
                offer("امام", 186_100, at=90),
                offer("امام", 185_900, at=110),
            ],
        )
        self.assertEqual(resolved["commodity"], "امام")
        self.assertEqual(
            resolved["commodity_validation_status"],
            "EXPLICIT_PRICE_CONTEXT_CONFLICT",
        )
        self.assertTrue(commodity_context_requires_abstention(resolved))

    def test_consistent_explicit_commodity_passes_price_validation(self) -> None:
        current = offer("امام", 185_950, method="explicit")
        resolved = resolve_offer_commodity(
            current,
            as_of_epoch=200,
            prior_offers=[
                offer("امام", 185_800, at=80),
                offer("امام", 186_100, at=90),
                offer("امام", 185_900, at=110),
            ],
        )
        self.assertEqual(resolved["commodity"], "امام")
        self.assertEqual(
            resolved["commodity_validation_status"], "PRICE_CONTEXT_CONSISTENT"
        )
        self.assertFalse(commodity_context_requires_abstention(resolved))

    def test_explicit_price_outside_plausible_range_is_blocked(self) -> None:
        resolved = resolve_offer_commodity(
            offer("ربع بهار", 181_900, method="explicit"),
            as_of_epoch=200,
        )
        self.assertEqual(
            resolved["commodity_validation_status"],
            "EXPLICIT_PRICE_CONTEXT_CONFLICT",
        )
        self.assertTrue(commodity_context_requires_abstention(resolved))

    def test_unnamed_overlap_without_context_abstains(self) -> None:
        resolved = resolve_offer_commodity(
            offer("امام", 181_900),
            as_of_epoch=200,
        )
        self.assertEqual(resolved["commodity_validation_status"], "AMBIGUOUS_PRICE_CONTEXT")
        self.assertTrue(commodity_context_requires_abstention(resolved))

    def test_staging_pass_corrects_reply_and_standalone_price_context(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE text_candidates(
                source_key TEXT, message_id TEXT, telegram_datetime TEXT,
                telegram_day TEXT, reply_message_id TEXT, extracted_json TEXT,
                extraction_confidence REAL, updated_at_utc TEXT,
                PRIMARY KEY(source_key,message_id)
            )"""
        )

        def insert(message_id: str, at: int, item: dict, reply: str | None = None) -> None:
            connection.execute(
                "INSERT INTO text_candidates VALUES(?,?,?,?,?,?,?,?)",
                (
                    "account2_group1",
                    message_id,
                    f"2026-08-01T17:{at:02d}:00Z",
                    None,
                    reply,
                    json.dumps({"offers": [item]}, ensure_ascii=False),
                    item["confidence"],
                    "before",
                ),
            )

        insert("1", 1, offer("امام", 185_800))
        insert("2", 2, offer("امام", 186_100))
        insert("3", 3, offer("امام", 185_900))
        insert("4", 4, offer("بهار", 181_500, method="explicit"))
        insert("5", 5, offer("امام", 181_900))
        insert("6", 6, offer("امام", 181_950), reply="4")
        connection.commit()

        self.assertGreaterEqual(apply_contextual_resolution(connection), 2)
        rows = connection.execute(
            "SELECT message_id,extracted_json FROM text_candidates WHERE message_id IN ('5','6') ORDER BY message_id"
        ).fetchall()
        standalone = json.loads(rows[0]["extracted_json"])["offers"][0]
        reply = json.loads(rows[1]["extracted_json"])["offers"][0]
        self.assertEqual(standalone["commodity"], "بهار")
        self.assertEqual(standalone["commodity_method"], "local_market_price_anchor")
        self.assertEqual(reply["commodity"], "بهار")
        self.assertEqual(reply["commodity_method"], "reply_parent_explicit")


if __name__ == "__main__":
    unittest.main()
