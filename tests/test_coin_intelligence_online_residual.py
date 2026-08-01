from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.contracts import RateShadowPrediction
from core.market_intelligence.online_residual_v1 import (
    evaluate_online_residual_v1,
)


NOW = datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)


def primary(history: list[dict]) -> RateShadowPrediction:
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity="امام",
        settlement="TOMORROW",
        trade_form="PHYSICAL",
        center_project_price=185_000,
        lower_project_price=183_500,
        upper_project_price=186_500,
        confidence_label="MEDIUM",
        method="PRIMARY",
        decision_reason="OK",
        anchor_kind="STRICTLY_PRIOR_ANCHOR",
        anchor_age_seconds=600,
        bundle_version="primary",
        feature_schema_version="v2",
        snapshot_version="snapshot",
        evidence={"same_market_history": history},
    )


def reviewed_trade(
    *,
    minutes_before: int,
    actual: int = 186_850,
    baseline: int = 185_000,
) -> dict:
    return {
        "observed_at_utc": (NOW - timedelta(minutes=minutes_before)).isoformat(),
        "price_project": actual,
        "baseline_project_price": baseline,
        "source_weight": 1.5,
        "source_kind": "CONFIRMED_TRADE",
        "label_status": "REVIEWED",
        "training_eligible": True,
    }


class OnlineResidualCandidateTests(unittest.TestCase):
    def test_requires_enough_reviewed_strictly_prior_trades(self) -> None:
        result = evaluate_online_residual_v1(
            primary([reviewed_trade(minutes_before=1)]),
            as_of_utc=NOW,
        )
        self.assertEqual(result.status, "GATED_OFF")
        self.assertEqual(
            result.decision_reason,
            "ONLINE_RESIDUAL_INSUFFICIENT_REVIEWED_TRADES",
        )

    def test_future_unreviewed_and_offer_rows_cannot_calibrate(self) -> None:
        history = [
            reviewed_trade(minutes_before=1),
            reviewed_trade(minutes_before=2),
            reviewed_trade(minutes_before=3),
            {
                **reviewed_trade(minutes_before=-1, actual=200_000),
            },
            {
                **reviewed_trade(minutes_before=1, actual=200_000),
                "label_status": "UNREVIEWED",
            },
            {
                **reviewed_trade(minutes_before=1, actual=200_000),
                "source_kind": "UNREVIEWED_OFFER",
            },
        ]
        result = evaluate_online_residual_v1(primary(history), as_of_utc=NOW)
        self.assertEqual(result.status, "ESTIMATED")
        self.assertGreater(result.center_project_price, 185_000)
        self.assertLess(result.center_project_price, 190_000)
        self.assertEqual(result.evidence["reviewed_trade_count"], 3)

    def test_candidate_never_narrows_primary_interval(self) -> None:
        result = evaluate_online_residual_v1(
            primary([reviewed_trade(minutes_before=index) for index in (1, 2, 3)]),
            as_of_utc=NOW,
        )
        self.assertEqual(result.status, "ESTIMATED")
        self.assertLessEqual(result.lower_project_price, 183_500)
        self.assertGreaterEqual(result.upper_project_price, 186_500)
        self.assertTrue(result.method.startswith("ONLINE_BAYESIAN_RESIDUAL_V1"))

    def test_extreme_residual_is_quarantined(self) -> None:
        history = [
            reviewed_trade(minutes_before=1),
            reviewed_trade(minutes_before=2),
            reviewed_trade(minutes_before=3),
            reviewed_trade(minutes_before=4, actual=250_000),
        ]
        result = evaluate_online_residual_v1(primary(history), as_of_utc=NOW)
        self.assertEqual(result.status, "ESTIMATED")
        self.assertEqual(result.evidence["reviewed_trade_count"], 3)


if __name__ == "__main__":
    unittest.main()
