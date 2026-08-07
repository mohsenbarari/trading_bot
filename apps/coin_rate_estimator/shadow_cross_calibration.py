"""Shadow-assisted morning calibration for the live book (not a model swap).

Workflow:
1) Near 09:50–10:00 Tehran, snapshot main + shadow centers (pre-open).
2) After 10:30, build morning truth from [10:00,10:30) trades/offers
   (trades first; else recency-weighted offers, newer heavier).
3) Compare pre-open main/shadows to truth; where shadows are closer and
   agree on the sign of the main error, propose a bounded correction.
4) Optionally apply that correction to the live estimate (fail-closed,
   never replaces the structural model).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from coin_estimator import COMMODITY_SPECS, iso_utc, write_json_atomic
from morning_reopen import morning_open_truth_label, tehran_local

TEHRAN = ZoneInfo("Asia/Tehran")
PRICE_MULTIPLIER = 1_000
MAX_CROSS_CORRECTION = 0.012  # 1.2%
MIN_TRUTH_ABS_ERROR_TO_ACT = 0.002  # ignore noise below 0.2%
SNAPSHOT_START_MINUTE = 9 * 60 + 50
SNAPSHOT_END_MINUTE = 10 * 60
APPLY_AFTER_MINUTE = 10 * 60 + 30


def _runtime_root() -> Path:
    return Path(
        os.environ.get(
            "COIN_RATE_ESTIMATOR_RUNTIME_DIR",
            str(Path(__file__).resolve().parent / "runtime"),
        )
    )


def _centers_from_estimate(estimate: dict[str, Any] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(estimate, dict):
        return out
    for settlement, body in (estimate.get("settlements") or {}).items():
        for row in (body or {}).get("rates") or []:
            if not isinstance(row, dict) or row.get("status") != "ESTIMATED":
                continue
            price = row.get("estimated_project_price")
            name = row.get("commodity_name")
            if price is None or not name:
                continue
            out[f"{name}:{settlement}"] = float(price)
    return out


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def maybe_capture_preopen_snapshot(
    *,
    live_estimate: dict[str, Any],
    shadow_payloads: dict[str, dict[str, Any]],
    end: datetime,
) -> dict[str, Any] | None:
    """Persist one pre-open snapshot per Tehran day."""

    local = tehran_local(end)
    minute = local.hour * 60 + local.minute
    if not (SNAPSHOT_START_MINUTE <= minute < SNAPSHOT_END_MINUTE):
        return None
    day = local.date().isoformat()
    path = _runtime_root() / f"preopen-snapshot-{day}.json"
    if path.is_file():
        return _load_json(path)
    payload = {
        "day": day,
        "captured_at_utc": iso_utc(end),
        "live": _centers_from_estimate(live_estimate),
        "shadows": {
            name: _centers_from_estimate(
                body.get("estimate") if isinstance(body.get("estimate"), dict) else body
            )
            for name, body in shadow_payloads.items()
        },
    }
    write_json_atomic(path, payload, mode=0o644)
    return payload


def _relative_error(pred: float, truth: float) -> float:
    return (pred - truth) / max(abs(truth), 1.0)


def build_morning_cross_calibration(
    *,
    conversation_db: Path,
    day: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Compare pre-open books to morning truth and propose bounded fixes."""

    live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else {}
    shadows = snapshot.get("shadows") if isinstance(snapshot.get("shadows"), dict) else {}
    proposals: dict[str, Any] = {}
    details: list[dict[str, Any]] = []
    for commodity in COMMODITY_SPECS:
        for settlement in ("CASH", "TOMORROW"):
            key = f"{commodity}:{settlement}"
            live_price = live.get(key)
            if live_price is None:
                continue
            truth = morning_open_truth_label(
                conversation_db,
                day=day,
                commodity=commodity,
                settlement=settlement,
            )
            if truth.get("status") != "OBSERVED" or truth.get("truth_project_price") is None:
                details.append(
                    {
                        "key": key,
                        "status": "NO_TRUTH",
                        "truth": truth,
                    }
                )
                continue
            truth_price = float(truth["truth_project_price"])
            live_err = _relative_error(float(live_price), truth_price)
            shadow_errors: dict[str, float] = {}
            for shadow_name, centers in shadows.items():
                if not isinstance(centers, dict) or key not in centers:
                    continue
                shadow_errors[str(shadow_name)] = _relative_error(
                    float(centers[key]), truth_price
                )
            if not shadow_errors:
                details.append(
                    {
                        "key": key,
                        "status": "NO_SHADOW",
                        "live_error": live_err,
                        "truth_project_price": truth_price,
                    }
                )
                continue
            best_name = min(shadow_errors, key=lambda name: abs(shadow_errors[name]))
            best_err = shadow_errors[best_name]
            # Shadow helps only if clearly closer and same sign as needed fix
            # (or live error large while shadow near zero).
            helps = abs(best_err) + 0.0005 < abs(live_err)
            agree_direction = (
                live_err == 0
                or best_err == 0
                or math.copysign(1.0, live_err) == math.copysign(1.0, best_err)
                or abs(best_err) < 0.001
            )
            # Correction moves live toward truth: truth/live - 1 ≈ -live_err/(1+live_err)
            raw_correction = (truth_price / max(float(live_price), 1.0)) - 1.0
            # Blend with best shadow target for robustness.
            shadow_target = float(shadows[best_name][key])
            shadow_correction = (shadow_target / max(float(live_price), 1.0)) - 1.0
            blended = 0.65 * raw_correction + 0.35 * shadow_correction
            applied = 0.0
            status = "OBSERVED_NO_APPLY"
            if (
                helps
                and agree_direction
                and abs(live_err) >= MIN_TRUTH_ABS_ERROR_TO_ACT
            ):
                applied = max(-MAX_CROSS_CORRECTION, min(MAX_CROSS_CORRECTION, blended))
                status = "PROPOSED"
                proposals[key] = {
                    "correction_ratio": applied,
                    "best_shadow": best_name,
                    "live_error": live_err,
                    "best_shadow_error": best_err,
                    "truth_project_price": truth_price,
                    "truth_source": truth.get("source"),
                }
            details.append(
                {
                    "key": key,
                    "status": status,
                    "live_price": live_price,
                    "truth_project_price": truth_price,
                    "truth_source": truth.get("source"),
                    "live_error": live_err,
                    "shadow_errors": shadow_errors,
                    "best_shadow": best_name,
                    "proposed_correction": applied,
                }
            )
    return {
        "day": day,
        "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
        "mode": "SHADOW_ASSISTED_MORNING_TRUTH_CROSS_CALIBRATION",
        "max_cross_correction": MAX_CROSS_CORRECTION,
        "proposals": proposals,
        "details": details,
        "proposal_count": len(proposals),
    }


def apply_cross_calibration_to_estimate(
    estimate: dict[str, Any],
    report: dict[str, Any],
) -> int:
    """Apply proposed morning cross-cal corrections to the live estimate book."""

    proposals = report.get("proposals") if isinstance(report.get("proposals"), dict) else {}
    if not proposals:
        return 0
    applied = 0
    for settlement, body in (estimate.get("settlements") or {}).items():
        for rate in body.get("rates") or []:
            if not isinstance(rate, dict) or rate.get("status") != "ESTIMATED":
                continue
            key = f"{rate.get('commodity_name')}:{settlement}"
            proposal = proposals.get(key)
            if not isinstance(proposal, dict):
                continue
            correction = float(proposal.get("correction_ratio") or 0.0)
            if abs(correction) < 1e-12:
                continue
            price = rate.get("estimated_price_toman")
            if price is None:
                continue
            base = float(price)
            corrected = int(round(base * (1.0 + correction) / 50_000.0) * 50_000)
            rate["_pre_shadow_cross_calibration_price_toman"] = base
            rate["estimated_price_toman"] = corrected
            rate["estimated_project_price"] = int(round(corrected / PRICE_MULTIPLIER))
            rate["shadow_cross_calibration"] = {
                "status": "APPLIED",
                "correction_ratio": correction,
                "best_shadow": proposal.get("best_shadow"),
                "truth_source": proposal.get("truth_source"),
            }
            applied += 1
    return applied


def maybe_run_shadow_cross_calibration(
    *,
    live_estimate: dict[str, Any],
    conversation_db: Path,
    end: datetime,
    shadow_state_paths: dict[str, Path],
    apply: bool = True,
) -> dict[str, Any]:
    """Capture snapshot / build morning report / optionally apply corrections."""

    shadow_payloads = {
        name: _load_json(path) for name, path in shadow_state_paths.items()
    }
    snapshot = maybe_capture_preopen_snapshot(
        live_estimate=live_estimate,
        shadow_payloads=shadow_payloads,
        end=end,
    )
    local = tehran_local(end)
    minute = local.hour * 60 + local.minute
    day = local.date().isoformat()
    report_path = _runtime_root() / f"shadow-cross-calibration-{day}.json"
    meta: dict[str, Any] = {
        "enabled": True,
        "day": day,
        "snapshot_captured": snapshot is not None or (
            _runtime_root() / f"preopen-snapshot-{day}.json"
        ).is_file(),
        "report_path": str(report_path),
        "applied_count": 0,
        "status": "IDLE",
    }
    if minute < APPLY_AFTER_MINUTE:
        meta["status"] = "WAITING_FOR_MORNING_TRUTH_WINDOW"
        return meta
    if report_path.is_file():
        report = _load_json(report_path)
    else:
        snap_path = _runtime_root() / f"preopen-snapshot-{day}.json"
        snapshot = snapshot or _load_json(snap_path)
        if not snapshot:
            meta["status"] = "MISSING_PREOPEN_SNAPSHOT"
            return meta
        report = build_morning_cross_calibration(
            conversation_db=conversation_db,
            day=day,
            snapshot=snapshot,
        )
        write_json_atomic(report_path, report, mode=0o644)
    meta["proposal_count"] = report.get("proposal_count")
    meta["status"] = "REPORT_READY"
    if apply and int(report.get("proposal_count") or 0) > 0:
        # Apply at most once per day to the live book path.
        applied_marker = _runtime_root() / f"shadow-cross-calibration-applied-{day}.json"
        if not applied_marker.is_file():
            count = apply_cross_calibration_to_estimate(live_estimate, report)
            meta["applied_count"] = count
            meta["status"] = "APPLIED" if count else "REPORT_READY_NO_ROWS"
            write_json_atomic(
                applied_marker,
                {
                    "applied_at_utc": iso_utc(datetime.now(timezone.utc)),
                    "applied_count": count,
                    "proposal_count": report.get("proposal_count"),
                },
                mode=0o644,
            )
        else:
            meta["status"] = "ALREADY_APPLIED_TODAY"
            # Still re-apply the same day's proposals so the book stays consistent
            # across refresh cycles after the one-time marker was written.
            meta["applied_count"] = apply_cross_calibration_to_estimate(
                live_estimate, report
            )
    return meta
