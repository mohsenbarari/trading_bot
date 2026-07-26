"""Read-only aggregation for persisted Shadow prediction outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from statistics import mean, median
from typing import Iterable, Mapping

from sqlalchemy import and_, create_engine, func, select
from sqlalchemy.orm import aliased

from models.coin_intelligence_shadow import (
    CoinIntelligenceShadowOutcome,
    CoinIntelligenceShadowPrediction,
    CoinIntelligenceShadowQualityDecision,
    CoinIntelligenceShadowReview,
    CoinIntelligenceShadowRun,
)


PROMOTION_LABEL_STATUSES = frozenset({"REVIEWED", "TRUSTED"})
REPORT_SCHEMA_VERSION = "COIN_SHADOW_EVALUATION_REPORT_V2"


@dataclass(frozen=True, slots=True)
class ShadowScoreRecord:
    run_id: str
    model_role: str
    candidate_name: str
    commodity: str
    settlement: str
    trade_form: str
    center_project_price: int | None
    lower_project_price: int | None
    upper_project_price: int | None
    actual_project_price: int
    absolute_percent_error: float | None
    signed_percent_error: float | None
    interval_covered: bool | None
    label_status: str
    training_eligible: bool

    @property
    def promotion_eligible(self) -> bool:
        return bool(
            self.training_eligible
            and self.label_status.upper() in PROMOTION_LABEL_STATUSES
        )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            math.ceil((percentile / 100.0) * len(ordered)) - 1,
        ),
    )
    return ordered[index]


def _rounded(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None


def _aggregate_group(records: list[ShadowScoreRecord]) -> dict:
    point_errors = [
        float(item.absolute_percent_error)
        for item in records
        if item.absolute_percent_error is not None
    ]
    signed_errors = [
        float(item.signed_percent_error)
        for item in records
        if item.signed_percent_error is not None
    ]
    interval_results = [
        bool(item.interval_covered)
        for item in records
        if item.interval_covered is not None
    ]
    widths = [
        (
            int(item.upper_project_price)
            - int(item.lower_project_price)
        )
        / int(item.actual_project_price)
        * 100.0
        for item in records
        if item.lower_project_price is not None
        and item.upper_project_price is not None
        and int(item.actual_project_price) > 0
    ]
    return {
        "outcome_count": len(records),
        "point_prediction_count": len(point_errors),
        "abstention_count": len(records) - len(point_errors),
        "mape_percent": _rounded(mean(point_errors))
        if point_errors
        else None,
        "median_absolute_percent_error": _rounded(median(point_errors))
        if point_errors
        else None,
        "p90_absolute_percent_error": _rounded(
            _percentile(point_errors, 90.0)
        ),
        "signed_bias_percent": _rounded(mean(signed_errors))
        if signed_errors
        else None,
        "interval_evaluable_count": len(interval_results),
        "interval_coverage_percent": _rounded(
            mean(interval_results) * 100.0
        )
        if interval_results
        else None,
        "mean_interval_width_percent_of_actual": _rounded(mean(widths))
        if widths
        else None,
    }


def _build_slices(
    materialized: list[ShadowScoreRecord],
) -> list[dict]:
    grouped: dict[tuple[str, str, str, str, str], list] = {}
    for item in materialized:
        key = (
            item.model_role,
            item.candidate_name,
            item.commodity,
            item.settlement,
            item.trade_form,
        )
        grouped.setdefault(key, []).append(item)

    slices = []
    for key in sorted(grouped):
        model_role, candidate, commodity, settlement, trade_form = key
        cohort = grouped[key]
        slices.append(
            {
                "model_role": model_role,
                "candidate_name": candidate,
                "commodity": commodity,
                "settlement": settlement,
                "trade_form": trade_form,
                **_aggregate_group(cohort),
            }
        )
    return slices


def _paired_candidate_comparisons(
    records: list[ShadowScoreRecord],
) -> list[dict]:
    by_run: dict[str, list[ShadowScoreRecord]] = {}
    for item in records:
        by_run.setdefault(item.run_id, []).append(item)
    comparisons: dict[tuple[str, str, str, str], list[tuple]] = {}
    for cohort in by_run.values():
        primary = next(
            (
                item
                for item in cohort
                if item.model_role == "PRIMARY_SHADOW"
            ),
            None,
        )
        if primary is None or primary.absolute_percent_error is None:
            continue
        for candidate in cohort:
            if (
                candidate.model_role != "CANDIDATE_SHADOW"
                or candidate.absolute_percent_error is None
            ):
                continue
            key = (
                candidate.candidate_name,
                candidate.commodity,
                candidate.settlement,
                candidate.trade_form,
            )
            comparisons.setdefault(key, []).append(
                (
                    float(primary.absolute_percent_error),
                    float(candidate.absolute_percent_error),
                    primary.interval_covered,
                    candidate.interval_covered,
                )
            )
    result = []
    for key in sorted(comparisons):
        candidate, commodity, settlement, trade_form = key
        pairs = comparisons[key]
        improvements = [
            primary_error - candidate_error
            for primary_error, candidate_error, _, _ in pairs
        ]
        primary_coverage = [
            bool(primary_hit)
            for _, _, primary_hit, _ in pairs
            if primary_hit is not None
        ]
        candidate_coverage = [
            bool(candidate_hit)
            for _, _, _, candidate_hit in pairs
            if candidate_hit is not None
        ]
        primary_coverage_percent = (
            mean(primary_coverage) * 100.0
            if primary_coverage
            else None
        )
        candidate_coverage_percent = (
            mean(candidate_coverage) * 100.0
            if candidate_coverage
            else None
        )
        result.append(
            {
                "candidate_name": candidate,
                "commodity": commodity,
                "settlement": settlement,
                "trade_form": trade_form,
                "paired_count": len(pairs),
                "mean_absolute_error_improvement_percentage_points": (
                    _rounded(mean(improvements))
                ),
                "candidate_strictly_better_percent": _rounded(
                    mean(value > 0 for value in improvements) * 100.0
                ),
                "primary_interval_coverage_percent": _rounded(
                    primary_coverage_percent
                ),
                "candidate_interval_coverage_percent": _rounded(
                    candidate_coverage_percent
                ),
                "coverage_delta_percentage_points": (
                    _rounded(
                        candidate_coverage_percent
                        - primary_coverage_percent
                    )
                    if candidate_coverage_percent is not None
                    and primary_coverage_percent is not None
                    else None
                ),
            }
        )
    return result


def aggregate_shadow_scores(
    records: Iterable[ShadowScoreRecord],
) -> dict:
    """Aggregate without exposing exact prices or subject identifiers."""

    materialized = list(records)
    slices = _build_slices(materialized)
    promotion_records = [
        item for item in materialized if item.promotion_eligible
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "operational_unreviewed_and_reviewed": {
            "record_count": len(materialized),
            "slices": slices,
            "paired_candidate_comparisons": (
                _paired_candidate_comparisons(materialized)
            ),
        },
        "promotion_eligible_only": {
            "record_count": len(promotion_records),
            "slices": _build_slices(promotion_records),
            "paired_candidate_comparisons": (
                _paired_candidate_comparisons(promotion_records)
            ),
        },
        "promotion_warning": (
            "NO_PROMOTION_ELIGIBLE_REVIEWED_OUTCOMES"
            if not promotion_records
            else None
        ),
    }


def load_shadow_score_records(
    database_url: str,
    *,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> list[ShadowScoreRecord]:
    """Load immutable outcome rows through the synchronous read path."""

    latest_review_version = (
        select(
            CoinIntelligenceShadowReview.outcome_id.label("outcome_id"),
            func.max(
                CoinIntelligenceShadowReview.review_version
            ).label("review_version"),
        )
        .group_by(CoinIntelligenceShadowReview.outcome_id)
        .subquery()
    )
    latest_review = aliased(CoinIntelligenceShadowReview)
    statement = (
        select(
            CoinIntelligenceShadowPrediction.run_id,
            CoinIntelligenceShadowPrediction.model_role,
            CoinIntelligenceShadowPrediction.candidate_name,
            CoinIntelligenceShadowPrediction.canonical_commodity,
            CoinIntelligenceShadowPrediction.settlement,
            CoinIntelligenceShadowPrediction.trade_form,
            CoinIntelligenceShadowPrediction.center_project_price,
            CoinIntelligenceShadowPrediction.lower_project_price,
            CoinIntelligenceShadowPrediction.upper_project_price,
            CoinIntelligenceShadowOutcome.actual_project_price,
            CoinIntelligenceShadowOutcome.absolute_percent_error,
            CoinIntelligenceShadowOutcome.signed_percent_error,
            CoinIntelligenceShadowOutcome.interval_covered,
            CoinIntelligenceShadowOutcome.label_status,
            CoinIntelligenceShadowRun.training_eligible,
            latest_review.action.label("review_action"),
            latest_review.corrected_project_price,
            CoinIntelligenceShadowQualityDecision.training_weight,
        )
        .join(
            CoinIntelligenceShadowRun,
            CoinIntelligenceShadowRun.id
            == CoinIntelligenceShadowPrediction.run_id,
        )
        .join(
            CoinIntelligenceShadowOutcome,
            CoinIntelligenceShadowOutcome.prediction_id
            == CoinIntelligenceShadowPrediction.id,
        )
        .outerjoin(
            latest_review_version,
            latest_review_version.c.outcome_id
            == CoinIntelligenceShadowOutcome.id,
        )
        .outerjoin(
            latest_review,
            and_(
                latest_review.outcome_id
                == latest_review_version.c.outcome_id,
                latest_review.review_version
                == latest_review_version.c.review_version,
            ),
        )
        .outerjoin(
            CoinIntelligenceShadowQualityDecision,
            CoinIntelligenceShadowQualityDecision.run_id
            == CoinIntelligenceShadowRun.id,
        )
        .where(
            CoinIntelligenceShadowRun.mode == "shadow",
            CoinIntelligenceShadowPrediction.is_authoritative.is_(False),
            CoinIntelligenceShadowRun.as_of_utc
            < CoinIntelligenceShadowOutcome.occurred_at_utc,
        )
    )
    if start_utc is not None:
        statement = statement.where(
            CoinIntelligenceShadowOutcome.occurred_at_utc >= start_utc
        )
    if end_utc is not None:
        statement = statement.where(
            CoinIntelligenceShadowOutcome.occurred_at_utc < end_utc
        )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows: Iterable[Mapping] = connection.execute(
                statement
            ).mappings()
            output = []
            for row in rows:
                action = str(row["review_action"] or "").upper()
                accepted = action in {
                    "ACCEPT_ORIGINAL",
                    "ACCEPT_CORRECTION",
                }
                actual = (
                    int(row["corrected_project_price"])
                    if action == "ACCEPT_CORRECTION"
                    and row["corrected_project_price"] is not None
                    else int(row["actual_project_price"])
                )
                center = row["center_project_price"]
                signed_error = (
                    (float(center) - actual) / actual * 100.0
                    if center is not None
                    else None
                )
                lower = row["lower_project_price"]
                upper = row["upper_project_price"]
                covered = (
                    int(lower) <= actual <= int(upper)
                    if lower is not None and upper is not None
                    else None
                )
                quality_weight = row["training_weight"]
                quality_allowed = (
                    quality_weight is not None
                    and float(quality_weight) > 0
                )
                has_review = bool(action)
                if accepted:
                    label_status = "REVIEWED"
                elif action == "REJECT_LABEL":
                    label_status = "REJECTED"
                elif action == "AMBIGUOUS":
                    label_status = "AMBIGUOUS"
                elif has_review:
                    label_status = "UNREVIEWED"
                else:
                    label_status = str(row["label_status"])
                training_eligible = bool(
                    quality_allowed
                    and (
                        accepted
                        if has_review
                        else row["training_eligible"]
                    )
                )
                output.append(
                    ShadowScoreRecord(
                        run_id=str(row["run_id"]),
                        model_role=str(row["model_role"]),
                        candidate_name=str(row["candidate_name"]),
                        commodity=str(
                            row["canonical_commodity"] or "UNKNOWN"
                        ),
                        settlement=str(row["settlement"]),
                        trade_form=str(row["trade_form"]),
                        center_project_price=row["center_project_price"],
                        lower_project_price=row["lower_project_price"],
                        upper_project_price=row["upper_project_price"],
                        actual_project_price=actual,
                        absolute_percent_error=(
                            abs(signed_error)
                            if signed_error is not None
                            else None
                        ),
                        signed_percent_error=signed_error,
                        interval_covered=covered,
                        label_status=label_status,
                        training_eligible=training_eligible,
                    )
                )
            return output
    finally:
        engine.dispose()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
