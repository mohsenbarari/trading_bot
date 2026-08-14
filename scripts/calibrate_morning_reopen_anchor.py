#!/usr/bin/env python3
"""Calibrate morning-reopen blend weights against 10:00–10:30 Tehran truth.

Read-only on market/conversation DBs. Writes a candidate model + JSON report.
Never auto-promotes the live model.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "apps" / "coin_rate_estimator"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(APP_ROOT))

from coin_estimator import estimate_rates, load_model  # noqa: E402
from morning_reopen import (  # noqa: E402
    DEFAULT_BLEND_FRESH,
    DEFAULT_BLEND_STALE,
    METHOD_NAME,
    list_tehran_days_with_activity,
    morning_open_truth_label,
    tehran_clock_utc,
)

FOCUS_COMMODITIES = ("امام", "بهار", "نیم بهار", "ربع بهار")
FOCUS_SETTLEMENTS = ("CASH", "TOMORROW")
# Tehran ladder: Herat ~08:00, melted ~09:00, coin ~10:00.
INPUT_LADDER = (
    (8, 5, "herat_open"),
    (9, 5, "melted_open"),
    (9, 55, "pre_coin_open"),
    (10, 0, "coin_open"),
)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _abs_pct(estimate: float, truth: float) -> float:
    return abs(float(estimate) - float(truth)) / max(abs(float(truth)), 1.0)


def _write_candidate_artifacts(
    *,
    candidate_path: Path,
    runtime_candidate_path: Path,
    text: str,
    stage_runtime_artifacts: bool,
) -> bool:
    """Write research output and stage runtime only after explicit opt-in."""

    candidate_path.write_text(text, encoding="utf-8")
    if not stage_runtime_artifacts:
        return False
    if runtime_candidate_path.resolve() != candidate_path.resolve():
        runtime_candidate_path.write_text(text, encoding="utf-8")
    return True


def evaluate_model_on_days(
    *,
    model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    days: list[str],
    as_of_hour: int = 10,
    as_of_minute: int = 0,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for day in days:
        as_of = tehran_clock_utc(day, as_of_hour, as_of_minute)
        try:
            payload = estimate_rates(
                model=model,
                market_db=market_db,
                end=as_of,
                conversation_db=conversation_db,
                live_group_events_enabled=False,
                group_live_events_before=as_of,
            )
        except Exception as exc:  # keep bakeoff running across bad days
            rows.append({"day": day, "error": type(exc).__name__, "status": "FAILED"})
            continue
        estimates: dict[str, dict[str, Any]] = {}
        for settlement, body in (payload.get("settlements") or {}).items():
            for item in body.get("rates") or []:
                if not isinstance(item, dict):
                    continue
                key = f"{item.get('commodity_name')}:{settlement}"
                estimates[key] = item
        for commodity in FOCUS_COMMODITIES:
            for settlement in FOCUS_SETTLEMENTS:
                truth = morning_open_truth_label(
                    conversation_db,
                    day=day,
                    commodity=commodity,
                    settlement=settlement,
                )
                if truth.get("status") != "OBSERVED":
                    continue
                key = f"{commodity}:{settlement}"
                est = estimates.get(key) or {}
                status = str(est.get("status") or "MISSING")
                row: dict[str, Any] = {
                    "day": day,
                    "key": key,
                    "truth_source": truth.get("source"),
                    "truth_project_price": truth.get("truth_project_price"),
                    "truth_trade_count": truth.get("trade_count"),
                    "truth_offer_count": truth.get("offer_count"),
                    "estimate_status": status,
                    "method": est.get("method"),
                    "estimated_project_price": est.get("estimated_project_price"),
                }
                if status == "ESTIMATED" and est.get("estimated_price_toman") is not None:
                    err = _abs_pct(
                        float(est["estimated_price_toman"]),
                        float(truth["truth_price_toman"]),
                    )
                    row["abs_pct"] = err
                    tol = est.get("tolerance") or {}
                    lower = tol.get("lower_price_toman")
                    upper = tol.get("upper_price_toman")
                    if lower is not None and upper is not None:
                        truth_toman = float(truth["truth_price_toman"])
                        row["interval_hit"] = float(lower) <= truth_toman <= float(upper)
                        row["band_width_pct"] = (
                            (float(upper) - float(lower)) / max(truth_toman, 1.0)
                        )
                rows.append(row)
    paired = [row for row in rows if row.get("abs_pct") is not None]
    hits = [row for row in paired if row.get("interval_hit") is True]
    reopen_methods = [
        row for row in paired if METHOD_NAME in str(row.get("method") or "")
    ]
    return {
        "day_count": len(days),
        "label_rows": len(rows),
        "paired_estimated": len(paired),
        "median_abs_pct": _median([float(row["abs_pct"]) for row in paired]),
        "mean_abs_pct": _mean([float(row["abs_pct"]) for row in paired]),
        "interval_hit_rate": (len(hits) / len(paired)) if paired else None,
        "median_band_width_pct": _median(
            [float(row["band_width_pct"]) for row in paired if row.get("band_width_pct") is not None]
        ),
        "morning_reopen_paired": len(reopen_methods),
        "rows": rows,
    }


def evaluate_input_ladder(
    *,
    model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    days: list[str],
) -> dict[str, Any]:
    """Summarize melted/usd readiness and Imam coverage across morning cutoffs."""

    by_cut: dict[str, dict[str, Any]] = {}
    for hour, minute, label in INPUT_LADDER:
        melted_ok = 0
        usd_ok = 0
        imam_estimated = 0
        days_seen = 0
        for day in days:
            days_seen += 1
            as_of = tehran_clock_utc(day, hour, minute)
            try:
                payload = estimate_rates(
                    model=model,
                    market_db=market_db,
                    end=as_of,
                    conversation_db=conversation_db,
                    live_group_events_enabled=False,
                    group_live_events_before=as_of,
                )
            except Exception:
                continue
            cash = (payload.get("settlements") or {}).get("CASH") or {}
            inputs = cash.get("inputs") or {}
            melted = inputs.get("melted_gold") or {}
            usd = inputs.get("usd") or {}
            if melted.get("status") in {"OBSERVED", "ESTIMATED"} or melted.get(
                "average_price"
            ) is not None:
                melted_ok += 1
            if usd.get("status") in {"OBSERVED", "ESTIMATED"} or usd.get(
                "average_price"
            ) is not None:
                usd_ok += 1
            for item in cash.get("rates") or []:
                if (
                    item.get("commodity_name") == "امام"
                    and item.get("status") == "ESTIMATED"
                ):
                    imam_estimated += 1
                    break
        by_cut[label] = {
            "tehran_clock": f"{hour:02d}:{minute:02d}",
            "days": days_seen,
            "melted_ready_days": melted_ok,
            "usd_ready_days": usd_ok,
            "imam_cash_estimated_days": imam_estimated,
        }
    # Truth comparison at 09:55 as secondary early check.
    early = evaluate_model_on_days(
        model=model,
        market_db=market_db,
        conversation_db=conversation_db,
        days=days,
        as_of_hour=9,
        as_of_minute=55,
    )
    return {
        "ladder": by_cut,
        "pre_open_0955_vs_truth": {
            k: early.get(k)
            for k in (
                "paired_estimated",
                "median_abs_pct",
                "mean_abs_pct",
                "interval_hit_rate",
                "morning_reopen_paired",
            )
        },
    }


def grid_search_weights(
    *,
    base_model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    train_days: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Small discrete grid over reopen blend weights; minimize train median error."""

    candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    fresh_grid = [
        {"transferred": 0.72, "structural": 0.23, "basis": 0.05},
        {"transferred": 0.80, "structural": 0.15, "basis": 0.05},
        {"transferred": 0.65, "structural": 0.30, "basis": 0.05},
        {"transferred": 0.55, "structural": 0.35, "basis": 0.10},
    ]
    stale_grid = [
        {"transferred": 0.35, "structural": 0.55, "basis": 0.10},
        {"transferred": 0.45, "structural": 0.45, "basis": 0.10},
        {"transferred": 0.25, "structural": 0.65, "basis": 0.10},
        {"transferred": 0.30, "structural": 0.60, "basis": 0.10},
    ]
    for fresh in fresh_grid:
        for stale in stale_grid:
            trial = deepcopy(base_model)
            trial["morning_reopen"] = {
                "enabled": True,
                "blend_fresh": fresh,
                "blend_stale": stale,
                "fresh_max_age_seconds": 36 * 3600,
                "band_multiplier_fresh": 1.6,
                "band_multiplier_stale": 2.2,
            }
            metrics = evaluate_model_on_days(
                model=trial,
                market_db=market_db,
                conversation_db=conversation_db,
                days=train_days,
            )
            score = metrics.get("median_abs_pct")
            if score is None:
                continue
            candidates.append((float(score), trial["morning_reopen"], metrics))
    if not candidates:
        fallback_policy = {
            "enabled": True,
            "blend_fresh": dict(DEFAULT_BLEND_FRESH),
            "blend_stale": dict(DEFAULT_BLEND_STALE),
            "fresh_max_age_seconds": 36 * 3600,
            "band_multiplier_fresh": 1.6,
            "band_multiplier_stale": 2.2,
        }
        trial = deepcopy(base_model)
        trial["morning_reopen"] = fallback_policy
        metrics = evaluate_model_on_days(
            model=trial,
            market_db=market_db,
            conversation_db=conversation_db,
            days=train_days,
        )
        return fallback_policy, metrics
    candidates.sort(key=lambda item: (item[0], -item[2].get("paired_estimated", 0)))
    best_score, best_policy, best_metrics = candidates[0]
    best_metrics = dict(best_metrics)
    best_metrics["selected_train_median_abs_pct"] = best_score
    return best_policy, best_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-db",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "estimator-live/public-market/market_prices.sqlite3"
        ),
    )
    parser.add_argument(
        "--conversation-db",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "estimator-live/conversation/conversation_events.sqlite3"
        ),
    )
    parser.add_argument(
        "--live-model",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "estimator-live/runtime/model.json"
        ),
    )
    parser.add_argument(
        "--shadow-model",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "estimator-live/runtime/model.shadow-previous-live.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tmp" / "morning-reopen-calibration",
    )
    parser.add_argument("--start-day", default="2026-07-01")
    parser.add_argument("--end-day", default="2026-08-05")
    parser.add_argument("--holdout-days", type=int, default=5)
    parser.add_argument(
        "--stage-runtime-artifacts",
        action="store_true",
        help=(
            "Also write the candidate model and research state beside --live-model. "
            "Disabled by default so calibration is read-only with respect to runtime."
        ),
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    live_model = load_model(args.live_model)
    shadow_model = load_model(args.shadow_model) if args.shadow_model.is_file() else None

    days = list_tehran_days_with_activity(
        args.conversation_db, start_day=args.start_day, end_day=args.end_day
    )
    # Keep weekdays that can produce at least one Imam truth label.
    usable_days: list[str] = []
    for day in days:
        for settlement in FOCUS_SETTLEMENTS:
            label = morning_open_truth_label(
                args.conversation_db,
                day=day,
                commodity="امام",
                settlement=settlement,
            )
            if label.get("status") == "OBSERVED":
                usable_days.append(day)
                break
    if len(usable_days) < 4:
        report = {
            "status": "INSUFFICIENT_DAYS",
            "usable_days": usable_days,
            "all_activity_days": days,
        }
        out = args.output_dir / "gate_report.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    holdout_n = max(1, min(args.holdout_days, max(1, len(usable_days) // 3)))
    train_days = usable_days[:-holdout_n]
    holdout_days = usable_days[-holdout_n:]

    # Disable reopen to measure the pre-change freshness/structural blend.
    legacy = deepcopy(live_model)
    legacy["morning_reopen"] = {"enabled": False}

    best_policy, train_metrics = grid_search_weights(
        base_model=live_model,
        market_db=args.market_db,
        conversation_db=args.conversation_db,
        train_days=train_days,
    )
    candidate = deepcopy(live_model)
    candidate["morning_reopen"] = best_policy
    candidate["model_kind"] = (
        str(live_model.get("model_kind") or "ROBUST_HYBRID")
        + "_WITH_MORNING_REOPEN"
    )
    candidate["morning_reopen_calibrated_at_utc"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    candidate_holdout = evaluate_model_on_days(
        model=candidate,
        market_db=args.market_db,
        conversation_db=args.conversation_db,
        days=holdout_days,
    )
    legacy_holdout = evaluate_model_on_days(
        model=legacy,
        market_db=args.market_db,
        conversation_db=args.conversation_db,
        days=holdout_days,
    )
    shadow_holdout = None
    if shadow_model is not None:
        shadow_holdout = evaluate_model_on_days(
            model=shadow_model,
            market_db=args.market_db,
            conversation_db=args.conversation_db,
            days=holdout_days,
        )
    ladder = evaluate_input_ladder(
        model=candidate,
        market_db=args.market_db,
        conversation_db=args.conversation_db,
        days=holdout_days,
    )

    def _score(block: dict[str, Any] | None) -> float:
        if not block or block.get("median_abs_pct") is None:
            return math.inf
        return float(block["median_abs_pct"])

    improved = _score(candidate_holdout) < _score(legacy_holdout) - 1e-9
    promote_ready = improved and (candidate_holdout.get("paired_estimated") or 0) >= max(
        3, int(0.5 * (legacy_holdout.get("paired_estimated") or 0))
    )

    candidate_path = args.output_dir / "model.morning-reopen.candidate.json"
    runtime_candidate = args.live_model.parent / "model.morning-reopen.candidate.json"
    text = json.dumps(candidate, ensure_ascii=False, indent=2)
    runtime_staged = _write_candidate_artifacts(
        candidate_path=candidate_path,
        runtime_candidate_path=runtime_candidate,
        text=text,
        stage_runtime_artifacts=bool(args.stage_runtime_artifacts),
    )

    report = {
        "status": "OK",
        "promote_ready": promote_ready,
        "auto_promote": False,
        "usable_days": usable_days,
        "train_days": train_days,
        "holdout_days": holdout_days,
        "best_policy": best_policy,
        "train_metrics_compact": {
            k: train_metrics.get(k)
            for k in (
                "paired_estimated",
                "median_abs_pct",
                "mean_abs_pct",
                "interval_hit_rate",
                "median_band_width_pct",
                "morning_reopen_paired",
                "selected_train_median_abs_pct",
            )
        },
        "holdout": {
            "legacy_live": {
                k: legacy_holdout.get(k)
                for k in (
                    "paired_estimated",
                    "median_abs_pct",
                    "mean_abs_pct",
                    "interval_hit_rate",
                    "median_band_width_pct",
                    "morning_reopen_paired",
                )
            },
            "candidate": {
                k: candidate_holdout.get(k)
                for k in (
                    "paired_estimated",
                    "median_abs_pct",
                    "mean_abs_pct",
                    "interval_hit_rate",
                    "median_band_width_pct",
                    "morning_reopen_paired",
                )
            },
            "shadow_previous_live": (
                {
                    k: shadow_holdout.get(k)
                    for k in (
                        "paired_estimated",
                        "median_abs_pct",
                        "mean_abs_pct",
                        "interval_hit_rate",
                        "median_band_width_pct",
                        "morning_reopen_paired",
                    )
                }
                if shadow_holdout
                else None
            ),
            "baseline_note": (
                "legacy_live sets morning_reopen.enabled=false (old blend); "
                "candidate uses grid-calibrated morning_reopen policy."
            ),
        },
        "candidate_model_path": str(candidate_path),
        "runtime_staging": {
            "requested": bool(args.stage_runtime_artifacts),
            "staged": runtime_staged,
            "candidate_path": str(runtime_candidate) if runtime_staged else None,
        },
        "as_of": "Tehran 10:00 primary; ladder 08:05/09:05/09:55/10:00",
        "truth": "trades if any in [10:00,10:30) else recency-weighted offers",
        "morning_ladder_holdout": ladder,
        "staging_decision": {
            "auto_promote": False,
            "promote_ready": promote_ready,
            "live_model_unchanged": True,
            "reason": (
                "Holdout point error improved; candidate eligible for manual promote."
                if promote_ready
                else (
                    "Holdout median abs pct of morning-reopen candidate is not better "
                    "than legacy blend; keep candidate for research shadow only."
                )
            ),
        },
    }
    # Keep detailed rows separately to avoid huge primary report.
    detail = {
        "legacy_rows": legacy_holdout.get("rows"),
        "candidate_rows": candidate_holdout.get("rows"),
        "shadow_rows": shadow_holdout.get("rows") if shadow_holdout else None,
    }
    # Research shadow artifact: isolated state, never overrides live.
    research_state = (
        args.live_model.parent / "state.morning-reopen.shadow.json"
        if args.stage_runtime_artifacts
        else args.output_dir / "state.morning-reopen.shadow.json"
    )
    try:
        from shadow_parallel import run_shadow_parallel

        live_now = estimate_rates(
            model=legacy,
            market_db=args.market_db,
            end=datetime.now(timezone.utc),
            conversation_db=args.conversation_db,
            live_group_events_enabled=False,
            group_live_events_before=datetime.now(timezone.utc),
        )
        research_model_path = runtime_candidate if runtime_staged else candidate_path
        run_shadow_parallel(
            live_estimate=live_now,
            market_db=args.market_db,
            conversation_db=args.conversation_db,
            end=datetime.now(timezone.utc),
            shadow_model_path=research_model_path,
            shadow_state_path=research_state,
            live_group_events_enabled=False,
            group_live_events_before=datetime.now(timezone.utc),
        )
        report["research_shadow_state"] = str(research_state)
    except Exception as exc:
        report["research_shadow_state_error"] = type(exc).__name__

    report_path = args.output_dir / "calibration_report.json"
    detail_path = args.output_dir / "calibration_rows.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if promote_ready or report["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
