#!/usr/bin/env python3
"""Consume the production full-matrix manifest and build an execution plan.

This runner is fail-closed: the current implementation is a manifest-driven
dry-run/planning runner only. It does not create production users, offers,
trades, notifications, or Telegram messages. Real production execution must be
added as explicit per-section drivers after the owner approves the execution
contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_production_full_matrix_manifest as manifest_builder


SCHEMA_VERSION = "production_full_matrix_runner_plan_v1"
EXECUTION_CONFIRM_ENV = "PRODUCTION_FULL_MATRIX_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-production-full-matrix"

SECTION_DRIVER_STATUS = {
    "market_behavior": "pending_production_market_driver",
    "delivery_contract": "pending_delivery_assertion_driver",
    "targeted_trade_delivery_join": "pending_targeted_join_production_driver",
    "production_base_trade_shape": "pending_two_server_trade_driver",
    "production_stress_overlay": "pending_two_server_stress_driver",
    "negative_business_guard": "pending_negative_guard_driver",
}


class RunnerError(ValueError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RunnerError(f"manifest must be a JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_manifest(args: argparse.Namespace) -> dict[str, Any]:
    if args.manifest:
        manifest = read_json(args.manifest)
    else:
        manifest = manifest_builder.build_manifest(prefix=args.prefix)
    errors = manifest_builder.validate_manifest(manifest)
    if errors:
        raise RunnerError("; ".join(errors))
    return manifest


def section_records(manifest: dict[str, Any], sections: set[str]) -> list[dict[str, Any]]:
    available_sections = manifest.get("sections") or {}
    unknown = sorted(sections.difference(available_sections))
    if unknown:
        raise RunnerError(f"unknown manifest section(s): {', '.join(unknown)}")
    selected_sections = sections or set(available_sections)
    records: list[dict[str, Any]] = []
    for section_name in sorted(selected_sections):
        for record in available_sections.get(section_name) or []:
            item = dict(record)
            item["section"] = section_name
            records.append(item)
    return records


def matches_optional(value: Any, allowed: set[str]) -> bool:
    return not allowed or str(value) in allowed


def filter_records(records: Iterable[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest_ids = set(args.manifest_id or [])
    outage_ids = set(args.outage_id or [])
    surface_pairs = set(args.surface_pair or [])
    actor_pair_ids = set(args.actor_pair_id or [])
    offer_types = set(args.offer_type or [])
    shapes = set(args.shape or [])

    selected: list[dict[str, Any]] = []
    for record in records:
        if manifest_ids and record.get("manifest_id") not in manifest_ids:
            continue
        if not matches_optional(record.get("outage_id"), outage_ids):
            continue
        if not matches_optional(record.get("surface_pair"), surface_pairs):
            continue
        if not matches_optional(record.get("actor_pair_id"), actor_pair_ids):
            continue
        if not matches_optional(record.get("offer_type"), offer_types):
            continue
        if not matches_optional(record.get("shape"), shapes):
            continue
        policy_supported = record.get("policy_supported")
        if args.policy == "supported" and policy_supported is not True:
            continue
        if args.policy == "unsupported" and policy_supported is not False:
            continue
        selected.append(record)

    if args.shard_count < 1:
        raise RunnerError("--shard-count must be >= 1")
    if args.shard_index < 1 or args.shard_index > args.shard_count:
        raise RunnerError("--shard-index must be between 1 and --shard-count")
    if args.shard_count > 1:
        selected = [
            record
            for index, record in enumerate(selected, start=1)
            if ((index - 1) % args.shard_count) + 1 == args.shard_index
        ]

    if args.max_scenarios is not None:
        selected = selected[: max(0, int(args.max_scenarios))]
    return selected


def count_by(records: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        value = record.get(key)
        if value is None:
            value = "none"
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def selected_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    policy_counter: Counter[str] = Counter()
    for record in records:
        value = record.get("policy_supported")
        if value is True:
            policy_counter["supported"] += 1
        elif value is False:
            policy_counter["unsupported"] += 1
        else:
            policy_counter["not_applicable"] += 1
    return {
        "selected_count": len(records),
        "by_section": count_by(records, "section"),
        "by_kind": count_by(records, "kind"),
        "by_quadrant": count_by(records, "quadrant"),
        "by_surface_pair": count_by(records, "surface_pair"),
        "by_outage": count_by(records, "outage_id"),
        "by_offer_type": count_by(records, "offer_type"),
        "by_shape": count_by(records, "shape"),
        "by_policy": dict(sorted(policy_counter.items())),
    }


def planned_steps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for order, record in enumerate(records, start=1):
        section = str(record["section"])
        steps.append(
            {
                "order": order,
                "manifest_id": record.get("manifest_id"),
                "section": section,
                "kind": record.get("kind"),
                "driver_status": SECTION_DRIVER_STATUS.get(section, "pending_driver"),
                "policy_supported": record.get("policy_supported"),
                "expected_outcome": record.get("expected_outcome"),
                "assertion_refs": list(record.get("assertion_refs") or []),
                "scenario_ref": {
                    "actor_pair_id": record.get("actor_pair_id"),
                    "surface_pair": record.get("surface_pair"),
                    "quadrant": record.get("quadrant"),
                    "outage_id": record.get("outage_id"),
                    "offer_type": record.get("offer_type"),
                    "shape": record.get("shape"),
                    "family": record.get("family"),
                    "case_id": record.get("case_id"),
                    "base_manifest_id": record.get("base_manifest_id"),
                },
            }
        )
    return steps


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_manifest(args)
    sections = set(args.section or [])
    records = section_records(manifest, sections)
    selected = filter_records(records, args)
    steps = planned_steps(selected)
    status = "planned"
    if args.execute:
        status = "blocked_not_implemented"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "environment": "production",
        "mutates_production": False,
        "execute_requested": bool(args.execute),
        "status": status,
        "prefix": manifest.get("prefix"),
        "manifest": {
            "schema_version": manifest.get("schema_version"),
            "summary": manifest.get("summary"),
            "source": str(args.manifest) if args.manifest else "generated",
        },
        "selection": {
            "sections": sorted(sections) if sections else "all",
            "manifest_ids": args.manifest_id or [],
            "policy": args.policy,
            "outage_ids": args.outage_id or [],
            "surface_pairs": args.surface_pair or [],
            "actor_pair_ids": args.actor_pair_id or [],
            "offer_types": args.offer_type or [],
            "shapes": args.shape or [],
            "max_scenarios": args.max_scenarios,
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
        },
        "selected_summary": selected_summary(selected),
        "execution_contract": {
            "requires_isolation": True,
            "requires_exact_prefix_cleanup": True,
            "requires_two_server_execution": True,
            "confirmation_env": EXECUTION_CONFIRM_ENV,
            "confirmation_value": EXECUTION_CONFIRM_VALUE,
            "production_drivers_implemented": False,
            "reason": "This runner currently plans manifest execution only; production drivers are not implemented.",
        },
        "steps": steps,
    }


def compact_stdout(plan: dict[str, Any], output: Path | None) -> dict[str, Any]:
    return {
        "schema_version": plan["schema_version"],
        "status": plan["status"],
        "prefix": plan["prefix"],
        "output": str(output) if output else None,
        "execute_requested": plan["execute_requested"],
        "selected_summary": plan["selected_summary"],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Existing manifest JSON. If omitted, a manifest is generated.")
    parser.add_argument("--prefix", default=manifest_builder.default_prefix())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--section", action="append", default=[])
    parser.add_argument("--manifest-id", action="append", default=[])
    parser.add_argument("--policy", choices=["all", "supported", "unsupported"], default="all")
    parser.add_argument("--outage-id", action="append", default=[])
    parser.add_argument("--surface-pair", action="append", default=[])
    parser.add_argument("--actor-pair-id", action="append", default=[])
    parser.add_argument("--offer-type", action="append", choices=["buy", "sell"], default=[])
    parser.add_argument("--shape", action="append", default=[])
    parser.add_argument("--max-scenarios", type=int)
    parser.add_argument("--shard-index", type=int, default=1)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--execute", action="store_true", help="Request production execution. Currently fail-closed.")
    parser.add_argument("--print-full", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(args)
    except RunnerError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    if args.output:
        write_json(args.output, plan)
    stdout_payload = plan if args.print_full else compact_stdout(plan, args.output)
    print(json.dumps(stdout_payload, ensure_ascii=False, sort_keys=True))
    return 2 if args.execute else 0


if __name__ == "__main__":
    raise SystemExit(main())
