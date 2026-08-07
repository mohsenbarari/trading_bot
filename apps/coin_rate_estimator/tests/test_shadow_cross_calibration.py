from __future__ import annotations

import unittest
from datetime import datetime, timezone

from shadow_cross_calibration import apply_cross_calibration_to_estimate


class ShadowCrossCalibrationTests(unittest.TestCase):
    def test_never_overrides_live_group_anchor_and_widens_shifted_band(self) -> None:
        anchored = {
            "commodity_name": "امام",
            "status": "ESTIMATED",
            "estimated_price_toman": 100_000_000,
            "estimated_project_price": 100_000,
            "group_offer_anchor": {"status": "OBSERVED"},
            "tolerance": {
                "lower_price_toman": 99_000_000,
                "upper_price_toman": 101_000_000,
            },
        }
        inferred = {
            "commodity_name": "نیم بهار",
            "status": "ESTIMATED",
            "estimated_price_toman": 100_000_000,
            "estimated_project_price": 100_000,
            "tolerance": {
                "lower_price_toman": 99_000_000,
                "upper_price_toman": 101_000_000,
            },
        }
        estimate = {"settlements": {"CASH": {"rates": [anchored, inferred]}}}
        report = {
            "proposals": {
                "امام:CASH": {"correction_ratio": 0.01},
                "نیم بهار:CASH": {"correction_ratio": 0.01, "best_shadow": "s2"},
            }
        }

        applied = apply_cross_calibration_to_estimate(
            estimate,
            report,
            # 10:30 Tehran: no time decay yet.
            as_of=datetime(2026, 8, 5, 7, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(applied, 1)
        self.assertEqual(anchored["estimated_price_toman"], 100_000_000)
        self.assertEqual(inferred["estimated_price_toman"], 101_000_000)
        self.assertEqual(inferred["tolerance"]["lower_price_toman"], 99_000_000)
        self.assertEqual(inferred["tolerance"]["upper_price_toman"], 102_000_000)
        self.assertEqual(inferred["tolerance"]["lower_project_price"], 99_000)
        self.assertEqual(inferred["tolerance"]["upper_project_price"], 102_000)


if __name__ == "__main__":
    unittest.main()
