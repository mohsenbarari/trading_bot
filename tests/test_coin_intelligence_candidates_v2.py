from __future__ import annotations

from datetime import datetime, timezone
import unittest

from core.market_intelligence.basis_v2 import evaluate_basis_v2
from core.market_intelligence.contracts import RateShadowPrediction
from core.market_intelligence.low_date_v2 import evaluate_low_date_v2


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def primary(commodity: str, settlement: str = "CASH", evidence=None):
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity=commodity,
        settlement=settlement,
        trade_form="PHYSICAL",
        center_project_price=180_000,
        lower_project_price=178_000,
        upper_project_price=182_000,
        confidence_label="MEDIUM",
        method="PRIMARY",
        decision_reason="OK",
        anchor_kind="OLD_ANCHOR",
        anchor_age_seconds=3600,
        bundle_version="primary",
        feature_schema_version="v2",
        snapshot_version="snapshot",
        evidence=evidence or {},
    )


class CandidateV2Tests(unittest.TestCase):
    def test_low_date_uses_physical_melted_reference(self) -> None:
        evidence = {
            "low_date_physical_reference": {
                "status": "OBSERVED",
                "trade_form": "PHYSICAL",
                "price_unit": "IRT_PER_MESGHAL_750",
                "average_price_toman": 80_000_000,
                "lower_price_toman": 79_900_000,
                "upper_price_toman": 80_100_000,
                "age_seconds": 60,
                "selection": "EXPLICIT_PHYSICAL",
            },
            "same_market_history": [],
        }
        result = evaluate_low_date_v2(
            primary("بهار", evidence=evidence),
            as_of_utc=NOW,
        )

        self.assertEqual(result.status, "ESTIMATED")
        self.assertEqual(result.center_project_price, 180_250)

    def test_settlement_history_keeps_low_date_candidates_separate(self) -> None:
        reference = {
            "status": "OBSERVED",
            "trade_form": "PHYSICAL",
            "price_unit": "IRT_PER_MESGHAL_750",
            "average_price_toman": 80_000_000,
            "lower_price_toman": 80_000_000,
            "upper_price_toman": 80_000_000,
        }
        cash = evaluate_low_date_v2(
            primary(
                "بهار",
                "CASH",
                {
                    "low_date_physical_reference": reference,
                    "same_market_history": [
                        {"bubble_ratio": 0.01, "source_weight": 1.0}
                    ],
                },
            ),
            as_of_utc=NOW,
        )
        tomorrow = evaluate_low_date_v2(
            primary(
                "بهار",
                "TOMORROW",
                {
                    "low_date_physical_reference": reference,
                    "same_market_history": [
                        {"bubble_ratio": -0.01, "source_weight": 1.0}
                    ],
                },
            ),
            as_of_utc=NOW,
        )

        self.assertNotEqual(
            cash.center_project_price,
            tomorrow.center_project_price,
        )

    def test_basis_requires_five_strictly_prior_pairs(self) -> None:
        gated = evaluate_basis_v2(
            primary(
                "امام",
                evidence={
                    "settlement_basis": {
                        "status": "OBSERVED",
                        "pair_count": 4,
                        "price_project": 181_000,
                    }
                },
            ),
            as_of_utc=NOW,
        )
        accepted = evaluate_basis_v2(
            primary(
                "امام",
                evidence={
                    "settlement_basis": {
                        "status": "OBSERVED",
                        "pair_count": 5,
                        "price_project": 181_000,
                    }
                },
            ),
            as_of_utc=NOW,
        )

        self.assertEqual(gated.status, "GATED_OFF")
        self.assertEqual(accepted.center_project_price, 181_000)


if __name__ == "__main__":
    unittest.main()
