from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.residual_research import (
    chronological_split,
    feature_vector,
    normalize_rows,
)
from scripts.discover_coin_residual_symbolic_shadow import discover
from scripts.train_coin_residual_catboost_shadow import train


NOW = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)


def source_row(index: int, **overrides) -> dict:
    payload = {
        "schema_version": "COIN_RESIDUAL_RESEARCH_V1_20260801",
        "occurred_at_utc": (NOW + timedelta(minutes=index)).isoformat(),
        "commodity": "امام",
        "settlement": "TOMORROW",
        "trade_form": "PHYSICAL",
        "baseline_project_price": 185_000,
        "actual_project_price": 185_500,
        "label_status": "REVIEWED",
        "training_eligible": True,
        "training_weight": 1.0,
        "features": {
            "tehran_time": {"minute_of_day": 700, "weekday_iso": 6},
            "market_regime_v2": {"direction_score": 0.2, "confidence": 0.8},
            "sources": {"melted_gold": {"status": "OBSERVED"}},
            "rate": {"intrinsic_toman": 170_000_000},
        },
    }
    payload.update(overrides)
    return payload


class ResidualResearchTests(unittest.TestCase):
    def test_only_reviewed_physical_bounded_rows_are_accepted(self) -> None:
        rows = normalize_rows(
            [
                source_row(1),
                source_row(2, label_status="UNREVIEWED"),
                source_row(3, trade_form="PAPER"),
                source_row(4, actual_project_price=250_000),
            ]
        )
        self.assertEqual(len(rows), 1)
        vector = feature_vector(rows[0])
        self.assertEqual(vector["commodity"], "امام")
        self.assertEqual(vector["melted_observed"], 1.0)

    def test_same_timestamp_never_crosses_chronological_partitions(self) -> None:
        rows = normalize_rows(
            [source_row(index // 2) for index in range(20)]
        )
        fit, calibration, test = chronological_split(rows)
        self.assertLess(max(row.occurred_at_utc for row in fit), min(row.occurred_at_utc for row in calibration))
        self.assertLess(max(row.occurred_at_utc for row in calibration), min(row.occurred_at_utc for row in test))

    def test_challengers_fail_closed_when_evidence_is_too_small(self) -> None:
        rows = normalize_rows([source_row(index) for index in range(30)])
        catboost_report, model = train(rows)
        symbolic_report = discover(rows, execute=False, niterations=5)
        self.assertIsNone(model)
        self.assertEqual(catboost_report["reason"], "INSUFFICIENT_REVIEWED_TRAINING_ROWS")
        self.assertEqual(symbolic_report["reason"], "INSUFFICIENT_REVIEWED_FIT_ROWS")


if __name__ == "__main__":
    unittest.main()
