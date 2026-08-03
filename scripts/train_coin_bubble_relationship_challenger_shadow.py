#!/usr/bin/env python3
"""Evaluate an optional CatBoost coin-bubble challenger in Shadow only."""

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

from core.market_intelligence.coin_relationship_challenger import (
    CHALLENGER_VERSION,
    CoinBubbleRow,
    chronological_split,
    median_baseline,
    readiness,
)
from core.market_intelligence.relationship_ledger import iter_labels


DATASET_SCHEMA = "COIN_INTRINSIC_RELATIONSHIP_DATASET_V1_SHADOW_20260803"


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("challenger_output_must_be_outside_repository")
    return resolved


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("challenger_timestamp_timezone_required")
    return parsed


def _rows_from_items(items) -> list[CoinBubbleRow]:
    rows: list[CoinBubbleRow] = []
    for item in items:
        if item.get("schema_version") != DATASET_SCHEMA:
            raise ValueError("challenger_dataset_schema_invalid")
        features = {
            str(name): float(value)
            for name, value in dict(item.get("features") or {}).items()
        }
        rows.append(
            CoinBubbleRow(
                available_at_utc=_timestamp(item["available_at_utc"]),
                realized_at_utc=_timestamp(item["realized_at_utc"]),
                commodity=str(item["commodity"]),
                settlement=str(item["settlement"]),
                trade_form=str(item["trade_form"]),
                bubble_ratio=float(item["bubble_ratio"]),
                features=features,
            )
        )
    return rows


def _load_rows(path: Path) -> list[CoinBubbleRow]:
    with path.open(encoding="utf-8") as handle:
        return _rows_from_items(json.loads(line) for line in handle if line.strip())


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument("--ledger", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--execute-catboost", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    report_path = _outside_repository(args.report)
    if args.dataset is not None:
        rows = _load_rows(_outside_repository(args.dataset))
        input_kind = "EPHEMERAL_DATASET"
    else:
        rows = _rows_from_items(iter_labels(_outside_repository(args.ledger)))
        input_kind = "DURABLE_LEDGER"
    split = chronological_split(rows)
    gate = readiness(split)
    report = {
        "version": CHALLENGER_VERSION,
        "status": "SHADOW_CHALLENGER_NOT_PROMOTED",
        "dataset_rows": len(rows),
        "input_kind": input_kind,
        "split": {
            "method": "chronological_60_20_20_availability_time_with_15m_purge",
            "validation_start_utc": split.validation_start_utc.isoformat(),
            "test_start_utc": split.test_start_utc.isoformat(),
        },
        "readiness": gate,
        "baseline_untouched_test": median_baseline(split.fit, split.test),
        "automatic_promotion": False,
    }
    if not gate["ready"]:
        report["reason"] = "CHALLENGER_GATED_" + "+".join(gate["reasons"])
        _write_json_atomic(report_path, report)
        print(json.dumps({"status": report["status"], "reason": report["reason"]}))
        return 0
    if not args.execute_catboost:
        report["reason"] = "CATBOOST_EXECUTION_REQUIRES_EXPLICIT_FLAG"
        _write_json_atomic(report_path, report)
        print(json.dumps({"status": report["status"], "reason": report["reason"]}))
        return 0
    # Deliberately import only after every data gate.  CatBoost is optional and
    # never enters the application image or runtime inference path here.
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        report["reason"] = "CATBOOST_OPTIONAL_DEPENDENCY_UNAVAILABLE"
        _write_json_atomic(report_path, report)
        print(json.dumps({"status": report["status"], "reason": report["reason"]}))
        return 0
    feature_names = sorted(set.intersection(*(set(row.features) for row in split.fit)))
    if not feature_names:
        report["reason"] = "NO_COMMON_NUMERIC_FEATURES"
        _write_json_atomic(report_path, report)
        return 0
    def matrix(rows):
        return [[row.features[name] for name in feature_names] for row in rows]
    model = CatBoostRegressor(
        loss_function="MAE",
        iterations=300,
        depth=5,
        learning_rate=0.04,
        random_seed=20260803,
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(matrix(split.fit), [row.bubble_ratio for row in split.fit])
    predicted = model.predict(matrix(split.test))
    errors = [abs(row.bubble_ratio - float(value)) for row, value in zip(split.test, predicted)]
    report["catboost_untouched_test"] = {
        "mae": round(sum(errors) / len(errors), 8),
        "feature_importance": [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in sorted(
                zip(feature_names, model.get_feature_importance()),
                key=lambda item: item[1], reverse=True
            )[:30]
        ],
    }
    report["reason"] = "CATBOOST_EVALUATED_SHADOW_ONLY_REQUIRES_HUMAN_REVIEW"
    _write_json_atomic(report_path, report)
    print(json.dumps({"status": report["status"], "reason": report["reason"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
