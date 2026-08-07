#!/usr/bin/env python3
"""Train a strengthened residual ML shadow (relative residual + recency weights).

Pipeline:
1) Trusted confirmed-trade labels with market context.
2) Chronological split (60/20/20 + purge).
3) Baseline = live model bubble table → project price.
4) Fit HistGradientBoosting on relative residual (obs-base)/base.
5) Select hyperparameters on validation MAE after clipped correction.
6) Write joblib artifact + comparison report. Never auto-promotes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "coin_rate_estimator"))

from coin_estimator import (  # noqa: E402
    COMMODITY_SPECS,
    PRICE_MULTIPLIER,
    connect_market_db,
    group_training_example,
    load_group_confirmed_trade_labels,
    load_model,
    parse_datetime,
    write_json_atomic,
)


SHADOW_VERSION = "residual-hgb-shadow-v2-relative-20260806"
PURGE = timedelta(minutes=15)
RECENCY_HALF_LIFE_DAYS = 10.0
MAX_ABS_RELATIVE_CORRECTION = 0.015
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
    "hour_sin",
    "hour_cos",
    "is_morning_open",
    "commodity_code",
    "settlement_code",
    "trade_form_code",
    "baseline_bubble",
    "baseline_project",
    "log_melted",
)

HYPERPARAMS = (
    {
        "max_depth": 3,
        "learning_rate": 0.05,
        "max_iter": 180,
        "min_samples_leaf": 25,
        "l2_regularization": 3.0,
        "early_stopping": False,
    },
    {
        "max_depth": 4,
        "learning_rate": 0.04,
        "max_iter": 220,
        "min_samples_leaf": 20,
        "l2_regularization": 2.5,
        "early_stopping": False,
    },
    {
        "max_depth": 3,
        "learning_rate": 0.08,
        "max_iter": 140,
        "min_samples_leaf": 35,
        "l2_regularization": 5.0,
        "early_stopping": False,
    },
)


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = parse_datetime(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _tehran_parts(moment: datetime) -> tuple[int, int]:
    local = moment.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Tehran"))
    return local.weekday(), local.hour


def _f(value: Any, default: float = math.nan) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def live_bubble(model: dict[str, Any], commodity: str, settlement: str, trade_form: str) -> float:
    by_name = {
        str(item.get("name")): item
        for item in model.get("commodities") or []
        if isinstance(item, dict)
    }
    item = by_name.get(commodity) or {}
    market_forms = ((item.get("market_forms") or {}).get(settlement) or {}).get(trade_form) or {}
    bubble = market_forms.get("bubble_ratio_median")
    if bubble is None:
        bubble = ((item.get("settlements") or {}).get(settlement) or {}).get("bubble_ratio_median")
    return float(bubble or 0.0)


def build_rows(market_db: Path, conversation_db: Path, live_model: dict[str, Any]) -> list[dict[str, Any]]:
    labels = load_group_confirmed_trade_labels(conversation_db)
    rows: list[dict[str, Any]] = []
    names = sorted(COMMODITY_SPECS)
    now = datetime.now(timezone.utc)
    with connect_market_db(market_db) as connection:
        for label in labels:
            example = group_training_example(connection, label)
            if example is None or not example.get("accepted"):
                continue
            moment = _utc(str(example["event_time_utc"]))
            weekday, hour = _tehran_parts(moment)
            commodity = str(example["commodity_name"])
            settlement = str(example["settlement_type"])
            trade_form = str(example.get("trade_form") or "PHYSICAL")
            baseline_b = live_bubble(live_model, commodity, settlement, trade_form)
            intrinsic = float(example["intrinsic_toman"])
            baseline_project = (intrinsic * (1.0 + baseline_b)) / PRICE_MULTIPLIER
            observed = float(example["project_price"])
            if baseline_project <= 0 or observed <= 0:
                continue
            relative_residual = (observed - baseline_project) / baseline_project
            # Drop pathological labels; keep training stable.
            if abs(relative_residual) > 0.08:
                continue
            age_days = max((now - moment).total_seconds() / 86400.0, 0.0)
            recency = math.exp(-math.log(2.0) * age_days / RECENCY_HALF_LIFE_DAYS)
            melted_avg = _f(example.get("melted_average_toman"))
            features = {
                "melted_average_toman": melted_avg,
                "melted_latest_toman": _f(example.get("melted_latest_toman")),
                "usd_average_toman": _f(example.get("usd_average_toman")),
                "usdt_average_toman": _f(example.get("usdt_average_toman")),
                "xauusd_average": _f(example.get("xauusd_average")),
                "melted_vs_global_ratio": _f(example.get("melted_vs_global_ratio")),
                "market_pressure_score": _f(example.get("market_pressure_score")),
                "market_regime_score": _f(example.get("market_regime_score")),
                "market_regime_confidence": _f(example.get("market_regime_confidence")),
                "source_confidence": _f(example.get("source_confidence"), 1.0),
                "quantity": _f(example.get("quantity"), 1.0),
                "tehran_weekday": float(weekday),
                "tehran_hour": float(hour),
                "hour_sin": math.sin(2.0 * math.pi * hour / 24.0),
                "hour_cos": math.cos(2.0 * math.pi * hour / 24.0),
                "is_morning_open": 1.0 if 8 <= hour < 11 else 0.0,
                "commodity_code": float(names.index(commodity) if commodity in names else -1),
                "settlement_code": 0.0 if settlement == "CASH" else 1.0,
                "trade_form_code": 0.0 if trade_form == "PHYSICAL" else 1.0,
                "baseline_bubble": baseline_b,
                "baseline_project": baseline_project,
                "log_melted": math.log(melted_avg) if math.isfinite(melted_avg) and melted_avg > 0 else math.nan,
            }
            rows.append(
                {
                    "available_at": moment,
                    "commodity": commodity,
                    "settlement": settlement,
                    "trade_form": trade_form,
                    "observed_project": observed,
                    "baseline_project": baseline_project,
                    "relative_residual": relative_residual,
                    "weight": float(example.get("source_weight") or 1.0) * recency,
                    "features": features,
                }
            )
    rows.sort(key=lambda item: item["available_at"])
    return rows


def split_rows(rows: list[dict[str, Any]]) -> tuple[list, list, list, dict[str, Any]]:
    if len(rows) < 40:
        raise SystemExit(f"not_enough_rows:{len(rows)}")
    validation_start = rows[int(len(rows) * 0.60)]["available_at"]
    test_start = rows[int(len(rows) * 0.80)]["available_at"]
    fit, validation, test = [], [], []
    purged = 0
    for row in rows:
        if abs(row["available_at"] - validation_start) <= PURGE or abs(row["available_at"] - test_start) <= PURGE:
            purged += 1
            continue
        if row["available_at"] < validation_start:
            fit.append(row)
        elif row["available_at"] < test_start:
            validation.append(row)
        else:
            test.append(row)
    meta = {
        "fit_rows": len(fit),
        "validation_rows": len(validation),
        "test_rows": len(test),
        "purged_rows": purged,
        "validation_start_utc": validation_start.isoformat().replace("+00:00", "Z"),
        "test_start_utc": test_start.isoformat().replace("+00:00", "Z"),
    }
    return fit, validation, test, meta


def matrix(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray([[row["features"][key] for key in FEATURE_KEYS] for row in rows], dtype=float)
    y = np.asarray([row["relative_residual"] for row in rows], dtype=float)
    w = np.asarray([row["weight"] for row in rows], dtype=float)
    return x, y, w


def metrics(rows: list[dict[str, Any]], relative_pred: np.ndarray) -> dict[str, float]:
    baseline = np.asarray([row["baseline_project"] for row in rows], dtype=float)
    observed = np.asarray([row["observed_project"] for row in rows], dtype=float)
    clipped = np.clip(relative_pred, -MAX_ABS_RELATIVE_CORRECTION, MAX_ABS_RELATIVE_CORRECTION)
    corrected = baseline * (1.0 + clipped)
    abs_base = np.abs(baseline - observed)
    abs_corr = np.abs(corrected - observed)
    return {
        "sample_count": int(len(rows)),
        "mae_baseline": float(np.mean(abs_base)),
        "mae_shadow": float(np.mean(abs_corr)),
        "medae_baseline": float(np.median(abs_base)),
        "medae_shadow": float(np.median(abs_corr)),
        "mape_baseline": float(np.mean(abs_base / np.maximum(observed, 1.0))),
        "mape_shadow": float(np.mean(abs_corr / np.maximum(observed, 1.0))),
        "improvement_mae_ratio": float(
            1.0 - (np.mean(abs_corr) / max(np.mean(abs_base), 1e-9))
        ),
    }


def feature_importance(model: HistGradientBoostingRegressor, x: np.ndarray, y: np.ndarray) -> list[dict[str, Any]]:
    if len(x) < 30:
        return []
    result = permutation_importance(
        model, x, y, n_repeats=5, random_state=42, scoring="neg_mean_absolute_error"
    )
    order = np.argsort(result.importances_mean)[::-1]
    return [
        {
            "feature": FEATURE_KEYS[int(index)],
            "importance_mean": float(result.importances_mean[int(index)]),
            "importance_std": float(result.importances_std[int(index)]),
        }
        for index in order[:12]
    ]


def calibrate_shrink(
    rows: list[dict[str, Any]], relative_pred: np.ndarray
) -> tuple[float, dict[str, float]]:
    """Pick shrink α∈[0,1] so clipped α·pred does not worsen validation MAE."""

    if not rows:
        return 0.0, {}
    best_alpha = 0.0
    best = metrics(rows, relative_pred * 0.0)
    for alpha in (0.0, 0.15, 0.25, 0.4, 0.55, 0.7, 0.85, 1.0):
        current = metrics(rows, relative_pred * alpha)
        if current["mae_shadow"] < best["mae_shadow"] - 1e-9:
            best = current
            best_alpha = alpha
    return best_alpha, best


def select_model(
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    w_fit: np.ndarray,
    validation: list[dict[str, Any]],
) -> tuple[HistGradientBoostingRegressor, dict[str, Any], dict[str, float], float]:
    x_val, _, _ = matrix(validation) if validation else (np.zeros((0, len(FEATURE_KEYS))), None, None)
    best_model = None
    best_params: dict[str, Any] = {}
    best_val: dict[str, float] = {"mae_shadow": float("inf")}
    best_alpha = 0.0
    # Winsorize relative residuals for fit stability.
    low, high = np.quantile(y_fit, [0.02, 0.98])
    y_fit_clipped = np.clip(y_fit, low, high)
    for index, params in enumerate(HYPERPARAMS, start=1):
        print(f"  fit candidate {index}/{len(HYPERPARAMS)}: {params}", flush=True)
        candidate = HistGradientBoostingRegressor(random_state=42, **params)
        candidate.fit(x_fit, y_fit_clipped, sample_weight=w_fit)
        if len(validation):
            raw_pred = candidate.predict(x_val)
            alpha, val_metrics = calibrate_shrink(validation, raw_pred)
        else:
            alpha, val_metrics = 0.0, {"mae_shadow": float("inf")}
        print(
            f"  val mae_shadow={val_metrics.get('mae_shadow')} shrink={alpha}",
            flush=True,
        )
        if val_metrics["mae_shadow"] < best_val["mae_shadow"]:
            best_model = candidate
            best_params = dict(params)
            best_val = val_metrics
            best_alpha = alpha
    assert best_model is not None
    return best_model, best_params, best_val, best_alpha


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
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tmp" / "shadow-ml-evidence",
    )
    parser.add_argument(
        "--skip-importance",
        action="store_true",
        help="Skip slow permutation importance (recommended for fast retrain).",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    live = load_model(args.live_model)
    print("building residual rows on live model baseline...", flush=True)
    rows = build_rows(args.market_db, args.conversation_db, live)
    fit, validation, test, split_meta = split_rows(rows)
    print(f"split={split_meta}", flush=True)
    x_fit, y_fit, w_fit = matrix(fit)
    x_test, _, _ = matrix(test)

    print("selecting hyperparameters on validation...", flush=True)
    model, best_params, val_metrics, shrink = select_model(
        x_fit, y_fit, w_fit, validation
    )
    test_pred = model.predict(x_test) * shrink
    test_metrics = metrics(test, test_pred)

    report = {
        "shadow_version": SHADOW_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "mode": "relative_residual_on_live_bubble_baseline",
        "target_mode": "relative",
        "max_abs_relative_correction": MAX_ABS_RELATIVE_CORRECTION,
        "prediction_shrink": shrink,
        "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        "selected_hyperparameters": best_params,
        "split": split_meta,
        "validation": val_metrics if validation else {},
        "test": test_metrics,
        "feature_importance": (
            []
            if args.skip_importance
            else feature_importance(model, x_test, model.predict(x_test))
        ),
        "live_model": {
            "path": str(args.live_model),
            "kind": live.get("model_kind"),
        },
        "promotion": {
            "allowed": False,
            "recommend_enable_residual_shadow": True,
        },
    }
    report["comparison"] = {
        "shadow_beats_baseline_on_test": report["test"]["mae_shadow"]
        < report["test"]["mae_baseline"],
        "recommendation": (
            "ENABLE_RESIDUAL_SHADOW_PARALLEL"
            if report["test"]["mae_shadow"] <= report["test"]["mae_baseline"] * 1.02
            else "KEEP_SHADOW_RESEARCH_ONLY"
        ),
    }

    artifact = {
        "shadow_version": SHADOW_VERSION,
        "feature_keys": list(FEATURE_KEYS),
        "target_mode": "relative",
        "max_abs_relative_correction": MAX_ABS_RELATIVE_CORRECTION,
        "max_abs_absolute_correction": 2500.0,
        "prediction_shrink": shrink,
        "split": split_meta,
        "selected_hyperparameters": best_params,
        "sklearn_model": model,
        "applies_to_model": "live-estimate-centers",
        "training_baseline": "live_model_bubble_table",
    }
    model_path = args.output_dir / "residual_shadow_hgb.joblib"
    runtime_shadow = args.live_model.parent / "residual_shadow_hgb.joblib"
    joblib.dump(artifact, model_path)
    joblib.dump(artifact, runtime_shadow)
    report_path = args.output_dir / "residual_shadow_report.json"
    write_json_atomic(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"wrote {report_path}")
    print(f"wrote {model_path}")
    print(f"wrote {runtime_shadow}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
