"""P7 rollout metrics must remain descriptive, bounded, and private."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest

from core.market_intelligence.coin_inference_rollout_metrics import build_coin_inference_rollout_metrics


def decision(
    key: str,
    *,
    status: str,
    source: str = "WEBAPP",
    settlement: str = "CASH",
    created: str = "2026-08-05T08:01:00Z",
    snapshot: str | None = "2026-08-05T08:00:00Z",
    selected_code: str | None = None,
    reason: str | None = None,
    underlying_source: str = "UNKNOWN",
    market_regime: str = "UNKNOWN",
):
    return SimpleNamespace(
        decision_key=key * 64,
        decision_status=status,
        source_surface=source,
        settlement_term=settlement,
        created_at=datetime.fromisoformat(created.replace("Z", "+00:00")),
        snapshot_generated_at_utc=(datetime.fromisoformat(snapshot.replace("Z", "+00:00")) if snapshot else None),
        selected_commodity_code=selected_code,
        reason_code=reason,
        dominant_underlying_source=underlying_source,
        market_regime=market_regime,
        submitted_project_price=186_800,
    )


def outcome(key: str, *, code: str, source: str = "WEBAPP", outcome_key: str = "f"):
    return SimpleNamespace(
        outcome_key=outcome_key * 64,
        decision_key=key * 64,
        source_surface=source,
        selected_commodity_code=code,
    )


class CoinInferenceRolloutMetricsTests(unittest.TestCase):
    def test_report_groups_safe_dimensions_and_never_promotes_automatically(self) -> None:
        report = build_coin_inference_rollout_metrics(
            [
                decision(
                    "a", status="AUTO_SELECT", selected_code="IMAM",
                    underlying_source="PRIVATE_PHYSICAL_TODAY", market_regime="UP",
                ),
                decision(
                    "b",
                    status="CONFIRM",
                    source="TELEGRAM_BOT",
                    settlement="TOMORROW",
                    created="2026-08-05T08:00:20Z",
                    snapshot="2026-08-05T08:00:00Z",
                    underlying_source="PRIVATE_PAPER_TOMORROW", market_regime="DOWN",
                ),
                decision("c", status="ABSTAIN", snapshot=None, reason="PRICE_OUTSIDE_PUBLISHED_RANGES"),
            ],
            [
                outcome("a", code="IMAM", outcome_key="d"),
                outcome("b", code="BAHAR", source="TELEGRAM_BOT", outcome_key="e"),
            ],
            generated_at_utc=datetime(2026, 8, 5, 8, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["decision_counts"], {"AUTO_SELECT": 1, "CONFIRM": 1, "ABSTAIN": 1})
        self.assertEqual((report["selectable_decision_count"], report["accepted_selection_count"]), (2, 2))
        self.assertEqual(report["accepted_selection_coverage_percent"], 100.0)
        self.assertEqual(report["auto_choice_revalidation"], {"matching_accepted_choices": 1, "mismatching_accepted_choices": 0})
        self.assertEqual(report["promotion_guard"]["auto_promotion_allowed"], False)
        self.assertEqual(report["promotion_guard"]["missing_dimensions"], ["operator_correction_outcome"])
        self.assertTrue(any(cell["tehran_hour"] == "11" for cell in report["cells"]))
        self.assertTrue(any(cell["snapshot_age_bucket"] == "AGE_31_120S" for cell in report["cells"]))
        self.assertTrue(
            any(
                cell["dominant_underlying_source"] == "PRIVATE_PHYSICAL_TODAY"
                and cell["market_regime"] == "UP"
                for cell in report["cells"]
            )
        )

    def test_report_detects_bad_outcomes_without_exposing_private_or_price_fields(self) -> None:
        report = build_coin_inference_rollout_metrics(
            [decision("a", status="AUTO_SELECT", selected_code="IMAM")],
            [
                outcome("a", code="IMAM", source="TELEGRAM_BOT", outcome_key="d"),
                outcome("z", code="IMAM", outcome_key="e"),
            ],
        )
        self.assertEqual(report["data_quality"]["surface_mismatched_outcomes"], 1)
        self.assertEqual(report["data_quality"]["orphan_outcomes"], 1)
        serialized = json.dumps(report, ensure_ascii=False)
        for forbidden in (
            '"submitted_project_price"',
            "186800",
            '"user_id"',
            '"offer_id"',
            '"telegram_id"',
            '"decision_key"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_empty_report_is_an_explicit_non_promotable_state(self) -> None:
        report = build_coin_inference_rollout_metrics([], [])
        self.assertEqual((report["status"], report["accepted_selection_coverage_percent"]), ("NO_DECISIONS", None))
        self.assertFalse(report["promotion_guard"]["auto_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
