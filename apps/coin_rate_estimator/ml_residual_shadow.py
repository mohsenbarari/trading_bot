"""Apply a sklearn residual ML shadow on top of the live estimate.

Uses the same live estimate settlements (same market/conversation window).
Never overrides the authoritative live book; writes an isolated state file.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import os

from coin_estimator import COMMODITY_SPECS, iso_utc, write_json_atomic
from shadow_parallel import compare_centers

TEHRAN = ZoneInfo("Asia/Tehran")
_RUNTIME = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_RUNTIME_DIR",
        str(Path(__file__).resolve().parent / "runtime"),
    )
)
DEFAULT_ML_ARTIFACT = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_ML_SHADOW_MODEL",
        str(_RUNTIME / "residual_shadow_hgb.joblib"),
    )
)
DEFAULT_ML_SHADOW_STATE = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_ML_SHADOW_STATE",
        str(_RUNTIME / "state.ml-residual.shadow.json"),
    )
)

_ARTIFACT_CACHE: dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "artifact": None,
}


def _load_artifact(path: Path) -> dict[str, Any]:
    """Cache the joblib artifact across refresh cycles."""

    stat = path.stat()
    cached_path = _ARTIFACT_CACHE.get("path")
    cached_mtime = _ARTIFACT_CACHE.get("mtime_ns")
    if (
        cached_path == str(path)
        and cached_mtime == stat.st_mtime_ns
        and isinstance(_ARTIFACT_CACHE.get("artifact"), dict)
    ):
        return _ARTIFACT_CACHE["artifact"]
    import joblib

    artifact = joblib.load(path)
    if not isinstance(artifact, dict) or "sklearn_model" not in artifact:
        raise ValueError("invalid_ml_artifact")
    _ARTIFACT_CACHE["path"] = str(path)
    _ARTIFACT_CACHE["mtime_ns"] = stat.st_mtime_ns
    _ARTIFACT_CACHE["artifact"] = artifact
    return artifact


def _f(value: Any, default: float = math.nan) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _point(block: dict[str, Any] | None) -> float:
    if not isinstance(block, dict):
        return math.nan
    for key in ("point_price", "latest_price", "average_price", "llm_value"):
        value = _f(block.get(key))
        if math.isfinite(value):
            return value
    return math.nan


def feature_vector(
    *,
    feature_keys: list[str],
    commodity_name: str,
    settlement: str,
    trade_form: str,
    rate: dict[str, Any],
    inputs: dict[str, Any],
    end: datetime,
) -> list[float]:
    """Build the same feature layout used at training time."""

    names = sorted(COMMODITY_SPECS)
    local = end.astimezone(TEHRAN)
    melted = inputs.get("melted_gold") if isinstance(inputs.get("melted_gold"), dict) else {}
    usd = inputs.get("usd") if isinstance(inputs.get("usd"), dict) else {}
    usdt = inputs.get("usdt") if isinstance(inputs.get("usdt"), dict) else {}
    xau = inputs.get("xauusd") if isinstance(inputs.get("xauusd"), dict) else {}
    flow = inputs.get("order_flow") if isinstance(inputs.get("order_flow"), dict) else {}
    regime = inputs.get("market_regime") if isinstance(inputs.get("market_regime"), dict) else {}
    latest_by_type = (
        inputs.get("melted_latest_by_type")
        if isinstance(inputs.get("melted_latest_by_type"), dict)
        else {}
    )
    by_type = latest_by_type.get("by_type") if isinstance(latest_by_type.get("by_type"), dict) else {}
    melted_latest = _point(by_type.get("PHYSICAL") if isinstance(by_type.get("PHYSICAL"), dict) else None)
    if not math.isfinite(melted_latest):
        melted_latest = _point(melted)

    melted_avg = _f(melted.get("average_price"), _point(melted))
    usd_avg = _f(usd.get("average_price"), _point(usd))
    usdt_avg = _f(usdt.get("average_price"), _point(usdt))
    xau_avg = _f(xau.get("average_price"), _point(xau))
    theoretical = (
        usd_avg * xau_avg / 9.572737
        if math.isfinite(usd_avg) and math.isfinite(xau_avg)
        else math.nan
    )
    melted_vs_global = (
        melted_avg / theoretical - 1.0
        if math.isfinite(melted_avg) and math.isfinite(theoretical) and theoretical > 0
        else math.nan
    )
    baseline_project = _f(rate.get("estimated_project_price"))
    baseline_bubble = _f(rate.get("bubble_ratio"), 0.0)
    values = {
        "melted_average_toman": melted_avg,
        "melted_latest_toman": melted_latest,
        "usd_average_toman": usd_avg,
        "usdt_average_toman": usdt_avg,
        "xauusd_average": xau_avg,
        "melted_vs_global_ratio": melted_vs_global,
        "market_pressure_score": _f(
            rate.get("market_pressure_score"), _f(flow.get("estimator_score"))
        ),
        "market_regime_score": _f(
            rate.get("market_regime_score"), _f(regime.get("direction_score"))
        ),
        "market_regime_confidence": _f(
            rate.get("market_regime_confidence"), _f(regime.get("confidence"))
        ),
        "source_confidence": _f(rate.get("confidence"), 1.0),
        "quantity": 1.0,
        "tehran_weekday": float(local.weekday()),
        "tehran_hour": float(local.hour),
        "hour_sin": math.sin(2.0 * math.pi * local.hour / 24.0),
        "hour_cos": math.cos(2.0 * math.pi * local.hour / 24.0),
        "is_morning_open": 1.0 if 8 <= local.hour < 11 else 0.0,
        "commodity_code": float(
            names.index(commodity_name) if commodity_name in names else -1
        ),
        "settlement_code": 0.0 if settlement == "CASH" else 1.0,
        "trade_form_code": 0.0 if trade_form == "PHYSICAL" else 1.0,
        "baseline_bubble": baseline_bubble,
        "baseline_project": baseline_project,
        "log_melted": math.log(melted_avg) if math.isfinite(melted_avg) and melted_avg > 0 else math.nan,
    }
    return [float(values.get(key, math.nan)) for key in feature_keys]


def apply_ml_residual_to_estimate(
    live_estimate: dict[str, Any],
    artifact: dict[str, Any],
    *,
    end: datetime,
) -> dict[str, Any]:
    """Return a deep-copied estimate with ML residual applied to centers/bands."""

    model = artifact["sklearn_model"]
    feature_keys = list(artifact.get("feature_keys") or [])
    mode = str(artifact.get("target_mode") or "relative")
    max_abs_rel = float(artifact.get("max_abs_relative_correction") or 0.015)
    max_abs_abs = float(artifact.get("max_abs_absolute_correction") or 2500.0)
    shrink = float(artifact.get("prediction_shrink") or 1.0)
    shrink = max(0.0, min(1.0, shrink))
    shadow = deepcopy(live_estimate)
    shadow["model_kind"] = (
        f"{live_estimate.get('model_kind') or 'LIVE'}+ML_RESIDUAL_SHADOW"
    )
    shadow["ml_residual_shadow"] = {
        "enabled": True,
        "shadow_version": artifact.get("shadow_version"),
        "target_mode": mode,
        "max_abs_relative_correction": max_abs_rel,
        "prediction_shrink": shrink,
    }
    # HGB prediction has a sizable fixed OpenMP cost.  Calling it once per
    # rate made the ML shadow much more expensive than the estimator itself.
    # Build the exact same vectors first, then make one batch prediction; the
    # result for each row is identical and the shadow remains isolated.
    candidates: list[tuple[dict[str, Any], list[float]]] = []
    for settlement, body in (shadow.get("settlements") or {}).items():
        if not isinstance(body, dict):
            continue
        inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
        rates = body.get("rates") if isinstance(body.get("rates"), list) else []
        for rate in rates:
            if not isinstance(rate, dict) or rate.get("status") != "ESTIMATED":
                continue
            price = rate.get("estimated_project_price")
            if price is None:
                continue
            trade_form = str(rate.get("trade_form") or "PHYSICAL")
            vector = feature_vector(
                feature_keys=feature_keys,
                commodity_name=str(rate.get("commodity_name") or ""),
                settlement=str(settlement),
                trade_form=trade_form,
                rate=rate,
                inputs=inputs,
                end=end,
            )
            candidates.append((rate, vector))

    raw_predictions = (
        model.predict([vector for _, vector in candidates]) if candidates else []
    )
    for (rate, _vector), prediction in zip(candidates, raw_predictions):
        raw = float(prediction) * shrink
        base = float(rate["estimated_project_price"])
        if mode == "relative":
            correction_rel = max(-max_abs_rel, min(max_abs_rel, raw))
            corrected = base * (1.0 + correction_rel)
            correction_abs = corrected - base
        else:
            correction_abs = max(-max_abs_abs, min(max_abs_abs, raw))
            corrected = base + correction_abs
            correction_rel = correction_abs / max(abs(base), 1.0)
        corrected_int = int(round(corrected))
        rate["estimated_project_price"] = corrected_int
        if rate.get("estimated_price_toman") is not None:
            # Keep toman display aligned with project units used by live book.
            multiplier = float(
                live_estimate.get("project_price_multiplier_to_toman") or 1000.0
            )
            rate["estimated_price_toman"] = int(round(corrected_int * multiplier))
        for band_key in ("lower_project_price", "upper_project_price"):
            band = rate.get(band_key)
            if band is None:
                continue
            if mode == "relative":
                rate[band_key] = int(round(float(band) * (1.0 + correction_rel)))
            else:
                rate[band_key] = int(round(float(band) + correction_abs))
        rate["method"] = f"{rate.get('method') or 'LIVE'}+ML_RESIDUAL"
        rate["ml_residual_correction"] = {
            "raw_prediction": raw,
            "applied_relative": correction_rel,
            "applied_absolute_project": correction_abs,
            "base_project_price": base,
        }
    return shadow


def run_ml_residual_shadow(
    *,
    live_estimate: dict[str, Any],
    end: datetime,
    artifact_path: Path | None = None,
    shadow_state_path: Path | None = None,
) -> dict[str, Any]:
    """Fail-closed ML residual shadow on the identical live estimate payload."""

    path = artifact_path or DEFAULT_ML_ARTIFACT
    state_path = shadow_state_path or DEFAULT_ML_SHADOW_STATE
    meta: dict[str, Any] = {
        "enabled": False,
        "status": "DISABLED",
        "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
        "shadow_model_path": str(path),
        "shadow_model_kind": "ML_RESIDUAL_HGB",
        "authoritative_override": False,
        "label": "سایه ۳ — مدل یادگیری ماشین",
    }
    if path is None or not path.is_file():
        write_json_atomic(state_path, meta, mode=0o644)
        return meta
    try:
        artifact = _load_artifact(path)
        shadow_estimate = apply_ml_residual_to_estimate(
            live_estimate, artifact, end=end
        )
        comparison = compare_centers(live_estimate, shadow_estimate)
        payload = {
            "enabled": True,
            "status": "OK",
            "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
            "shadow_model_path": str(path),
            "shadow_model_kind": "ML_RESIDUAL_HGB",
            "shadow_version": artifact.get("shadow_version"),
            "authoritative_override": False,
            "comparison_vs_live": {
                "paired_estimated_count": comparison["paired_estimated_count"],
                "mean_abs_pct": comparison["mean_abs_pct"],
                "median_abs_pct": comparison["median_abs_pct"],
            },
            "estimate": shadow_estimate,
            "pair_details": comparison["pairs"],
            "label": "سایه ۳ — مدل یادگیری ماشین",
        }
        write_json_atomic(state_path, payload, mode=0o644)
        meta.update(
            {
                "enabled": True,
                "status": "OK",
                "comparison_vs_live": payload["comparison_vs_live"],
                "shadow_version": artifact.get("shadow_version"),
            }
        )
        return meta
    except Exception as exc:  # fail-closed
        meta.update({"status": "FAILED", "error": type(exc).__name__})
        write_json_atomic(state_path, meta, mode=0o644)
        return meta
