#!/usr/bin/env python3
"""Execute (or simulate) the combined-matrix offer wave with OT/estimate hooks.

This driver is intentionally staging-safe:
* synthetic prefix must start with ``CMB_``
* default mode ``plan-replay`` only materialises timing without network mutation
* ``execute-local`` replays the schedule clock and records intended actions
* ``execute-staging`` is reserved for the parent runner which owns credentials

The heavy create/trade path is orchestrated by the parent combined runner so this
module stays free of ``core.db`` imports and can load under staging env files.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class WaveBudget:
    valid_target: int
    invalid_target: int
    scale: float
    reduction_reason: str | None = None

    @property
    def valid_limit(self) -> int:
        return max(1, int(round(self.valid_target * self.scale)))

    @property
    def invalid_limit(self) -> int:
        return max(0, int(round(self.invalid_target * self.scale)))


def _evenly_spaced(
    items: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Select deterministically across the full 30-minute timeline."""

    if limit <= 0:
        return []
    if limit >= len(items):
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    indexes = [
        int(round(index * (len(items) - 1) / float(limit - 1)))
        for index in range(limit)
    ]
    return [items[index] for index in indexes]


def _fixed_mix_quotas(total: int) -> tuple[int, int, int, int]:
    """40% deferred trades and 50/50 request surfaces within each origin."""

    raw = (total * 0.20, total * 0.20, total * 0.30, total * 0.30)
    quotas = [int(value) for value in raw]
    remainder = total - sum(quotas)
    order = sorted(
        range(4),
        key=lambda index: (raw[index] - quotas[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        quotas[index] += 1
    return quotas[0], quotas[1], quotas[2], quotas[3]


def scale_events(events: list[dict[str, Any]], budget: WaveBudget) -> list[dict[str, Any]]:
    """Scale while preserving telegram-heavy offer mix (~40% webapp / ~60% bot)."""

    valid = [item for item in events if item.get("kind") == "valid"]
    invalid = [item for item in events if item.get("kind") == "invalid"]
    if budget.valid_limit >= len(valid):
        selected_valid = list(valid)
    else:
        webapp_n = int(round(budget.valid_limit * 0.40))
        bot_n = budget.valid_limit - webapp_n
        if budget.valid_limit > 0 and bot_n / float(budget.valid_limit) < 0.60:
            bot_n = int(round(budget.valid_limit * 0.60))
            webapp_n = budget.valid_limit - bot_n
        selected_valid = []
        for surface, surface_limit in (
            ("webapp", webapp_n),
            ("bot", bot_n),
        ):
            groups: dict[tuple[bool, str], list[dict[str, Any]]] = {}
            for item in valid:
                if str(item.get("surface") or "") != surface:
                    continue
                key = (
                    int(item["seq"]) % 100 < 40,
                    str(item.get("request_surface") or "webapp"),
                )
                groups.setdefault(key, []).append(item)
            quotas = _fixed_mix_quotas(surface_limit)
            for key, quota in zip(
                (
                    (True, "webapp"),
                    (True, "telegram"),
                    (False, "webapp"),
                    (False, "telegram"),
                ),
                quotas,
            ):
                selected_valid.extend(
                    _evenly_spaced(groups.get(key, []), quota)
                )
        # Top up from whichever surface still has leftover if rounding/shortage.
        if len(selected_valid) < budget.valid_limit:
            chosen = {id(item) for item in selected_valid}
            for item in valid:
                if id(item) in chosen:
                    continue
                selected_valid.append(item)
                if len(selected_valid) >= budget.valid_limit:
                    break
        selected_valid = selected_valid[: budget.valid_limit]
    invalid_webapp_n = int(round(budget.invalid_limit * 0.40))
    invalid_bot_n = budget.invalid_limit - invalid_webapp_n
    selected_invalid = _evenly_spaced(
        [item for item in invalid if item.get("surface") == "webapp"],
        invalid_webapp_n,
    ) + _evenly_spaced(
        [item for item in invalid if item.get("surface") == "bot"],
        invalid_bot_n,
    )
    selected = selected_valid + selected_invalid
    if not selected:
        return []
    return sorted(selected, key=lambda item: (item["t_seconds"], item["seq"]))


def replay_schedule(
    events: Iterable[dict[str, Any]],
    *,
    realtime: bool,
    speed: float,
) -> list[dict[str, Any]]:
    """Walk the schedule; optionally sleep to approximate realtime/speed."""

    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    for item in events:
        target = float(item["t_seconds"]) / max(speed, 0.01)
        if realtime:
            delay = target - (time.perf_counter() - started)
            if delay > 0:
                time.sleep(min(delay, 1.0) if speed >= 50 else delay)
        action = {
            "at_utc": utc_now(),
            "seq": item["seq"],
            "t_seconds": item["t_seconds"],
            "kind": item["kind"],
            "surface": item["surface"],
            "shape": item.get("shape"),
            "overtime_creator": bool(item.get("overtime_creator")),
            "estimate_probe": bool(item.get("estimate_probe")),
            "status": "planned_action",
        }
        if item["kind"] == "invalid":
            action["intended"] = "reject_invalid_create"
        else:
            action["intended"] = "create_offer"
            if item.get("overtime_creator"):
                action["hooks"] = action.get("hooks", []) + ["overtime_snapshot"]
            if item.get("estimate_probe"):
                action["hooks"] = action.get("hooks", []) + ["estimate_preview"]
        results.append(action)
    return results


def summarise(actions: list[dict[str, Any]], budget: WaveBudget) -> dict[str, Any]:
    return {
        "checked_at_utc": utc_now(),
        "action_count": len(actions),
        "valid_actions": sum(1 for item in actions if item["kind"] == "valid"),
        "invalid_actions": sum(1 for item in actions if item["kind"] == "invalid"),
        "webapp_actions": sum(1 for item in actions if item["surface"] == "webapp"),
        "bot_actions": sum(1 for item in actions if item["surface"] == "bot"),
        "overtime_creator_actions": sum(1 for item in actions if item.get("overtime_creator")),
        "estimate_probe_actions": sum(1 for item in actions if item.get("estimate_probe")),
        "budget": {
            "scale": budget.scale,
            "valid_limit": budget.valid_limit,
            "invalid_limit": budget.invalid_limit,
            "reduction_reason": budget.reduction_reason,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--mode", choices=["plan-replay", "execute-local"], default="plan-replay")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--speed", type=float, default=1.0, help=">1 compresses waits")
    parser.add_argument("--reduction-reason", default=None)
    args = parser.parse_args(argv)

    if not str(args.run_prefix).startswith("CMB_"):
        raise SystemExit("run prefix must start with CMB_")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    events = list(manifest.get("wave_events") or [])
    wave = manifest.get("wave") or {}
    budget = WaveBudget(
        valid_target=int(wave.get("valid_target") or 1800),
        invalid_target=int(wave.get("invalid_target") or 400),
        scale=float(args.scale),
        reduction_reason=args.reduction_reason,
    )
    selected = scale_events(events, budget)
    realtime = bool(args.realtime and args.mode == "execute-local")
    actions = replay_schedule(selected, realtime=realtime, speed=float(args.speed))
    report = {
        "schema_version": "staging_combined_wave_driver_v1",
        "mode": args.mode,
        "run_prefix": args.run_prefix,
        "schedule_sha256": wave.get("schedule_sha256"),
        "summary": summarise(actions, budget),
        "actions": actions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(args.output), **report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
