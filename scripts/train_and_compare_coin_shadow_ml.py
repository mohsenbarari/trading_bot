#!/usr/bin/env python3
"""Train a sklearn shadow bubble model and compare it with the live estimator.

This never promotes a model.  Artifacts are written under a research directory.
Training uses only labels available at or before each sample time, with a
chronological split and a short purge window around cut boundaries.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, median_absolute_error


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ESTIMATOR_ROOT = REPO_ROOT / "apps" / "coin_rate_estimator"
if str(ESTIMATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ESTIMATOR_ROOT))

from coin_estimator import (  # noqa: E402
    COMMODITY_SPECS,
    PRICE_MULTIPLIER,
    connect_market_db,
    group_training_example,
    load_group_confirmed_trade_labels,
    load_model,
    parse_datetime,
)
from core.market_intelligence.coin_rate_engine import build_coin_rate_estimates  # noqa: E402


SHADOW_VERSION = "coin-bubble-hgb-shadow-v1-20260805"
PURGE = timedelta(minutes=15)
FEATURE_KEYS = (
    "melted_average_toman",
    "melted_latest_toman",
    "usd_average_toman",
    "usdt_average_toman",
    "xauusd_average",
    "melted_vs_global_ratio",
    "market_pressure_score",
    "market_regime_score",
    "market_regime_confidence",
    "source_confidence",
    "quantity",
    "tehran_weekday",
    "tehran_hour",
)


@dataclass(frozen=True)
class Sample:
    available_at: datetime
    commodity: str
    settlement: str
    trade_form: str
    bubble_ratio: float
    observed_project: int
    intrinsic_toman: float
    features: dict[str, float]
    source_weight: float


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = parse_datetime(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _tehran_parts(moment: datetime) -> tuple[int, int]:
    local = moment.astimezone(timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Tehran")
    )
    return local.weekday(), local.hour


def _feature_vector(sample: Sample, feature_keys: tuple[str, ...]) -> list[float]:
    return [float(sample.features.get(key, math.nan)) for key in feature_keys]


def build_samples(
    *,
    market_db: Path,
    conversation_db: Path,
) -> list[Sample]:
    labels = load_group_confirmed_trade_labels(conversation_db)
    samples: list[Sample] = []
    with connect_market_db(market_db) as connection:
        for label in labels:
            example = group_training_example(connection, label)
            if example is None or not example.get("accepted"):
                continue
            moment = _utc(str(example["event_time_utc"]))
            weekday, hour = _tehran_parts(moment)
            features = {
                key: (
                    float(example[key])
                    if example.get(key) is not None
                    and math.isfinite(float(example[key]))
                    else math.nan
                )
                for key in FEATURE_KEYS
                if key
                not in {
                    "tehran_weekday",
                    "tehran_hour",
                    "quantity",
                    "source_confidence",
                }
            }
            features["tehran_weekday"] = float(weekday)
            features["tehran_hour"] = float(hour)
            features["quantity"] = (
                float(example["quantity"])
                if example.get("quantity") is not None
                else math.nan
            )
            features["source_confidence"] = float(example.get("source_confidence") or 1.0)
            # One-hot-ish commodity / settlement / form codes as numeric ids.
            commodity = str(example["commodity_name"])
            settlement = str(example["settlement_type"])
            trade_form = str(example.get("trade_form") or "PHYSICAL")
            features["commodity_code"] = float(
                sorted(COMMODITY_SPECS).index(commodity)
                if commodity in COMMODITY_SPECS
                else -1
            )
            features["settlement_code"] = 0.0 if settlement == "CASH" else 1.0
            features["trade_form_code"] = 0.0 if trade_form == "PHYSICAL" else 1.0
            samples.append(
                Sample(
                    available_at=moment,
                    commodity=commodity,
                    settlement=settlement,
                    trade_form=trade_form,
                    bubble_ratio=float(example["bubble_ratio"]),
                    observed_project=int(example["project_price"]),
                    intrinsic_toman=float(example["intrinsic_toman"]),
                    features=features,
                    source_weight=float(example.get("source_weight") or 1.0),
                )
            )
    samples.sort(key=lambda row: row.available_at)
    return samples


FEATURE_KEYS_FULL = FEATURE_KEYS + (
    "commodity_code",
    "settlement_code",
    "trade_form_code",
)


def chronological_split(
    samples: list[Sample],
) -> tuple[list[Sample], list[Sample], list[Sample], dict[str, Any]]:
    if len(samples) < 30:
        raise SystemExit(f"not_enough_samples:{len(samples)}")
    validation_start = samples[int(len(samples) * 0.60)].available_at
    test_start = samples[int(len(samples) * 0.80)].available_at
    fit: list[Sample] = []
    validation: list[Sample] = []
    test: list[Sample] = []
    purged = 0
    for row in samples:
        if (
            abs(row.available_at - validation_start) <= PURGE
            or abs(row.available_at - test_start) <= PURGE
        ):
            purged += 1
            continue
        if row.available_at < validation_start:
            fit.append(row)
        elif row.available_at < test_start:
            validation.append(row)
        else:
            test.append(row)
    meta = {
        "validation_start_utc": validation_start.isoformat().replace("+00:00", "Z"),
        "test_start_utc": test_start.isoformat().replace("+00:00", "Z"),
        "purged_rows": purged,
        "fit_rows": len(fit),
        "validation_rows": len(validation),
        "test_rows": len(test),
    }
    return fit, validation, test, meta


def _matrix(rows: list[Sample]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray([_feature_vector(row, FEATURE_KEYS_FULL) for row in rows], dtype=float)
    y = np.asarray([row.bubble_ratio for row in rows], dtype=float)
    w = np.asarray([row.source_weight for row in rows], dtype=float)
    return x, y, w


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "mae_bubble": float(mean_absolute_error(y_true, y_pred)),
        "medae_bubble": float(median_absolute_error(y_true, y_pred)),
        "mae_project_price": float(
            mean_absolute_error(
                y_true * 0 + 0,  # placeholder replaced by caller for price
                y_pred * 0,
            )
        )
        if False
        else 0.0,
    }


def project_from_bubble(sample: Sample, bubble: float) -> float:
    return (sample.intrinsic_toman * (1.0 + bubble)) / PRICE_MULTIPLIER


def evaluate_price_mae(rows: list[Sample], bubbles: np.ndarray) -> dict[str, float]:
    predicted = np.asarray(
        [project_from_bubble(row, float(bubble)) for row, bubble in zip(rows, bubbles)],
        dtype=float,
    )
    observed = np.asarray([float(row.observed_project) for row in rows], dtype=float)
    abs_err = np.abs(predicted - observed)
    rel = abs_err / np.maximum(observed, 1.0)
    return {
        "sample_count": int(len(rows)),
        "mae_project": float(np.mean(abs_err)),
        "medae_project": float(np.median(abs_err)),
        "mean_abs_pct": float(np.mean(rel)),
        "median_abs_pct": float(np.median(rel)),
        "mae_bubble": float(
            mean_absolute_error(
                np.asarray([row.bubble_ratio for row in rows]), bubbles
            )
        ),
        "medae_bubble": float(
            median_absolute_error(
                np.asarray([row.bubble_ratio for row in rows]), bubbles
            )
        ),
    }


def baseline_median_bubbles(fit: list[Sample], rows: list[Sample]) -> np.ndarray:
    buckets: dict[tuple[str, str, str], list[float]] = {}
    for row in fit:
        key = (row.commodity, row.settlement, row.trade_form)
        buckets.setdefault(key, []).append(row.bubble_ratio)
    overall = float(np.median([row.bubble_ratio for row in fit])) if fit else 0.0
    preds = []
    for row in rows:
        key = (row.commodity, row.settlement, row.trade_form)
        values = buckets.get(key)
        preds.append(float(np.median(values)) if values else overall)
    return np.asarray(preds, dtype=float)


def live_model_bubbles(model: dict[str, Any], rows: list[Sample]) -> np.ndarray:
    """Use the live model's stored bubble medians when present."""

    by_name = {
        str(item.get("name")): item
        for item in model.get("commodities") or []
        if isinstance(item, dict)
    }
    preds = []
    for row in rows:
        item = by_name.get(row.commodity) or {}
        market_forms = ((item.get("market_forms") or {}).get(row.settlement) or {}).get(
            row.trade_form
        ) or {}
        bubble = market_forms.get("bubble_ratio_median")
        if bubble is None:
            settlements = (item.get("settlements") or {}).get(row.settlement) or {}
            bubble = settlements.get("bubble_ratio_median")
        if bubble is None:
            bubble = 0.0
        preds.append(float(bubble))
    return np.asarray(preds, dtype=float)


def commodity_engine_price_errors(
    *,
    market_store: Path,
    rows: list[Sample],
    maximum_rows: int = 40,
) -> dict[str, float]:
    # Commodity names in the structural engine are English codes; conversation
    # labels use Persian display names.  Map the common ones for a fair slice.
    name_to_code = {
        "امام": "IMAM",
        "بهار": "BAHAR",
        "نیم بهار": "HALF_BAHAR",
        "ربع بهار": "QUARTER_BAHAR",
        "نیم تاریخ پایین": "HALF_LOW_DATE",
        "ربع تاریخ پایین": "QUARTER_LOW_DATE",
        "یک گرمی": "ONE_GRAM",
    }
    selected = rows[:: max(1, len(rows) // maximum_rows)][:maximum_rows]
    connection = sqlite3.connect(f"file:{market_store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        abs_err: list[float] = []
        covered = 0
        for row in selected:
            code = name_to_code.get(row.commodity)
            if code is None:
                continue
            estimates = build_coin_rate_estimates(
                connection, as_of_utc=row.available_at
            )
            match = None
            for item in estimates:
                payload = item.to_dict() if hasattr(item, "to_dict") else item
                if (
                    payload.get("commodity_code") == code
                    and payload.get("settlement_term") == row.settlement
                    and payload.get("status") == "ESTIMATED"
                    and payload.get("estimated_project_price") is not None
                ):
                    match = payload
                    break
            if match is None:
                continue
            covered += 1
            abs_err.append(
                abs(float(match["estimated_project_price"]) - float(row.observed_project))
            )
    finally:
        connection.close()
    if not abs_err:
        return {
            "sample_count": 0,
            "attempted_rows": len(selected),
            "mae_project": None,
            "medae_project": None,
        }
    arr = np.asarray(abs_err, dtype=float)
    return {
        "sample_count": int(len(arr)),
        "attempted_rows": len(selected),
        "covered_rows": covered,
        "mae_project": float(np.mean(arr)),
        "medae_project": float(np.median(arr)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-db",
        type=Path,
        default=Path(
            "/srv/trading-bot-three-site-staging-data/coin-intelligence/"
            "apps/telegram-price-poc/data/market_prices.sqlite3"
        ),
    )
    parser.add_argument(
        "--conversation-db",
        type=Path,
        default=Path(
            "/srv/trading-bot-three-site-staging-data/coin-intelligence/"
            "apps/coin-intelligence/data/conversation_events.sqlite3"
        ),
    )
    parser.add_argument(
        "--live-model",
        type=Path,
        default=Path(
            "/srv/trading-bot-three-site-staging-data/coin-intelligence/"
            "apps/coin-rate-estimator/runtime/model.json"
        ),
    )
    parser.add_argument(
        "--market-store",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "private-gold-live/market/market.sqlite3"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tmp" / "shadow-ml-evidence",
    )
    parser.add_argument(
        "--commodity-engine-rows",
        type=int,
        default=8,
        help="Max test rows for the slow Market Store structural engine slice.",
    )
    parser.add_argument(
        "--skip-commodity-engine",
        action="store_true",
        help="Skip the structural Market Store engine comparison.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("building samples...", flush=True)
    samples = build_samples(market_db=args.market_db, conversation_db=args.conversation_db)
    print(f"accepted_samples={len(samples)}", flush=True)
    fit, validation, test, split_meta = chronological_split(samples)
    print(f"split={split_meta}", flush=True)
    if len(fit) < 50 or len(test) < 20:
        raise SystemExit(f"split_too_small:{split_meta}")

    x_fit, y_fit, w_fit = _matrix(fit)
    x_val, y_val, _ = _matrix(validation)
    x_test, y_test, _ = _matrix(test)

    model = HistGradientBoostingRegressor(
        max_depth=6,
        learning_rate=0.05,
        max_iter=300,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=42,
    )
    print("fitting HistGradientBoosting...", flush=True)
    model.fit(x_fit, y_fit, sample_weight=w_fit)

    val_pred = model.predict(x_val) if len(validation) else np.asarray([])
    test_pred = model.predict(x_test)
    baseline_pred = baseline_median_bubbles(fit, test)
    live = load_model(args.live_model)
    live_pred = live_model_bubbles(live, test)
    if args.skip_commodity_engine:
        commodity_report: dict[str, Any] = {"skipped": True}
    else:
        print(
            f"evaluating commodity engine slice (max {args.commodity_engine_rows})...",
            flush=True,
        )
        commodity_report = commodity_engine_price_errors(
            market_store=args.market_store,
            rows=test,
            maximum_rows=max(0, int(args.commodity_engine_rows)),
        )

    report = {
        "shadow_version": SHADOW_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "library": "sklearn.ensemble.HistGradientBoostingRegressor",
        "feature_keys": list(FEATURE_KEYS_FULL),
        "split": split_meta,
        "total_accepted_samples": len(samples),
        "validation": evaluate_price_mae(validation, val_pred) if len(validation) else {},
        "test_shadow_ml": evaluate_price_mae(test, test_pred),
        "test_median_baseline": evaluate_price_mae(test, baseline_pred),
        "test_live_model_bubble_table": evaluate_price_mae(test, live_pred),
        "test_commodity_engine_market_store": commodity_report,
        "promotion": {
            "allowed": False,
            "reason": "shadow_only_requires_human_promotion",
        },
    }
    shadow_beats_live = (
        report["test_shadow_ml"]["mae_project"]
        < report["test_live_model_bubble_table"]["mae_project"]
    )
    shadow_beats_baseline = (
        report["test_shadow_ml"]["mae_project"]
        < report["test_median_baseline"]["mae_project"]
    )
    report["comparison"] = {
        "shadow_beats_live_bubble_table": shadow_beats_live,
        "shadow_beats_median_baseline": shadow_beats_baseline,
        "recommendation": (
            "KEEP_LIVE_MODEL_SHADOW_ONLY"
            if not shadow_beats_live
            else "SHADOW_BETTER_ON_TEST_BUT_DO_NOT_AUTO_PROMOTE"
        ),
    }

    artifact = {
        "shadow_version": SHADOW_VERSION,
        "feature_keys": list(FEATURE_KEYS_FULL),
        "split": split_meta,
        "sklearn_model": model,
    }
    model_path = args.output_dir / "shadow_hgb_model.joblib"
    report_path = args.output_dir / "shadow_ml_compare_report.json"
    joblib.dump(artifact, model_path)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"wrote {report_path}")
    print(f"wrote {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
