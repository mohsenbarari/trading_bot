from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.contracts import RateShadowPrediction
from core.market_intelligence.hybrid_v2 import evaluate_hybrid_v2


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def primary(
    *,
    commodity: str = "امام",
    anchor_age_seconds: int | None = 1_200,
    anchor_kind: str | None = "STRUCTURAL",
    evidence: dict | None = None,
) -> RateShadowPrediction:
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity=commodity,
        settlement="CASH",
        trade_form="PHYSICAL",
        center_project_price=184_000,
        lower_project_price=183_000,
        upper_project_price=185_000,
        confidence_label="MEDIUM",
        method="CURRENT",
        decision_reason="VALIDATED_CANONICAL_RATE",
        anchor_kind=anchor_kind,
        anchor_age_seconds=anchor_age_seconds,
        bundle_version="primary-v1",
        feature_schema_version="features-v1",
        snapshot_version="snapshot-v1",
        evidence=evidence or {},
    )


class HybridV2ShadowTests(unittest.TestCase):
    def test_unvalidated_market_falls_back_without_number(self) -> None:
        candidate = evaluate_hybrid_v2(
            primary(commodity="ربع بهار"),
            as_of_utc=NOW,
        )

        self.assertEqual(candidate.status, "GATED_OFF")
        self.assertIsNone(candidate.center_project_price)
        self.assertEqual(
            candidate.decision_reason,
            "HYBRID_V2_MARKET_NOT_VALIDATED",
        )

    def test_missing_history_is_visible_instead_of_fabricating_price(
        self,
    ) -> None:
        candidate = evaluate_hybrid_v2(primary(), as_of_utc=NOW)

        self.assertEqual(candidate.status, "GATED_OFF")
        self.assertIsNone(candidate.center_project_price)
        self.assertEqual(
            candidate.decision_reason,
            "HYBRID_V2_REQUIRED_SAME_MARKET_HISTORY_MISSING",
        )

    def test_live_anchor_path_is_identical_to_primary(self) -> None:
        source = primary(anchor_age_seconds=120, anchor_kind="CONFIRMED_TRADE")

        candidate = evaluate_hybrid_v2(source, as_of_utc=NOW)

        self.assertEqual(candidate.status, "ESTIMATED")
        self.assertEqual(
            candidate.center_project_price,
            source.center_project_price,
        )
        self.assertEqual(
            candidate.lower_project_price,
            source.lower_project_price,
        )
        self.assertEqual(candidate.method, "HYBRID_V2_SHARED_LIVE_ANCHOR")

    def test_strictly_prior_history_builds_candidate_union_interval(
        self,
    ) -> None:
        history = [
            {
                "observed_at_utc": (
                    NOW - timedelta(minutes=30 - index * 5)
                ).isoformat(),
                "bubble_ratio": bubble,
                "source_weight": 1.5 if index == 2 else 1.0,
            }
            for index, bubble in enumerate((0.015, 0.020, 0.018, 0.021, 0.019))
        ]
        # This future value would materially move the estimate if leakage were
        # allowed; the candidate must ignore it.
        history.append(
            {
                "observed_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
                "bubble_ratio": 0.50,
                "source_weight": 100.0,
            }
        )
        source = primary(
            evidence={
                "rate": {"intrinsic_toman": 180_000_000},
                "market_regime": {
                    "direction_score": 0.0,
                    "confidence": 1.0,
                    "volatility_percent": 0.05,
                },
                "same_market_history": history,
            }
        )

        candidate = evaluate_hybrid_v2(source, as_of_utc=NOW)

        self.assertEqual(candidate.status, "ESTIMATED")
        self.assertLess(candidate.center_project_price, 200_000)
        self.assertLessEqual(
            candidate.lower_project_price,
            source.lower_project_price,
        )
        self.assertGreaterEqual(
            candidate.upper_project_price,
            source.upper_project_price,
        )
        self.assertEqual(
            candidate.evidence["dynamic_history_count"],
            5,
        )


if __name__ == "__main__":
    unittest.main()
