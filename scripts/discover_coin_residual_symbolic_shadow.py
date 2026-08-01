#!/usr/bin/env python3
"""Run optional PySR discovery over frozen reviewed residual data.

Symbolic regression is an explanation/research producer only.  Its equations
are written to a report and cannot enter the running estimator, change a
weight, or satisfy a promotion gate by themselves.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.residual_research import (
    RESIDUAL_RESEARCH_SCHEMA,
    chronological_split,
    feature_vector,
    normalize_rows,
)


SYMBOLIC_VERSION = "PYSR_RESIDUAL_DISCOVERY_V1_SHADOW_20260801"
MINIMUM_FIT_ROWS = 160


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("symbolic_report_must_be_outside_repository")
    return resolved


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _load(path: Path) -> list[dict]:
    values = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("schema_version") != RESIDUAL_RESEARCH_SCHEMA:
                raise ValueError("residual_research_schema_mismatch")
            values.append(value)
    return values


def _numeric_matrix(rows):
    # Symbolic formulas must remain interpretable and unit-bounded. Categorical
    # commodity/settlement fields are evaluated in separate future cohorts,
    # rather than encoded as arbitrary numbers.
    vectors = [feature_vector(row) for row in rows]
    names = [
        "minute_of_day", "weekday_iso", "candidate_banking_window",
        "direction_score", "volatility_percent", "confidence",
        "agreement_score", "cross_source_disagreement", "intrinsic_toman_log",
        "melted_observed", "usd_observed", "usdt_observed", "ime_observed",
    ]
    return [[float(vector[name]) for name in names] for vector in vectors], [row.residual_ratio for row in rows], names


def discover(rows, *, execute: bool, niterations: int) -> dict:
    fit_rows, validation_rows, test_rows = chronological_split(rows)
    report = {
        "version": SYMBOLIC_VERSION,
        "status": "SHADOW_RESEARCH_NOT_PROMOTED",
        "split": {
            "method": "chronological_60_20_20_timestamp_purged",
            "fit": len(fit_rows),
            "validation": len(validation_rows),
            "test": len(test_rows),
        },
        "promotion": {
            "automatic_promotion": False,
            "reason": "SYMBOLIC_FORMULAS_REQUIRE_SEPARATE_WALK_FORWARD_AND_OWNER_REVIEW",
        },
    }
    if len(fit_rows) < MINIMUM_FIT_ROWS:
        report.update({"reason": "INSUFFICIENT_REVIEWED_FIT_ROWS"})
        return report
    if not execute:
        report.update({"reason": "PYSR_EXECUTION_REQUIRES_EXPLICIT_FLAG"})
        return report
    try:
        from pysr import PySRRegressor
    except ImportError:
        report.update({"reason": "PYSR_UNAVAILABLE"})
        return report
    fit_x, fit_y, names = _numeric_matrix(fit_rows)
    validation_x, validation_y, _ = _numeric_matrix(validation_rows)
    test_x, test_y, _ = _numeric_matrix(test_rows)
    model = PySRRegressor(
        niterations=niterations,
        binary_operators=["+", "-", "*", "/"],
        unary_operators=[],
        maxsize=12,
        parsimony=0.02,
        model_selection="best",
        progress=False,
        temp_equation_file=True,
        random_state=20260801,
    )
    model.fit(fit_x, fit_y, variable_names=names)
    validation_prediction = model.predict(validation_x)
    test_prediction = model.predict(test_x)
    mse = lambda actual, prediction: sum((a - float(p)) ** 2 for a, p in zip(actual, prediction)) / len(actual)
    report.update(
        {
            "reason": "FORMULAS_DISCOVERED_RESEARCH_ONLY",
            "variables": names,
            "selected_equation": str(model.sympy()),
            "validation_mse": round(mse(validation_y, validation_prediction), 10),
            "untouched_test_mse": round(mse(test_y, test_prediction), 10),
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--execute-pysr", action="store_true")
    parser.add_argument("--niterations", type=int, default=40)
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    if not 5 <= args.niterations <= 100:
        raise SystemExit("niterations must be between 5 and 100")
    report_path = _outside_repository(args.report)
    normalized = normalize_rows(_load(args.input))
    report = discover(normalized, execute=args.execute_pysr, niterations=args.niterations)
    report["input_rows"] = len(normalized)
    _write_json_atomic(report_path, report)
    print(json.dumps({"status": report["status"], "reason": report.get("reason")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
