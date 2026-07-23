from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

from core.market_intelligence.bundle import load_runtime_bundle
from core.market_intelligence.ranker import (
    CommodityRanker,
    estimator_state_snapshot,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _rate(
    commodity: str,
    center: int,
    lower: int,
    upper: int,
    confidence: str = "MEDIUM",
) -> dict:
    return {
        "commodity_name": commodity,
        "status": "ESTIMATED",
        "estimated_project_price": center,
        "confidence": confidence,
        "training_sample_count": 40,
        "tolerance": {
            "lower_project_price": lower,
            "upper_project_price": upper,
        },
    }


def _snapshot(
    rates: list[dict],
    *,
    generated_at: datetime = NOW,
    settlement: str = "CASH",
    low_date_reference: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "generated_at_utc": generated_at.isoformat().replace(
            "+00:00",
            "Z",
        ),
        "settlement": settlement,
        "trade_form": "PHYSICAL",
        "model_version": "model-sha",
        "feature_schema_version": "features-v1",
        "snapshot_version": "snapshot-1",
        "rates": rates,
        "low_date_physical_reference": low_date_reference,
    }


class CoinIntelligenceRankerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ranker = CommodityRanker(
            load_runtime_bundle().ranker_policy
        )

    def test_unique_range_is_inferred(self) -> None:
        result = self.ranker.infer(
            29_100,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [
                    _rate("ربع بهار", 54_000, 52_500, 56_000),
                    _rate("یک گرمی", 29_000, 28_500, 29_500),
                ]
            ),
            now=NOW,
        )

        self.assertEqual(result.status, "INFERRED")
        self.assertEqual(result.inferred_commodity, "یک گرمی")
        self.assertFalse(result.requires_user_confirmation)

    def test_full_toman_price_is_normalized(self) -> None:
        result = self.ranker.infer(
            29_100_000,
            price_unit="TOMAN_FULL",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [_rate("یک گرمی", 29_000, 28_500, 29_500)]
            ),
            now=NOW,
        )

        self.assertEqual(result.status, "INFERRED")
        self.assertEqual(result.normalized_price, 29_100)

    def test_domain_overlap_abstains_without_physical_anchor(self) -> None:
        result = self.ranker.infer(
            184_000,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [
                    _rate("امام", 184_000, 181_000, 187_000),
                    _rate("بهار", 179_000, 176_000, 182_000),
                ]
            ),
            now=NOW,
        )

        self.assertEqual(result.status, "AMBIGUOUS")
        self.assertIsNone(result.inferred_commodity)
        self.assertIn("DOMAIN_PRICE_OVERLAP", result.warnings)

    def test_explicit_constraint_can_resolve_domain_overlap(self) -> None:
        result = self.ranker.infer(
            184_000,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [
                    _rate("امام", 184_000, 181_000, 187_000),
                    _rate("بهار", 179_000, 176_000, 182_000),
                ]
            ),
            now=NOW,
            allowed_commodities=["امام"],
        )

        self.assertEqual(result.status, "INFERRED")
        self.assertEqual(result.inferred_commodity, "امام")

    def test_dynamic_low_date_boundary_keeps_guard_ambiguous(self) -> None:
        reference = {
            "status": "OBSERVED",
            "trade_form": "PHYSICAL",
            "uncertainty_relative": 0.0,
        }
        result = self.ranker.infer(
            90_750,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [
                    _rate(
                        "نیم تاریخ پایین",
                        87_500,
                        87_000,
                        88_000,
                    ),
                    _rate("نیم بهار", 94_000, 92_000, 96_000),
                ],
                low_date_reference=reference,
            ),
            now=NOW,
        )

        self.assertEqual(result.status, "AMBIGUOUS")
        self.assertIn("DYNAMIC_FAMILY_BOUNDARY_GUARD", result.warnings)

    def test_stale_snapshot_never_auto_selects(self) -> None:
        result = self.ranker.infer(
            29_100,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [_rate("یک گرمی", 29_000, 28_500, 29_500)],
                generated_at=NOW - timedelta(days=1),
            ),
            now=NOW,
        )

        self.assertEqual(result.status, "STALE_INPUT")
        self.assertIsNone(result.inferred_commodity)

    def test_empty_snapshot_and_invalid_price_are_explicit(self) -> None:
        empty = self.ranker.infer(
            29_100,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot([]),
            now=NOW,
        )
        invalid = self.ranker.infer(
            -1,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot([]),
            now=NOW,
        )

        self.assertEqual(empty.status, "NO_MARKET_DATA")
        self.assertEqual(invalid.status, "INVALID_PRICE")

    def test_market_dimension_mismatch_fails_closed(self) -> None:
        result = self.ranker.infer(
            29_100,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="TOMORROW",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [_rate("یک گرمی", 29_000, 28_500, 29_500)]
            ),
            now=NOW,
        )

        self.assertEqual(result.status, "UNSUPPORTED_MARKET_DIMENSION")

    def test_legacy_adapter_never_invents_paper_coin_market(self) -> None:
        state = {
            "generated_at_utc": "2026-07-23T12:00:00Z",
            "settlements": {"CASH": {"rates": []}},
        }

        with self.assertRaises(ValueError):
            estimator_state_snapshot(
                state,
                settlement="CASH",
                trade_form="PAPER",
            )

    def test_result_is_json_serializable(self) -> None:
        result = self.ranker.infer(
            29_100,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement="CASH",
            trade_form="PHYSICAL",
            snapshot=_snapshot(
                [_rate("یک گرمی", 29_000, 28_500, 29_500)]
            ),
            now=NOW,
        )

        rendered = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertIn("requires_user_confirmation", rendered)


if __name__ == "__main__":
    unittest.main()
