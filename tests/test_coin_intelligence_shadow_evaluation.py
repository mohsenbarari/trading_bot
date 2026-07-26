from __future__ import annotations

import unittest

from core.market_intelligence.evaluation import (
    ShadowScoreRecord,
    aggregate_shadow_scores,
)


def _record(
    *,
    run_id: str = "run-1",
    role: str = "PRIMARY_SHADOW",
    candidate: str = "CURRENT",
    center: int | None = 100,
    lower: int | None = 95,
    upper: int | None = 105,
    actual: int = 100,
    absolute_error: float | None = 0.0,
    signed_error: float | None = 0.0,
    covered: bool | None = True,
    label: str = "UNREVIEWED",
    training_eligible: bool = False,
) -> ShadowScoreRecord:
    return ShadowScoreRecord(
        run_id=run_id,
        model_role=role,
        candidate_name=candidate,
        commodity="امام",
        settlement="CASH",
        trade_form="PHYSICAL",
        center_project_price=center,
        lower_project_price=lower,
        upper_project_price=upper,
        actual_project_price=actual,
        absolute_percent_error=absolute_error,
        signed_percent_error=signed_error,
        interval_covered=covered,
        label_status=label,
        training_eligible=training_eligible,
    )


class ShadowEvaluationTests(unittest.TestCase):
    def test_unreviewed_project_outcomes_never_enter_promotion_cohort(
        self,
    ) -> None:
        report = aggregate_shadow_scores([_record()])

        self.assertEqual(
            report["operational_unreviewed_and_reviewed"]["record_count"],
            1,
        )
        self.assertEqual(
            report["promotion_eligible_only"]["record_count"],
            0,
        )
        self.assertEqual(
            report["promotion_warning"],
            "NO_PROMOTION_ELIGIBLE_REVIEWED_OUTCOMES",
        )

    def test_review_and_training_gate_are_both_required(self) -> None:
        report = aggregate_shadow_scores(
            [
                _record(label="REVIEWED", training_eligible=False),
                _record(label="UNREVIEWED", training_eligible=True),
                _record(label="TRUSTED", training_eligible=True),
            ]
        )

        self.assertEqual(
            report["promotion_eligible_only"]["record_count"],
            1,
        )

    def test_primary_and_candidate_are_reported_separately(self) -> None:
        report = aggregate_shadow_scores(
            [
                _record(absolute_error=2.0, signed_error=2.0),
                _record(
                    role="CANDIDATE_SHADOW",
                    candidate="HYBRID_V2",
                    absolute_error=1.0,
                    signed_error=-1.0,
                    covered=False,
                ),
            ]
        )

        slices = report["operational_unreviewed_and_reviewed"]["slices"]
        self.assertEqual(len(slices), 2)
        by_candidate = {item["candidate_name"]: item for item in slices}
        self.assertEqual(by_candidate["CURRENT"]["mape_percent"], 2.0)
        self.assertEqual(
            by_candidate["HYBRID_V2"]["interval_coverage_percent"],
            0.0,
        )
        paired = report[
            "operational_unreviewed_and_reviewed"
        ]["paired_candidate_comparisons"]
        self.assertEqual(len(paired), 1)
        self.assertEqual(
            paired[0][
                "mean_absolute_error_improvement_percentage_points"
            ],
            1.0,
        )

    def test_abstention_does_not_become_zero_error(self) -> None:
        report = aggregate_shadow_scores(
            [
                _record(
                    center=None,
                    lower=None,
                    upper=None,
                    absolute_error=None,
                    signed_error=None,
                    covered=None,
                )
            ]
        )

        metrics = report[
            "operational_unreviewed_and_reviewed"
        ]["slices"][0]
        self.assertEqual(metrics["abstention_count"], 1)
        self.assertIsNone(metrics["mape_percent"])


if __name__ == "__main__":
    unittest.main()
