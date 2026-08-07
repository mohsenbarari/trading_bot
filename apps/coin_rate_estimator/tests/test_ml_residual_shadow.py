from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ml_residual_shadow import apply_ml_residual_to_estimate


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[list[list[float]]] = []

    def predict(self, vectors: list[list[float]]) -> list[float]:
        self.calls.append(vectors)
        return [0.01 * (index + 1) for index, _ in enumerate(vectors)]


class MlResidualShadowTests(unittest.TestCase):
    def test_all_rates_are_predicted_in_one_equivalent_batch(self) -> None:
        model = RecordingModel()
        live = {
            "model_kind": "LIVE",
            "project_price_multiplier_to_toman": 1000,
            "settlements": {
                "CASH": {
                    "inputs": {},
                    "rates": [
                        {
                            "status": "ESTIMATED",
                            "commodity_name": "امام",
                            "trade_form": "PHYSICAL",
                            "estimated_project_price": 100_000,
                            "estimated_price_toman": 100_000_000,
                            "lower_project_price": 99_000,
                            "upper_project_price": 101_000,
                            "method": "STRUCTURAL",
                        },
                        {
                            "status": "ESTIMATED",
                            "commodity_name": "نیم بهار",
                            "trade_form": "PHYSICAL",
                            "estimated_project_price": 200_000,
                            "estimated_price_toman": 200_000_000,
                            "lower_project_price": 199_000,
                            "upper_project_price": 201_000,
                            "method": "STRUCTURAL",
                        },
                    ],
                }
            },
        }
        shadow = apply_ml_residual_to_estimate(
            live,
            {
                "sklearn_model": model,
                "feature_keys": [],
                "target_mode": "relative",
                "max_abs_relative_correction": 0.015,
                "prediction_shrink": 1.0,
            },
            end=datetime(2026, 8, 6, tzinfo=timezone.utc),
        )
        rates = shadow["settlements"]["CASH"]["rates"]
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.calls[0]), 2)
        self.assertEqual(rates[0]["estimated_project_price"], 101_000)
        # The second raw 2% correction is capped at the same 1.5% limit as
        # before batching.
        self.assertEqual(rates[1]["estimated_project_price"], 203_000)
        self.assertEqual(rates[1]["ml_residual_correction"]["applied_relative"], 0.015)


if __name__ == "__main__":
    unittest.main()
