#!/usr/bin/env python3
"""Train and evaluate a non-authoritative CatBoost residual challenger.

Input must be the compact reviewed export created by
``export_coin_residual_shadow.py``.  This command never writes an active
artifact and refuses to run without an explicit Shadow acknowledgement.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.residual_research import (
    RESIDUAL_RESEARCH_SCHEMA,
    chronological_split,
    feature_vector,
    normalize_rows,
)


CANDIDATE_NAME = "CATBOOST_RESIDUAL_V1_SHADOW"
MINIMUM_TRAINING_ROWS = 80
TARGET_COVERAGE_PERCENT = 85.0


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("residual_artifact_must_be_outside_repository")
    return resolved


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("schema_version") != RESIDUAL_RESEARCH_SCHEMA:
                raise ValueError("residual_research_schema_mismatch")
            rows.append(value)
    return rows


def _matrix(rows) -> tuple[list[list[float | str]], list[float], list[float], list[str]]:
    vectors = [feature_vector(row) for row in rows]
    columns = list(vectors[0])
    matrix = [[vector[column] for column in columns] for vector in vectors]
    targets = [row.residual_ratio for row in rows]
    weights = [row.training_weight for row in rows]
    return matrix, targets, weights, columns


def _weighted_quantile(values: list[float], weights: list[float], probability: float) -> float:
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(weights)
    if total <= 0:
        raise ValueError("non_positive_calibration_weight")
    threshold = total * probability
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _mape(actuals: list[float], predictions: list[float]) -> float:
    return sum(abs(actual - predicted) / actual for actual, predicted in zip(actuals, predictions)) / len(actuals) * 100.0


def train(rows) -> tuple[dict[str, Any], Any | None]:
    """Run fit/calibration/test in chronological order; no promotion path."""

    fit_rows, calibration_rows, test_rows = chronological_split(rows)
    if len(fit_rows) < MINIMUM_TRAINING_ROWS:
        return {
            "status": "SHADOW_NOT_TRAINED",
            "reason": "INSUFFICIENT_REVIEWED_TRAINING_ROWS",
            "split": {"fit": len(fit_rows), "calibration": len(calibration_rows), "test": len(test_rows)},
        }, None
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        return {
            "status": "SHADOW_NOT_TRAINED",
            "reason": "CATBOOST_UNAVAILABLE",
            "split": {"fit": len(fit_rows), "calibration": len(calibration_rows), "test": len(test_rows)},
        }, None

    fit_x, fit_y, fit_w, columns = _matrix(fit_rows)
    calibration_x, calibration_y, calibration_w, calibration_columns = _matrix(calibration_rows)
    test_x, test_y, _, test_columns = _matrix(test_rows)
    if columns != calibration_columns or columns != test_columns:
        raise ValueError("residual_feature_columns_inconsistent")
    categorical = [index for index, name in enumerate(columns) if name in {"commodity", "settlement", "trade_form"}]
    model = CatBoostRegressor(
        loss_function="RMSE",
        iterations=250,
        depth=5,
        learning_rate=0.04,
        l2_leaf_reg=8.0,
        random_seed=20260801,
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(fit_x, fit_y, sample_weight=fit_w, cat_features=categorical)
    calibration_prediction = list(model.predict(calibration_x))
    residual_radius = _weighted_quantile(
        [abs(actual - predicted) for actual, predicted in zip(calibration_y, calibration_prediction)],
        calibration_w,
        0.90,
    )
    test_residual_prediction = list(model.predict(test_x))
    baseline = [row.baseline_project_price for row in test_rows]
    actuals = [row.actual_project_price for row in test_rows]
    candidate = [base * (1.0 + residual) for base, residual in zip(baseline, test_residual_prediction)]
    lower = [base * (1.0 + residual - residual_radius) for base, residual in zip(baseline, test_residual_prediction)]
    upper = [base * (1.0 + residual + residual_radius) for base, residual in zip(baseline, test_residual_prediction)]
    coverage = sum(low <= actual <= high for low, actual, high in zip(lower, actuals, upper)) / len(actuals) * 100.0
    baseline_mape = _mape(actuals, baseline)
    candidate_mape = _mape(actuals, candidate)
    report = {
        "status": "SHADOW_EVALUATED_NOT_PROMOTED",
        "candidate": CANDIDATE_NAME,
        "split": {"method": "chronological_60_20_20_timestamp_purged", "fit": len(fit_rows), "calibration": len(calibration_rows), "test": len(test_rows)},
        "feature_columns": columns,
        "metrics": {
            "baseline_mape_percent": round(baseline_mape, 6),
            "candidate_mape_percent": round(candidate_mape, 6),
            "relative_mape_improvement_percent": round((baseline_mape - candidate_mape) / baseline_mape * 100.0, 6),
            "conformal_residual_radius": round(residual_radius, 8),
            "test_interval_coverage_percent": round(coverage, 6),
        },
        "promotion": {
            "automatic_promotion": False,
            "target_coverage_percent": TARGET_COVERAGE_PERCENT,
            "coverage_target_met": coverage >= TARGET_COVERAGE_PERCENT,
            "reason": "OWNER_REVIEW_AND_FULL_MARKET_SLICE_GATES_REQUIRED",
        },
    }
    return report, model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model-output", type=Path)
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    report_path = _outside_repository(args.report)
    if args.model_output is not None:
        _outside_repository(args.model_output)
    normalized = normalize_rows(_load(args.input))
    report, model = train(normalized)
    report["input_rows"] = len(normalized)
    _write_json_atomic(report_path, report)
    if model is not None and args.model_output is not None:
        model_path = args.model_output.expanduser().resolve()
        model_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_model = model_path.with_suffix(model_path.suffix + ".tmp")
        model.save_model(str(temporary_model))
        os.chmod(temporary_model, 0o600)
        temporary_model.replace(model_path)
    print(json.dumps({"status": report["status"], "report": str(report_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
