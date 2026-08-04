"""Read-only product-neutral ranking tests for published coin-rate snapshots."""

from __future__ import annotations

import copy
import unittest

from core.market_intelligence.coin_inference import infer_coin_commodity
from core.market_intelligence.coin_rate_engine import COIN_RATE_ENGINE_VERSION, COIN_SPECS


def snapshot() -> dict:
    items = []
    for settlement in ("CASH", "TOMORROW"):
        for code in COIN_SPECS:
            items.append(
                {
                    "commodity_code": code,
                    "settlement_term": settlement,
                    "status": "NO_DATA",
                    "estimated_project_price": None,
                    "lower_project_price": None,
                    "upper_project_price": None,
                    "confidence": "NONE",
                    "method": "test",
                    "underlying_source": None,
                    "anchor_age_seconds": None,
                    "market_regime": "NORMAL",
                    "reason": "test",
                }
            )
    return {
        "schema_version": 1,
        "market_store_contract_version": 1,
        "builder_version": "test",
        "generated_at_utc": "2026-08-04T10:00:00Z",
        "signals": {},
        "market_regime": {},
        "snapshot_status": "PARTIAL_COIN_RATE_STATE",
        "rates": {"engine_version": COIN_RATE_ENGINE_VERSION, "items": items, "estimated_count": 0, "no_data_count": len(items)},
    }


def set_rate(target: dict, code: str, settlement: str, center: int, lower: int, upper: int, confidence: str = "HIGH") -> None:
    item = next(row for row in target["rates"]["items"] if row["commodity_code"] == code and row["settlement_term"] == settlement)
    item.update(status="ESTIMATED", estimated_project_price=center, lower_project_price=lower, upper_project_price=upper, confidence=confidence, reason=None)
    target["rates"]["estimated_count"] += 1
    target["rates"]["no_data_count"] -= 1


class CoinInferenceTests(unittest.TestCase):
    def test_unique_high_confidence_range_auto_selects_canonical_name(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "TOMORROW", 186_900, 185_500, 188_300)
        result = infer_coin_commodity(value, price_project_thousand_toman=186_800, settlement_term="TOMORROW", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual((result.status, result.candidates[0].commodity_code, result.candidates[0].commodity_name), ("AUTO_SELECT", "IMAM", "امام"))
        self.assertEqual(len(result.snapshot_receipt), 64)

    def test_overlapping_ranges_require_confirmation_not_hidden_default(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "TOMORROW", 186_900, 185_000, 188_500)
        set_rate(value, "BAHAR", "TOMORROW", 186_700, 185_500, 187_900)
        result = infer_coin_commodity(value, price_project_thousand_toman=186_800, settlement_term="TOMORROW", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual((result.status, [item.commodity_code for item in result.candidates]), ("CONFIRM", ["IMAM", "BAHAR"]))

    def test_low_confidence_paper_fallback_requires_confirmation(self) -> None:
        value = snapshot()
        set_rate(value, "BAHAR", "CASH", 180_900, 179_700, 182_100, confidence="LOW_PAPER_FALLBACK")
        result = infer_coin_commodity(value, price_project_thousand_toman=180_900, settlement_term="CASH", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual(result.status, "CONFIRM")

    def test_stale_or_out_of_range_snapshot_abstains(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "TOMORROW", 186_900, 185_500, 188_300)
        stale = infer_coin_commodity(value, price_project_thousand_toman=186_900, settlement_term="TOMORROW", now_utc="2026-08-04T10:03:00Z")
        outside = infer_coin_commodity(value, price_project_thousand_toman=190_000, settlement_term="TOMORROW", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual((stale.status, stale.reason), ("ABSTAIN", "SNAPSHOT_STALE_OR_FUTURE"))
        self.assertEqual((outside.status, outside.reason), ("ABSTAIN", "PRICE_OUTSIDE_PUBLISHED_RANGES"))


if __name__ == "__main__":
    unittest.main()
