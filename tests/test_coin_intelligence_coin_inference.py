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

    def test_same_denomination_date_variants_can_require_confirmation(self) -> None:
        value = snapshot()
        set_rate(value, "HALF_BAHAR", "CASH", 94_600, 93_500, 95_700)
        set_rate(value, "HALF_LOW_DATE", "CASH", 94_200, 93_300, 95_100)
        result = infer_coin_commodity(value, price_project_thousand_toman=94_500, settlement_term="CASH", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual((result.status, [item.commodity_code for item in result.candidates]), ("CONFIRM", ["HALF_BAHAR", "HALF_LOW_DATE"]))

    def test_cross_denomination_overlap_abstains_instead_of_offering_an_implausible_choice(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "CASH", 186_900, 80_000, 190_000)
        set_rate(value, "HALF_BAHAR", "CASH", 94_600, 93_500, 95_700)
        result = infer_coin_commodity(value, price_project_thousand_toman=94_500, settlement_term="CASH", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual((result.status, result.reason, result.candidates), ("ABSTAIN", "CROSS_DENOMINATION_CANDIDATES", ()))

    def test_stale_or_out_of_range_snapshot_abstains(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "TOMORROW", 186_900, 185_500, 188_300)
        stale = infer_coin_commodity(value, price_project_thousand_toman=186_900, settlement_term="TOMORROW", now_utc="2026-08-04T10:03:00Z")
        outside = infer_coin_commodity(value, price_project_thousand_toman=210_000, settlement_term="TOMORROW", now_utc="2026-08-04T10:00:30Z")
        self.assertEqual((stale.status, stale.reason), ("ABSTAIN", "SNAPSHOT_STALE_OR_FUTURE"))
        self.assertEqual((outside.status, outside.reason), ("ABSTAIN", "PRICE_OUTSIDE_PUBLISHED_RANGES"))

    def test_unique_nearest_center_within_ten_percent_requires_confirmation(self) -> None:
        value = snapshot()
        set_rate(value, "QUARTER_BAHAR", "CASH", 52_300, 52_000, 52_650)
        set_rate(value, "QUARTER_LOW_DATE", "CASH", 47_300, 47_050, 47_600)

        result = infer_coin_commodity(
            value,
            price_project_thousand_toman=51_500,
            settlement_term="CASH",
            now_utc="2026-08-04T10:00:30Z",
        )

        self.assertEqual(
            (
                result.status,
                result.reason,
                [item.commodity_code for item in result.candidates],
            ),
            (
                "CONFIRM",
                "NEAREST_CENTER_FALLBACK_REQUIRES_CONFIRMATION",
                ["QUARTER_BAHAR"],
            ),
        )

    def test_tied_nearby_centers_expose_same_family_choices(self) -> None:
        value = snapshot()
        set_rate(value, "QUARTER_BAHAR", "CASH", 52_000, 51_900, 52_100)
        set_rate(value, "QUARTER_LOW_DATE", "CASH", 48_000, 47_900, 48_100)

        result = infer_coin_commodity(
            value,
            price_project_thousand_toman=50_000,
            settlement_term="CASH",
            now_utc="2026-08-04T10:00:30Z",
        )

        self.assertEqual(
            (result.status, [item.commodity_code for item in result.candidates]),
            ("CONFIRM", ["QUARTER_BAHAR", "QUARTER_LOW_DATE"]),
        )

    def test_nearby_fallback_never_crosses_denomination_families(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "CASH", 95_000, 94_500, 95_500)
        set_rate(value, "HALF_BAHAR", "CASH", 105_000, 104_500, 105_500)

        result = infer_coin_commodity(
            value,
            price_project_thousand_toman=100_000,
            settlement_term="CASH",
            now_utc="2026-08-04T10:00:30Z",
        )

        self.assertEqual(
            (result.status, result.reason, result.candidates),
            ("ABSTAIN", "CROSS_DENOMINATION_NEARBY_CANDIDATES", ()),
        )

    def test_low_date_scope_excludes_normal_date_candidates(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "CASH", 186_900, 185_500, 188_300)
        set_rate(value, "BAHAR", "CASH", 186_700, 185_500, 187_900)
        result = infer_coin_commodity(
            value,
            price_project_thousand_toman=186_800,
            settlement_term="CASH",
            now_utc="2026-08-04T10:00:30Z",
            candidate_scope="LOW_DATE_ONLY",
        )
        self.assertEqual(
            (result.status, [item.commodity_code for item in result.candidates]),
            ("AUTO_SELECT", ["BAHAR"]),
        )

    def test_pack_scope_derives_half_pack_from_base_rates_without_pack_rates(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "TOMORROW", 198_000, 197_000, 199_000)
        set_rate(value, "HALF_BAHAR", "TOMORROW", 100_000, 99_400, 101_000)
        set_rate(value, "QUARTER_BAHAR", "TOMORROW", 51_000, 50_500, 51_500)

        result = infer_coin_commodity(
            value,
            price_project_thousand_toman=100_600,
            settlement_term="TOMORROW",
            now_utc="2026-08-04T10:00:30Z",
            candidate_scope="PACK_ONLY",
        )

        self.assertEqual(
            (
                result.status,
                result.candidates[0].commodity_code,
                result.candidates[0].commodity_name,
            ),
            ("AUTO_SELECT", "PACK_HALF", "پک نیم"),
        )
        self.assertFalse(
            any(str(item["commodity_code"]).startswith("PACK_") for item in value["rates"]["items"])
        )

    def test_pack_premium_uses_nearest_base_range_and_requires_confirmation(self) -> None:
        value = snapshot()
        set_rate(value, "IMAM", "CASH", 198_000, 197_000, 199_000)
        set_rate(value, "HALF_BAHAR", "CASH", 100_000, 99_400, 100_200)
        set_rate(value, "QUARTER_BAHAR", "CASH", 51_000, 50_500, 51_500)

        result = infer_coin_commodity(
            value,
            price_project_thousand_toman=100_600,
            settlement_term="CASH",
            now_utc="2026-08-04T10:00:30Z",
            candidate_scope="PACK_ONLY",
        )

        self.assertEqual(
            (
                result.status,
                result.reason,
                [item.commodity_code for item in result.candidates],
            ),
            (
                "CONFIRM",
                "NEAREST_CENTER_FALLBACK_REQUIRES_CONFIRMATION",
                ["PACK_HALF"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
