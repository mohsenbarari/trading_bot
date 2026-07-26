from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.features_v2 import (
    FEATURE_SNAPSHOT_V2_SCHEMA,
    build_feature_snapshot_v2,
)
from core.market_intelligence.regime_v2 import evaluate_regime_v2


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def _evidence(direction: float = 0.5) -> dict:
    return {
        "schema_version": "V1",
        "sources": {
            "melted_gold": {"status": "OBSERVED", "sample_count": 10},
            "usd": {"status": "OBSERVED", "sample_count": 8},
            "usdt": {"status": "OBSERVED", "sample_count": 4},
            "xauusd": {"status": "NO_DATA", "sample_count": 0},
        },
        "market_regime": {
            "direction_score": direction,
            "confidence": 0.9,
            "volatility_percent": 0.08,
            "components": [
                {
                    "name": "MELTED_GOLD",
                    "direction_strength": direction,
                    "reliability": 1.0,
                },
                {
                    "name": "USD_HERAT",
                    "direction_strength": direction * 0.8,
                    "reliability": 0.85,
                },
            ],
        },
        "order_flow": {"event_count": 6},
        "rate": {"intrinsic_toman": 170_000_000},
    }


class FeatureSnapshotV2Tests(unittest.TestCase):
    def test_future_and_same_timestamp_history_are_removed(self) -> None:
        snapshot = build_feature_snapshot_v2(
            _evidence(),
            as_of_utc=NOW,
            same_market_history=[
                {
                    "observed_at_utc": (
                        NOW - timedelta(minutes=1)
                    ).isoformat(),
                    "bubble_ratio": 0.08,
                    "source_weight": 1.0,
                },
                {
                    "observed_at_utc": NOW.isoformat(),
                    "bubble_ratio": 0.09,
                    "source_weight": 1.0,
                },
                {
                    "observed_at_utc": (
                        NOW + timedelta(seconds=1)
                    ).isoformat(),
                    "bubble_ratio": 0.10,
                    "source_weight": 1.0,
                },
            ],
        )

        self.assertEqual(snapshot["schema_version"], FEATURE_SNAPSHOT_V2_SCHEMA)
        self.assertEqual(snapshot["strictly_prior_history_count"], 1)
        self.assertEqual(
            snapshot["same_market_history"][0]["bubble_ratio"],
            0.08,
        )

    def test_missing_values_are_explicit_and_tehran_time_is_retained(
        self,
    ) -> None:
        snapshot = build_feature_snapshot_v2(
            _evidence(),
            as_of_utc=NOW,
        )

        self.assertIn("same_market_history", snapshot["missing_fields_v2"])
        self.assertIn("settlement_basis", snapshot["missing_fields_v2"])
        self.assertEqual(
            snapshot["tehran_time"]["timezone"],
            "Asia/Tehran",
        )

    def test_basis_at_cutoff_is_rejected(self) -> None:
        snapshot = build_feature_snapshot_v2(
            _evidence(),
            as_of_utc=NOW,
            settlement_basis={
                "status": "OBSERVED",
                "as_of_utc": NOW.isoformat(),
                "pair_count": 5,
                "price_project": 184_000,
            },
        )

        self.assertEqual(snapshot["settlement_basis"]["status"], "NO_DATA")


class RegimeV2Tests(unittest.TestCase):
    def test_underlying_agreement_produces_continuous_up_state(self) -> None:
        result = evaluate_regime_v2(_evidence(0.65))

        self.assertEqual(result["label"], "UP")
        self.assertGreater(result["direction_score"], 0)
        self.assertGreater(result["agreement_score"], 0.8)
        self.assertFalse(result["disagreement_flag"])

    def test_cross_source_disagreement_does_not_automatically_become_shock(
        self,
    ) -> None:
        evidence = _evidence()
        evidence["market_regime"]["direction_score"] = 0.0
        evidence["market_regime"]["components"][0][
            "direction_strength"
        ] = 0.8
        evidence["market_regime"]["components"][1][
            "direction_strength"
        ] = -0.8

        result = evaluate_regime_v2(evidence)

        self.assertTrue(result["disagreement_flag"])
        self.assertEqual(result["label"], "RANGE")

    def test_hysteresis_prevents_marginal_up_to_range_flip(self) -> None:
        evidence = _evidence(0.15)
        result = evaluate_regime_v2(evidence, previous_label="UP")

        self.assertEqual(result["label"], "UP")
        self.assertTrue(result["hysteresis_applied"])


if __name__ == "__main__":
    unittest.main()
