from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ml_residual_shadow import apply_ml_residual_to_estimate, feature_vector


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[list[list[float]]] = []

    def predict(self, vectors: list[list[float]]) -> list[float]:
        self.calls.append(vectors)
        return [0.01 * (index + 1) for index, _ in enumerate(vectors)]


class MlResidualShadowTests(unittest.TestCase):
    def test_feature_schema_preserves_legacy_artifact_meaning(self) -> None:
        keys = ["tehran_hour", "tehran_minute_of_day", "is_morning_open"]
        end = datetime(2026, 8, 6, 6, 0, tzinfo=timezone.utc)  # 09:30 Tehran
        common = {
            "feature_keys": keys,
            "commodity_name": "امام",
            "settlement": "CASH",
            "trade_form": "PHYSICAL",
            "rate": {},
            "inputs": {},
            "end": end,
        }
        legacy = feature_vector(**common)
        minute_v3 = feature_vector(
            **common, feature_schema_version="STRUCTURAL_MINUTE_V3"
        )
        self.assertEqual(legacy, [9.0, 570.0, 1.0])
        self.assertEqual(minute_v3, [9.5, 570.0, 0.0])

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
                            "tolerance": {
                                "lower_price_toman": 99_000_000,
                                "upper_price_toman": 101_000_000,
                            },
                            "method": "STRUCTURAL",
                        },
                        {
                            "status": "ESTIMATED",
                            "commodity_name": "نیم بهار",
                            "trade_form": "PHYSICAL",
                            "estimated_project_price": 200_000,
                            "estimated_price_toman": 200_000_000,
                            "tolerance": {
                                "lower_price_toman": 199_000_000,
                                "upper_price_toman": 201_000_000,
                            },
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
        # The published range follows the ML shift but preserves the original
        # structural coverage instead of being silently narrowed.
        self.assertEqual(rates[0]["tolerance"]["lower_price_toman"], 99_000_000)
        self.assertEqual(rates[0]["tolerance"]["upper_price_toman"], 102_010_000)
        self.assertEqual(rates[1]["tolerance"]["lower_price_toman"], 199_000_000)
        self.assertEqual(rates[1]["tolerance"]["upper_price_toman"], 204_015_000)


if __name__ == "__main__":
    unittest.main()
