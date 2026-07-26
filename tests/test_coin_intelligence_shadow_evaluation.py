from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
)

from core.market_intelligence.evaluation import (
    ShadowScoreRecord,
    aggregate_shadow_scores,
    load_shadow_score_records,
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

    def test_latest_rejection_overrides_prior_acceptance_and_trust(
        self,
    ) -> None:
        metadata = MetaData()
        runs = Table(
            "coin_intelligence_shadow_runs",
            metadata,
            Column("id", String, primary_key=True),
            Column("mode", String),
            Column("training_eligible", Boolean),
            Column("as_of_utc", DateTime(timezone=True)),
        )
        predictions = Table(
            "coin_intelligence_shadow_predictions",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("run_id", String),
            Column("model_role", String),
            Column("candidate_name", String),
            Column("canonical_commodity", String),
            Column("settlement", String),
            Column("trade_form", String),
            Column("center_project_price", Integer),
            Column("lower_project_price", Integer),
            Column("upper_project_price", Integer),
            Column("is_authoritative", Boolean),
        )
        outcomes = Table(
            "coin_intelligence_shadow_outcomes",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("prediction_id", Integer),
            Column("actual_project_price", Integer),
            Column("absolute_percent_error", Float),
            Column("signed_percent_error", Float),
            Column("interval_covered", Boolean),
            Column("label_status", String),
            Column("occurred_at_utc", DateTime(timezone=True)),
        )
        quality = Table(
            "coin_intelligence_shadow_quality_decisions",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("run_id", String),
            Column("training_weight", Float),
        )
        reviews = Table(
            "coin_intelligence_shadow_reviews",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("outcome_id", Integer),
            Column("review_version", Integer),
            Column("action", String),
            Column("corrected_project_price", Integer),
        )
        with tempfile.TemporaryDirectory() as directory:
            database_url = (
                f"sqlite+pysqlite:///{Path(directory) / 'review.sqlite'}"
            )
            engine = create_engine(database_url)
            metadata.create_all(engine)
            predicted_at = datetime(
                2026,
                7,
                26,
                8,
                0,
                tzinfo=timezone.utc,
            )
            with engine.begin() as connection:
                connection.execute(
                    runs.insert(),
                    {
                        "id": "run-1",
                        "mode": "shadow",
                        "training_eligible": True,
                        "as_of_utc": predicted_at,
                    },
                )
                connection.execute(
                    predictions.insert(),
                    {
                        "id": 1,
                        "run_id": "run-1",
                        "model_role": "PRIMARY_SHADOW",
                        "candidate_name": "CURRENT",
                        "canonical_commodity": "امام",
                        "settlement": "CASH",
                        "trade_form": "PHYSICAL",
                        "center_project_price": 110,
                        "lower_project_price": 100,
                        "upper_project_price": 120,
                        "is_authoritative": False,
                    },
                )
                connection.execute(
                    outcomes.insert(),
                    {
                        "id": 1,
                        "prediction_id": 1,
                        "actual_project_price": 100,
                        "absolute_percent_error": 10.0,
                        "signed_percent_error": 10.0,
                        "interval_covered": True,
                        "label_status": "TRUSTED",
                        "occurred_at_utc": predicted_at
                        + timedelta(minutes=1),
                    },
                )
                connection.execute(
                    quality.insert(),
                    {
                        "id": 1,
                        "run_id": "run-1",
                        "training_weight": 1.0,
                    },
                )
                connection.execute(
                    reviews.insert(),
                    [
                        {
                            "id": 1,
                            "outcome_id": 1,
                            "review_version": 1,
                            "action": "ACCEPT_CORRECTION",
                            "corrected_project_price": 105,
                        },
                        {
                            "id": 2,
                            "outcome_id": 1,
                            "review_version": 2,
                            "action": "REJECT_LABEL",
                            "corrected_project_price": None,
                        },
                    ],
                )
            engine.dispose()

            records = load_shadow_score_records(database_url)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].label_status, "REJECTED")
        self.assertFalse(records[0].training_eligible)
        self.assertEqual(records[0].actual_project_price, 100)


if __name__ == "__main__":
    unittest.main()
