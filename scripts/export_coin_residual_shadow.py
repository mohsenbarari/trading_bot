#!/usr/bin/env python3
"""Export privacy-minimized reviewed Shadow outcomes for offline research.

The export intentionally contains no offer/trade public ID, user ID, subject
fingerprint, raw text, or prediction/run ID.  It cannot promote a model.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from sqlalchemy import and_, create_engine, func, select
from sqlalchemy.orm import aliased


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.market_intelligence.residual_research import RESIDUAL_RESEARCH_SCHEMA
from models.coin_intelligence_shadow import (
    CoinIntelligenceShadowFeatureSnapshot,
    CoinIntelligenceShadowOutcome,
    CoinIntelligenceShadowPrediction,
    CoinIntelligenceShadowQualityDecision,
    CoinIntelligenceShadowReview,
    CoinIntelligenceShadowRun,
)


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("residual_export_must_be_outside_repository")
    return resolved


def _latest_review_subquery():
    return (
        select(
            CoinIntelligenceShadowReview.outcome_id.label("outcome_id"),
            func.max(CoinIntelligenceShadowReview.review_version).label(
                "review_version"
            ),
        )
        .group_by(CoinIntelligenceShadowReview.outcome_id)
        .subquery()
    )


def rows(database_url: str) -> list[dict]:
    latest_version = _latest_review_subquery()
    latest_review = aliased(CoinIntelligenceShadowReview)
    statement = (
        select(
            CoinIntelligenceShadowOutcome.occurred_at_utc,
            CoinIntelligenceShadowPrediction.canonical_commodity,
            CoinIntelligenceShadowPrediction.settlement,
            CoinIntelligenceShadowPrediction.trade_form,
            CoinIntelligenceShadowPrediction.center_project_price,
            CoinIntelligenceShadowOutcome.actual_project_price,
            CoinIntelligenceShadowFeatureSnapshot.features,
            CoinIntelligenceShadowQualityDecision.training_weight,
            latest_review.action.label("review_action"),
            latest_review.corrected_project_price,
        )
        .join(
            CoinIntelligenceShadowRun,
            CoinIntelligenceShadowRun.id == CoinIntelligenceShadowPrediction.run_id,
        )
        .join(
            CoinIntelligenceShadowOutcome,
            CoinIntelligenceShadowOutcome.prediction_id == CoinIntelligenceShadowPrediction.id,
        )
        .join(
            CoinIntelligenceShadowFeatureSnapshot,
            CoinIntelligenceShadowFeatureSnapshot.run_id == CoinIntelligenceShadowRun.id,
        )
        .join(
            CoinIntelligenceShadowQualityDecision,
            CoinIntelligenceShadowQualityDecision.run_id == CoinIntelligenceShadowRun.id,
        )
        .outerjoin(
            latest_version,
            latest_version.c.outcome_id == CoinIntelligenceShadowOutcome.id,
        )
        .outerjoin(
            latest_review,
            and_(
                latest_review.outcome_id == latest_version.c.outcome_id,
                latest_review.review_version == latest_version.c.review_version,
            ),
        )
        .where(
            CoinIntelligenceShadowRun.mode == "shadow",
            CoinIntelligenceShadowPrediction.model_role == "PRIMARY_SHADOW",
            CoinIntelligenceShadowPrediction.is_authoritative.is_(False),
            CoinIntelligenceShadowRun.as_of_utc < CoinIntelligenceShadowOutcome.occurred_at_utc,
            CoinIntelligenceShadowQualityDecision.training_weight > 0,
            latest_review.action.in_(("ACCEPT_ORIGINAL", "ACCEPT_CORRECTION")),
        )
        .order_by(CoinIntelligenceShadowOutcome.occurred_at_utc)
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            output = []
            for row in connection.execute(statement).mappings():
                actual = (
                    row["corrected_project_price"]
                    if row["review_action"] == "ACCEPT_CORRECTION"
                    and row["corrected_project_price"] is not None
                    else row["actual_project_price"]
                )
                if (
                    row["center_project_price"] is None
                    or actual is None
                    or row["features"] is None
                ):
                    continue
                output.append(
                    {
                        "schema_version": RESIDUAL_RESEARCH_SCHEMA,
                        "occurred_at_utc": row["occurred_at_utc"].astimezone(
                            timezone.utc
                        ).isoformat(),
                        "commodity": str(row["canonical_commodity"] or ""),
                        "settlement": str(row["settlement"]),
                        "trade_form": str(row["trade_form"]),
                        "baseline_project_price": int(row["center_project_price"]),
                        "actual_project_price": int(actual),
                        "label_status": "REVIEWED",
                        "training_eligible": True,
                        "training_weight": float(row["training_weight"]),
                        "features": dict(row["features"]),
                    }
                )
            return output
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    try:
        output = _outside_repository(args.output)
        prepared = rows(settings.sync_database_url)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in prepared:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(output)
        print(json.dumps({"status": "RESIDUAL_EXPORT_COMPLETE", "rows": len(prepared)}))
        return 0
    except Exception as exc:
        # Do not emit database DSNs or SQL errors: a training exporter is not
        # allowed to leak connection details to logs.
        print(json.dumps({"status": "RESIDUAL_EXPORT_FAILED", "reason": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
