"""Fail-closed parallel shadow estimates that never override the live book."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from coin_estimator import estimate_rates, iso_utc, load_model, write_json_atomic


def _centers(estimate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for settlement, body in (estimate.get("settlements") or {}).items():
        for item in body.get("rates") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("commodity_name") or "")
            key = f"{name}:{settlement}"
            out[key] = {
                "status": item.get("status"),
                "estimated_project_price": item.get("estimated_project_price"),
                "method": item.get("method"),
            }
    return out


def compare_centers(live: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    left = _centers(live)
    right = _centers(shadow)
    paired = []
    abs_pct: list[float] = []
    for key in sorted(set(left) | set(right)):
        a = left.get(key) or {"status": "MISSING"}
        b = right.get(key) or {"status": "MISSING"}
        rel = None
        if (
            a.get("status") == "ESTIMATED"
            and b.get("status") == "ESTIMATED"
            and a.get("estimated_project_price") is not None
            and b.get("estimated_project_price") is not None
        ):
            av = float(a["estimated_project_price"])
            bv = float(b["estimated_project_price"])
            rel = abs(av - bv) / max(abs(av), 1.0)
            abs_pct.append(rel)
        paired.append({"key": key, "live": a, "shadow": b, "abs_pct": rel})
    return {
        "paired_estimated_count": len(abs_pct),
        "mean_abs_pct": (sum(abs_pct) / len(abs_pct)) if abs_pct else None,
        "median_abs_pct": (sorted(abs_pct)[len(abs_pct) // 2] if abs_pct else None),
        "pairs": paired,
    }


def run_shadow_parallel(
    *,
    live_estimate: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    end: datetime,
    shadow_model_path: Path | None,
    shadow_state_path: Path,
    live_group_events_enabled: bool,
    group_live_events_before: datetime | None,
) -> dict[str, Any]:
    """Estimate with an optional shadow model and write an isolated state file."""

    meta: dict[str, Any] = {
        "enabled": False,
        "status": "DISABLED",
        "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
        "shadow_model_path": str(shadow_model_path) if shadow_model_path else None,
        "authoritative_override": False,
    }
    if shadow_model_path is None or not shadow_model_path.is_file():
        write_json_atomic(shadow_state_path, meta, mode=0o644)
        return meta
    try:
        shadow_model = load_model(shadow_model_path)
        shadow_estimate = estimate_rates(
            shadow_model,
            market_db,
            end,
            conversation_db,
            live_group_events_enabled=live_group_events_enabled,
            group_live_events_before=group_live_events_before,
        )
        comparison = compare_centers(live_estimate, shadow_estimate)
        payload = {
            "enabled": True,
            "status": "OK",
            "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
            "shadow_model_path": str(shadow_model_path),
            "shadow_model_kind": shadow_model.get("model_kind"),
            "authoritative_override": False,
            "comparison_vs_live": {
                "paired_estimated_count": comparison["paired_estimated_count"],
                "mean_abs_pct": comparison["mean_abs_pct"],
                "median_abs_pct": comparison["median_abs_pct"],
            },
            "estimate": shadow_estimate,
            "pair_details": comparison["pairs"],
        }
        write_json_atomic(shadow_state_path, payload, mode=0o644)
        meta.update(
            {
                "enabled": True,
                "status": "OK",
                "shadow_model_kind": shadow_model.get("model_kind"),
                "comparison_vs_live": payload["comparison_vs_live"],
            }
        )
        return meta
    except Exception as exc:  # fail-closed: live path must continue
        meta.update({"status": "FAILED", "error": type(exc).__name__})
        write_json_atomic(shadow_state_path, meta, mode=0o644)
        return meta
