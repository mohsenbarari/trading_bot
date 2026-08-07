from __future__ import annotations

import unittest

from estimate_finalization import finalize_deterministic_book


def _rate(name: str, point: int, *, lower: int, upper: int) -> dict:
    return {
        "commodity_name": name,
        "status": "ESTIMATED",
        "estimated_price_toman": point,
        "estimated_project_price": point // 1_000,
        "tolerance": {
            "lower_price_toman": lower,
            "upper_price_toman": upper,
            "lower_project_price": lower // 1_000,
            "upper_project_price": upper // 1_000,
        },
    }


class EstimateFinalizationTests(unittest.TestCase):
    def test_enforces_term_floor_and_keeps_point_inside_band(self) -> None:
        cash = _rate("امام", 101_000_000, lower=100_000_000, upper=102_000_000)
        tomorrow = _rate("امام", 99_000_000, lower=98_000_000, upper=100_000_000)
        estimate = {
            "settlements": {
                "CASH": {"rates": [cash]},
                "TOMORROW": {"rates": [tomorrow]},
            }
        }

        audit = finalize_deterministic_book(estimate)

        self.assertEqual(len(audit["term_structure_fixes"]), 1)
        self.assertEqual(tomorrow["estimated_price_toman"], 101_000_000)
        self.assertLessEqual(
            tomorrow["tolerance"]["lower_price_toman"],
            tomorrow["estimated_price_toman"],
        )
        self.assertGreaterEqual(
            tomorrow["tolerance"]["upper_price_toman"],
            tomorrow["estimated_price_toman"],
        )

    def test_low_date_range_stays_separate_from_regular_family(self) -> None:
        low = _rate(
            "ربع تاریخ پایین", 47_000_000, lower=45_000_000, upper=55_000_000
        )
        regular = _rate("ربع بهار", 53_000_000, lower=52_000_000, upper=56_000_000)
        estimate = {"settlements": {"CASH": {"rates": [low, regular]}}}

        audit = finalize_deterministic_book(estimate)

        self.assertEqual(audit["low_date_rows"], 2)
        self.assertLess(
            low["tolerance"]["upper_price_toman"],
            regular["tolerance"]["lower_price_toman"],
        )
        self.assertLessEqual(
            low["tolerance"]["lower_price_toman"], low["estimated_price_toman"]
        )
        self.assertGreaterEqual(
            low["tolerance"]["upper_price_toman"], low["estimated_price_toman"]
        )


if __name__ == "__main__":
    unittest.main()
