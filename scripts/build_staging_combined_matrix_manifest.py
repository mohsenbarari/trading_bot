#!/usr/bin/env python3
"""Build the combined market × queue × overtime × estimate staging manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "staging_combined_matrix_manifest_v1"

VALID_OFFER_TARGET = 4000
INVALID_ATTEMPT_TARGET = 800
WAVE_SECONDS = 1800
# Offer create surfaces on the two-server staging topology:
# Iran=webapp, foreign=telegram/bot. Offers must be telegram-heavy.
WEBAPP_OFFER_SHARE = 0.40
BOT_OFFER_SHARE = 0.60
# Requests placed on offers (market drivers) stay balanced.
WEBAPP_REQUEST_SHARE = 0.50
TELEGRAM_REQUEST_SHARE = 0.50

# Mandatory coverage cells that must be non-empty after build.
MANDATORY_CELLS = (
    "market:authority:iran_iran",
    "market:authority:iran_foreign",
    "market:authority:foreign_iran",
    "market:authority:foreign_foreign",
    "market:shape:wholesale",
    "market:shape:retail_two_lot",
    "market:shape:retail_three_lot",
    "market:actor:tier1_customer",
    "market:actor:tier2_customer",
    "market:terminal:completed",
    "market:terminal:partial",
    "market:terminal:rejected",
    "market:terminal:time_expired",
    "queue:surface:webapp",
    "queue:surface:bot",
    "queue:surface:telegram_heavy",
    "queue:wave:valid",
    "queue:wave:invalid",
    "queue:regime:peak",
    "market:request_surface:balanced",
    "market:comprehensive:all_228",
    "market:comprehensive:family:create_offer",
    "market:comprehensive:family:trade_concurrent",
    "market:comprehensive:family:trade_non_concurrent",
    "market:comprehensive:family:manual_expire_contention",
    "market:comprehensive:family:time_expiry",
    "market:comprehensive:family:after_completed_reject",
    "market:comprehensive:family:after_manual_expiry_reject",
    "market:comprehensive:family:after_time_expiry_reject",
    "market:comprehensive:family:manual_expire_non_concurrent",
    "market:comprehensive:family:active_view",
    "market:comprehensive:family:public_detail_view",
    "market:comprehensive:family:market_history_view",
    "overtime:pref_save",
    "overtime:offer_snapshot",
    "overtime:req_iran_iran",
    "overtime:req_foreign_foreign",
    "overtime:req_cross_forward",
    "overtime:queue_order",
    "overtime:cancel_requester",
    "overtime:final_tail",
    "overtime:channel_marker",
    "overtime:tg_retry",
    "overtime:ui_reconnect",
    "overtime:disabled_regression",
    "estimate:preview_shadow",
    "estimate:selectable_accept",
    "estimate:selectable_decline",
    "estimate:no_data_fail_closed",
)

COMPREHENSIVE_MARKET_FAMILY_COUNTS = {
    "create_offer": 12,
    "trade_concurrent": 12,
    "trade_non_concurrent": 12,
    "manual_expire_contention": 12,
    "time_expiry": 12,
    "after_completed_reject": 12,
    "after_manual_expiry_reject": 12,
    "after_time_expiry_reject": 12,
    "manual_expire_non_concurrent": 24,
    "active_view": 24,
    "public_detail_view": 48,
    "market_history_view": 36,
}
COMPREHENSIVE_MARKET_SCENARIO_COUNT = sum(
    COMPREHENSIVE_MARKET_FAMILY_COUNTS.values()
)

OT_FAMILIES = (
    ("OT-PREF-WEBAPP-SAVE", "overtime:pref_save"),
    ("OT-PREF-BOT-SAVE", "overtime:pref_save"),
    ("OT-PREF-DISABLED-REGRESSION", "overtime:disabled_regression"),
    ("OT-OFFER-WEBAPP-ORIGIN", "overtime:offer_snapshot"),
    ("OT-OFFER-BOT-ORIGIN", "overtime:offer_snapshot"),
    ("OT-REQ-IRAN-TO-IRAN", "overtime:req_iran_iran"),
    ("OT-REQ-FOREIGN-TO-FOREIGN", "overtime:req_foreign_foreign"),
    ("OT-REQ-CROSS-FORWARD", "overtime:req_cross_forward"),
    ("OT-QUEUE-ORDER", "overtime:queue_order"),
    ("OT-CANCEL-REQUESTER", "overtime:cancel_requester"),
    ("OT-FINAL-TAIL", "overtime:final_tail"),
    ("OT-CHANNEL-MARKER", "overtime:channel_marker"),
    ("OT-TG-RETRY", "overtime:tg_retry"),
    ("OT-UI-RECONNECT", "overtime:ui_reconnect"),
    ("OT-SYNC-RECOVERY", "overtime:req_cross_forward"),
)


def _slot(
    *,
    cell: str,
    lane: str,
    scenario_id: str,
    tags: list[str],
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "cell": cell,
        "lane": lane,
        "scenario_id": scenario_id,
        "coverage_tags": sorted(set(tags + [cell, f"lane:{lane}"])),
        "required": True,
        "detail": detail or {},
    }


def market_cells() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for quadrant in ("iran_iran", "iran_foreign", "foreign_iran", "foreign_foreign"):
        rows.append(
            _slot(
                cell=f"market:authority:{quadrant}",
                lane="market",
                scenario_id=f"MKT-AUTH-{quadrant.upper()}",
                tags=["market", "authority", quadrant],
            )
        )
    for shape, sid in (
        ("wholesale", "MKT-SHAPE-WHOLESALE"),
        ("retail_two_lot", "MKT-SHAPE-RETAIL2"),
        ("retail_three_lot", "MKT-SHAPE-RETAIL3"),
    ):
        rows.append(
            _slot(
                cell=f"market:shape:{shape}",
                lane="market",
                scenario_id=sid,
                tags=["market", "shape", shape],
            )
        )
    for actor, sid in (
        ("tier1_customer", "MKT-ACTOR-TIER1"),
        ("tier2_customer", "MKT-ACTOR-TIER2"),
    ):
        rows.append(
            _slot(
                cell=f"market:actor:{actor}",
                lane="market",
                scenario_id=sid,
                tags=["market", "actor", actor],
            )
        )
    for terminal, sid in (
        ("completed", "MKT-TERM-COMPLETED"),
        ("partial", "MKT-TERM-PARTIAL"),
        ("rejected", "MKT-TERM-REJECTED"),
        ("time_expired", "MKT-TERM-TIME-EXPIRED"),
    ):
        rows.append(
            _slot(
                cell=f"market:terminal:{terminal}",
                lane="market",
                scenario_id=sid,
                tags=["market", "terminal", terminal],
            )
        )
    return rows


def comprehensive_market_cells() -> list[dict[str, Any]]:
    """Coverage contract for the exhaustive 228-state Bot/WebApp matrix."""

    rows = [
        _slot(
            cell="market:comprehensive:all_228",
            lane="market_comprehensive",
            scenario_id="CLM-ALL-228",
            tags=["market", "comprehensive", "all_states"],
            detail={"required_scenario_count": COMPREHENSIVE_MARKET_SCENARIO_COUNT},
        )
    ]
    for family, expected_count in COMPREHENSIVE_MARKET_FAMILY_COUNTS.items():
        rows.append(
            _slot(
                cell=f"market:comprehensive:family:{family}",
                lane="market_comprehensive",
                scenario_id=f"CLM-FAMILY-{family.upper().replace('_', '-')}",
                tags=["market", "comprehensive", "family", family],
                detail={"family": family, "required_scenario_count": expected_count},
            )
        )
    return rows


def overtime_cells() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scenario_id, cell in OT_FAMILIES:
        if cell in seen and scenario_id != "OT-SYNC-RECOVERY":
            # Still register scenario under same cell via tags.
            rows.append(
                _slot(
                    cell=cell,
                    lane="overtime",
                    scenario_id=scenario_id,
                    tags=["overtime", "stage16", scenario_id.lower()],
                    detail={"family": scenario_id},
                )
            )
            continue
        seen.add(cell)
        rows.append(
            _slot(
                cell=cell,
                lane="overtime",
                scenario_id=scenario_id,
                tags=["overtime", "stage16", scenario_id.lower()],
                detail={"family": scenario_id},
            )
        )
    return rows


def estimate_cells() -> list[dict[str, Any]]:
    return [
        _slot(
            cell="estimate:preview_shadow",
            lane="estimate",
            scenario_id="EST-PREVIEW-SHADOW",
            tags=["estimate", "preview", "shadow"],
        ),
        _slot(
            cell="estimate:selectable_accept",
            lane="estimate",
            scenario_id="EST-SELECT-ACCEPT",
            tags=["estimate", "selection", "accept"],
        ),
        _slot(
            cell="estimate:selectable_decline",
            lane="estimate",
            scenario_id="EST-SELECT-DECLINE",
            tags=["estimate", "selection", "decline"],
        ),
        _slot(
            cell="estimate:no_data_fail_closed",
            lane="estimate",
            scenario_id="EST-NO-DATA",
            tags=["estimate", "fail_closed", "no_data"],
        ),
    ]


def build_wave_schedule(*, seed: int = 20260806) -> dict[str, Any]:
    """Deterministic 1800s schedule: 4000 valid + 800 invalid attempts."""

    rng = random.Random(seed)
    valid_times: list[float] = []
    # Base uniform then inject peak bursts across the 30-minute window.
    for _ in range(VALID_OFFER_TARGET):
        valid_times.append(rng.random() * WAVE_SECONDS)
    # Quiet / shoulder / peak: re-cluster ~20% into short peak windows (8–12/s).
    peak_windows = [
        (120.0, 128.0),
        (360.0, 368.0),
        (720.0, 728.0),
        (1080.0, 1088.0),
        (1440.0, 1448.0),
        (1680.0, 1688.0),
    ]
    peak_count = int(VALID_OFFER_TARGET * 0.20)
    for i in range(peak_count):
        start, end = peak_windows[i % len(peak_windows)]
        valid_times[i] = start + rng.random() * (end - start)
    valid_times.sort()

    invalid_times = sorted(rng.random() * WAVE_SECONDS for _ in range(INVALID_ATTEMPT_TARGET))

    events: list[dict[str, Any]] = []
    wholesale_n = VALID_OFFER_TARGET // 2
    retail2_n = VALID_OFFER_TARGET // 4
    retail3_n = VALID_OFFER_TARGET - wholesale_n - retail2_n
    shapes = (
        ["wholesale"] * wholesale_n
        + ["retail_two_lot"] * retail2_n
        + ["retail_three_lot"] * retail3_n
    )
    rng.shuffle(shapes)
    webapp_n = int(round(VALID_OFFER_TARGET * WEBAPP_OFFER_SHARE))
    bot_n = VALID_OFFER_TARGET - webapp_n
    if bot_n / float(VALID_OFFER_TARGET) < BOT_OFFER_SHARE - 1e-9:
        # Keep telegram/bot strictly at least the configured share.
        bot_n = int(round(VALID_OFFER_TARGET * BOT_OFFER_SHARE))
        webapp_n = VALID_OFFER_TARGET - bot_n
    surfaces = (["webapp"] * webapp_n) + (["bot"] * bot_n)
    rng.shuffle(surfaces)
    # Planned request surfaces stay exactly 50/50 overall and inside each
    # deterministic 100-sequence action bucket. Pairs avoid coupling request
    # surface to the alternating buy/sell offer type.
    request_surfaces = [
        "webapp" if idx % 4 in (0, 1) else "telegram"
        for idx in range(VALID_OFFER_TARGET)
    ]
    for idx, (t, shape, surface) in enumerate(zip(valid_times, shapes, surfaces)):
        events.append(
            {
                "seq": idx,
                "t_seconds": round(t, 3),
                "kind": "valid",
                "shape": shape,
                "surface": surface,
                "request_surface": request_surfaces[idx],
                "offer_type": "buy" if idx % 2 == 0 else "sell",
                "settlement": "cash" if idx % 3 else "tomorrow",
                "overtime_creator": idx % 17 == 0,
                "estimate_probe": surface == "webapp" and idx % 23 == 0,
            }
        )
    invalid_webapp_n = int(round(INVALID_ATTEMPT_TARGET * WEBAPP_OFFER_SHARE))
    invalid_surfaces = (["webapp"] * invalid_webapp_n) + (
        ["bot"] * (INVALID_ATTEMPT_TARGET - invalid_webapp_n)
    )
    rng.shuffle(invalid_surfaces)
    for idx, t in enumerate(invalid_times):
        events.append(
            {
                "seq": VALID_OFFER_TARGET + idx,
                "t_seconds": round(t, 3),
                "kind": "invalid",
                "shape": "invalid",
                "surface": invalid_surfaces[idx],
                "request_surface": None,
                "offer_type": "sell",
                "settlement": "cash",
                "overtime_creator": False,
                "estimate_probe": False,
            }
        )
    events.sort(key=lambda item: (item["t_seconds"], item["seq"]))
    raw = json.dumps(events, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    peak_events = sum(
        1
        for item in events
        if item["kind"] == "valid"
        and any(start <= item["t_seconds"] <= end for start, end in peak_windows)
    )
    valid_events = [item for item in events if item["kind"] == "valid"]
    webapp_valid = sum(1 for item in valid_events if item["surface"] == "webapp")
    bot_valid = sum(1 for item in valid_events if item["surface"] == "bot")
    request_webapp = sum(1 for item in valid_events if item.get("request_surface") == "webapp")
    request_telegram = sum(1 for item in valid_events if item.get("request_surface") == "telegram")
    return {
        "seed": seed,
        "wave_seconds": WAVE_SECONDS,
        "valid_target": VALID_OFFER_TARGET,
        "invalid_target": INVALID_ATTEMPT_TARGET,
        "event_count": len(events),
        "peak_valid_events": peak_events,
        "peak_windows": peak_windows,
        "offer_surface_mix": {
            "webapp": webapp_valid,
            "bot": bot_valid,
            "webapp_share": round(webapp_valid / float(VALID_OFFER_TARGET), 4),
            "bot_share": round(bot_valid / float(VALID_OFFER_TARGET), 4),
        },
        "request_surface_mix": {
            "webapp": request_webapp,
            "telegram": request_telegram,
            "webapp_share": round(request_webapp / float(VALID_OFFER_TARGET), 4),
            "telegram_share": round(request_telegram / float(VALID_OFFER_TARGET), 4),
        },
        "schedule_sha256": digest,
        "events": events,
        "queue_cells": [
            _slot(
                cell="queue:surface:webapp",
                lane="queue",
                scenario_id="Q-WAVE-WEBAPP",
                tags=["queue", "wave", "webapp"],
                detail={"valid_events": webapp_valid, "target_share": WEBAPP_OFFER_SHARE},
            ),
            _slot(
                cell="queue:surface:bot",
                lane="queue",
                scenario_id="Q-WAVE-BOT",
                tags=["queue", "wave", "bot", "telegram"],
                detail={"valid_events": bot_valid, "target_share": BOT_OFFER_SHARE},
            ),
            _slot(
                cell="queue:surface:telegram_heavy",
                lane="queue",
                scenario_id="Q-WAVE-TELEGRAM-HEAVY",
                tags=["queue", "wave", "telegram_heavy", "bot"],
                detail={
                    "bot_share": round(bot_valid / float(VALID_OFFER_TARGET), 4),
                    "webapp_share": round(webapp_valid / float(VALID_OFFER_TARGET), 4),
                    "min_bot_share": BOT_OFFER_SHARE,
                },
            ),
            _slot(
                cell="queue:wave:valid",
                lane="queue",
                scenario_id="Q-WAVE-VALID",
                tags=["queue", "wave", "valid"],
                detail={"count": VALID_OFFER_TARGET},
            ),
            _slot(
                cell="queue:wave:invalid",
                lane="queue",
                scenario_id="Q-WAVE-INVALID",
                tags=["queue", "wave", "invalid"],
                detail={"count": INVALID_ATTEMPT_TARGET},
            ),
            _slot(
                cell="queue:regime:peak",
                lane="queue",
                scenario_id="Q-WAVE-PEAK",
                tags=["queue", "wave", "peak"],
                detail={"peak_valid_events": peak_events, "windows": peak_windows},
            ),
            _slot(
                cell="market:request_surface:balanced",
                lane="market",
                scenario_id="MKT-REQUEST-SURFACE-50-50",
                tags=["market", "request_surface", "balanced"],
                detail={
                    "webapp": request_webapp,
                    "telegram": request_telegram,
                    "target_webapp_share": WEBAPP_REQUEST_SHARE,
                    "target_telegram_share": TELEGRAM_REQUEST_SHARE,
                },
            ),
        ],
    }


def build_manifest(*, seed: int = 20260806) -> dict[str, Any]:
    wave = build_wave_schedule(seed=seed)
    scenarios = (
        market_cells()
        + comprehensive_market_cells()
        + wave["queue_cells"]
        + overtime_cells()
        + estimate_cells()
    )
    by_cell: dict[str, list[str]] = {cell: [] for cell in MANDATORY_CELLS}
    for row in scenarios:
        by_cell.setdefault(row["cell"], []).append(row["scenario_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "staging_two_server",
        "feature": "combined_market_queue_overtime_estimate",
        "mutates_production": False,
        "mandatory_cells": list(MANDATORY_CELLS),
        "scenarios": scenarios,
        "wave": {
            "seed": wave["seed"],
            "wave_seconds": wave["wave_seconds"],
            "valid_target": wave["valid_target"],
            "invalid_target": wave["invalid_target"],
            "event_count": wave["event_count"],
            "peak_valid_events": wave["peak_valid_events"],
            "peak_windows": wave.get("peak_windows") or [],
            "mean_valid_rps": round(VALID_OFFER_TARGET / float(WAVE_SECONDS), 3),
            "offer_surface_mix": wave["offer_surface_mix"],
            "request_surface_mix": wave["request_surface_mix"],
            "schedule_sha256": wave["schedule_sha256"],
            "topology": "staging_two_server",
            "iran_surface": "webapp",
            "foreign_surface": "bot",
        },
        "wave_events": wave["events"],
        "coverage_index": by_cell,
        "summary": {
            "scenario_count": len(scenarios),
            "mandatory_cell_count": len(MANDATORY_CELLS),
            "filled_mandatory_cells": sum(1 for cell in MANDATORY_CELLS if by_cell.get(cell)),
        },
    }


def validate_combined_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    index = manifest.get("coverage_index") or {}
    for cell in MANDATORY_CELLS:
        if not index.get(cell):
            errors.append(f"mandatory cell empty: {cell}")
    wave = manifest.get("wave") or {}
    if int(wave.get("valid_target") or 0) != VALID_OFFER_TARGET:
        errors.append("wave valid_target mismatch")
    if int(wave.get("invalid_target") or 0) != INVALID_ATTEMPT_TARGET:
        errors.append("wave invalid_target mismatch")
    if int(wave.get("event_count") or 0) != VALID_OFFER_TARGET + INVALID_ATTEMPT_TARGET:
        errors.append("wave event_count mismatch")
    if not wave.get("schedule_sha256"):
        errors.append("wave schedule hash missing")
    offer_mix = wave.get("offer_surface_mix") or {}
    bot_share = float(offer_mix.get("bot_share") or 0.0)
    webapp_share = float(offer_mix.get("webapp_share") or 0.0)
    if bot_share + 1e-9 < BOT_OFFER_SHARE:
        errors.append(
            f"offer bot/telegram share {bot_share} below required {BOT_OFFER_SHARE}"
        )
    if abs(webapp_share - WEBAPP_OFFER_SHARE) > 0.011:
        errors.append(
            f"offer webapp share {webapp_share} outside 40% target ({WEBAPP_OFFER_SHARE})"
        )
    request_mix = wave.get("request_surface_mix") or {}
    req_web = float(request_mix.get("webapp_share") or 0.0)
    req_tg = float(request_mix.get("telegram_share") or 0.0)
    if abs(req_web - WEBAPP_REQUEST_SHARE) > 0.011 or abs(req_tg - TELEGRAM_REQUEST_SHARE) > 0.011:
        errors.append(
            f"request surface mix not 50/50 (webapp={req_web}, telegram={req_tg})"
        )
    if str(wave.get("topology") or "") != "staging_two_server":
        errors.append("wave topology must be staging_two_server")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "tmp" / "staging-combined-matrix" / "manifest-latest.json",
    )
    parser.add_argument("--omit-events", action="store_true", help="write summary without full event list")
    args = parser.parse_args(argv)
    manifest = build_manifest(seed=args.seed)
    errors = validate_combined_manifest(manifest)
    if errors:
        raise SystemExit("manifest invalid: " + "; ".join(errors))
    payload = dict(manifest)
    if args.omit_events:
        payload.pop("wave_events", None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(args.output),
                "scenario_count": manifest["summary"]["scenario_count"],
                "schedule_sha256": manifest["wave"]["schedule_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
