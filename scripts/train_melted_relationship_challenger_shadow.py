#!/usr/bin/env python3
"""Evaluate the optional non-linear melted-market challenger in Shadow mode."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.melted_relationship_challenger import (
    MELTED_CHALLENGER_VERSION,
    MeltedRelationshipRow,
    chronological_split,
    median_baseline,
    readiness,
)
from core.market_intelligence.relationship_ledger import iter_melted_features


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("melted_challenger_runtime_path_inside_repository")
    return resolved


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("melted_challenger_timestamp_timezone_required")
    return parsed


def _rows(ledger: Path) -> list[MeltedRelationshipRow]:
    return [
        MeltedRelationshipRow(
            available_at_utc=_time(item["available_at_utc"]),
            realized_at_utc=_time(item["realized_at_utc"]),
            target_market=str(item["target_market"]),
            target_return_bps=float(item["target_return_bps"]),
            features={str(name): float(value) for name, value in item["features"].items()},
        )
        for item in iter_melted_features(ledger)
        if str(item["target_market"]).startswith(("PAPER:", "PHYSICAL:"))
    ]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--execute-catboost", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    ledger = _outside_repository(args.ledger)
    report_path = _outside_repository(args.report)
    split = chronological_split(_rows(ledger))
    gate = readiness(split)
    report = {
        "version": MELTED_CHALLENGER_VERSION,
        "status": "SHADOW_MELTED_CHALLENGER_NOT_PROMOTED",
        "readiness": gate,
        "baseline_untouched_test": median_baseline(split.fit, split.test),
        "automatic_promotion": False,
    }
    if not gate["ready"]:
        report["reason"] = "MELTED_CHALLENGER_GATED_" + "+".join(gate["reasons"])
        _write(report_path, report)
        print(json.dumps({"status": report["status"], "reason": report["reason"]}))
        return 0
    if not args.execute_catboost:
        report["reason"] = "CATBOOST_EXECUTION_REQUIRES_EXPLICIT_FLAG"
        _write(report_path, report)
        print(json.dumps({"status": report["status"], "reason": report["reason"]}))
        return 0
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        report["reason"] = "CATBOOST_OPTIONAL_DEPENDENCY_UNAVAILABLE"
        _write(report_path, report)
        print(json.dumps({"status": report["status"], "reason": report["reason"]}))
        return 0
    names = sorted(set.intersection(*(set(row.features) for row in split.fit)))
    if not names:
        report["reason"] = "NO_COMMON_NUMERIC_FEATURES"
        _write(report_path, report)
        return 0
    matrix = lambda rows: [[row.features[name] for name in names] for row in rows]
    model = CatBoostRegressor(
        loss_function="MAE", iterations=350, depth=6, learning_rate=0.04,
        random_seed=20260803, verbose=False, allow_writing_files=False,
    )
    model.fit(matrix(split.fit), [row.target_return_bps for row in split.fit])
    predictions = model.predict(matrix(split.test))
    errors = [abs(row.target_return_bps - float(predicted)) for row, predicted in zip(split.test, predictions)]
    report["catboost_untouched_test"] = {
        "mae_bps": round(sum(errors) / len(errors), 6),
        "feature_importance": [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in sorted(zip(names, model.get_feature_importance()), key=lambda item: item[1], reverse=True)[:30]
        ],
    }
    report["reason"] = "CATBOOST_EVALUATED_SHADOW_ONLY_REQUIRES_HUMAN_REVIEW"
    _write(report_path, report)
    print(json.dumps({"status": report["status"], "reason": report["reason"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
