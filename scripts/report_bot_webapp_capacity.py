#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


BOT_WEBAPP_CAPACITY_REPORT_SCHEMA_VERSION = "bot_webapp_capacity_report_v1"
PRODUCTION_GATE_BLOCKED_STATUS = "blocked_until_owner_staging_validation"
SURFACE_TO_ROLE = {
    "telegram": "telegram_foreign",
    "webapp": "webapp_iran",
}
REQUIRED_LATENCY_FIELDS = ("p50_ms", "p95_ms", "p99_ms", "max_ms")
REQUIRED_COUNT_FIELDS = ("success", "rejected", "error")
REQUIRED_OBSERVABILITY_FIELDS = (
    "db_pool",
    "redis",
    "sync",
    "worker_backlog",
    "telegram_gateway_boundary",
)


class CapacityReportError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CapacityReportError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CapacityReportError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def latency_summary(payload: dict[str, Any] | None) -> dict[str, float]:
    source = payload or {}
    return {field: as_float(source.get(field)) for field in REQUIRED_LATENCY_FIELDS}


def count_summary(payload: dict[str, Any] | None) -> dict[str, int]:
    source = payload or {}
    return {field: as_int(source.get(field)) for field in REQUIRED_COUNT_FIELDS}


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def extract_mixed_load_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("mixed_load"), dict):
        return payload["mixed_load"]
    if isinstance(payload.get("reports"), dict):
        return payload
    raise CapacityReportError("mixed-load payload must contain either mixed_load.reports or reports")


def normalize_observability(observability: dict[str, Any], *, telegram_gateway_boundary: str) -> dict[str, Any]:
    if not isinstance(observability, dict):
        raise CapacityReportError("observability must be a JSON object")

    normalized = deepcopy(observability)
    normalized["telegram_gateway_boundary"] = telegram_gateway_boundary
    missing = [field for field in REQUIRED_OBSERVABILITY_FIELDS if field not in normalized]
    if missing:
        raise CapacityReportError(f"observability is missing required fields: {', '.join(missing)}")
    return normalized


def scenario_counts_from_summary(summary: dict[str, Any]) -> dict[str, int]:
    counts = count_summary(summary)
    counts["total"] = as_int(summary.get("total"))
    return counts


def hot_offer_state_from_report(report: dict[str, Any]) -> dict[str, Any]:
    persistence = report.get("persistence") or {}
    hot_offer = {
        "offer_status": first_present(report.get("offer_status"), persistence.get("offer_status")),
        "remaining_quantity": first_present(report.get("offer_remaining_quantity"), persistence.get("remaining_quantity")),
        "original_quantity": persistence.get("original_quantity"),
        "completed_trade_quantity": persistence.get("completed_trade_quantity"),
        "persisted_trade_count": first_present(report.get("persisted_trade_count"), persistence.get("persisted_trade_count")),
        "expected_winner_count": report.get("expected_winner_count"),
        "completed_ledger_count": persistence.get("completed_ledger_count"),
        "trades_without_completed_ledger_count": persistence.get("trades_without_completed_ledger_count"),
        "failed_internal_ledger_count": persistence.get("failed_internal_ledger_count"),
        "persistence": persistence,
    }
    hot_offer["trade_ledger_consistent"] = (
        as_int(hot_offer.get("trades_without_completed_ledger_count")) == 0
        and as_int(hot_offer.get("failed_internal_ledger_count")) == 0
        and as_int(hot_offer.get("completed_ledger_count")) >= as_int(hot_offer.get("persisted_trade_count"))
    )
    return hot_offer


def scenario_from_report(name: str, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    surfaces: dict[str, Any] = {}
    for surface, payload in sorted((summary.get("surfaces") or {}).items()):
        surfaces[surface] = {
            "counts": {
                **count_summary(payload),
                "total": as_int(payload.get("total")),
            },
            "latency": latency_summary(payload.get("latency") or {}),
        }

    return {
        "name": name,
        "business_request_rps": as_float(summary.get("business_request_rps")),
        "attempt_start_rps": as_float(summary.get("attempt_start_rps"), default=as_float(summary.get("business_request_rps"))),
        "telegram_update_rps": as_float(summary.get("telegram_update_rps")),
        "counts": scenario_counts_from_summary(summary),
        "latency": latency_summary(summary.get("latency") or {}),
        "surfaces": surfaces,
        "hot_offer": hot_offer_state_from_report(report),
    }


def merge_role_summaries(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    roles: dict[str, Any] = {
        role: {
            "surface": surface,
            "counts": {"total": 0, "success": 0, "rejected": 0, "error": 0},
            "latency": {field: 0.0 for field in REQUIRED_LATENCY_FIELDS},
            "scenario_latency": {},
        }
        for surface, role in SURFACE_TO_ROLE.items()
    }

    for scenario in scenarios:
        for surface, surface_summary in (scenario.get("surfaces") or {}).items():
            role_name = SURFACE_TO_ROLE.get(surface)
            if role_name is None:
                continue
            role = roles[role_name]
            counts = surface_summary.get("counts") or {}
            for field in ("total", *REQUIRED_COUNT_FIELDS):
                role["counts"][field] += as_int(counts.get(field))
            latency = latency_summary(surface_summary.get("latency") or {})
            role["scenario_latency"][scenario["name"]] = latency
            for field in REQUIRED_LATENCY_FIELDS:
                role["latency"][field] = max(role["latency"][field], latency[field])

    return roles


def collect_correctness_failures(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for scenario in report.get("scenarios") or []:
        name = scenario.get("name", "unknown")
        counts = scenario.get("counts") or {}
        hot_offer = scenario.get("hot_offer") or {}

        if as_int(counts.get("error")):
            failures.append(f"{name}: request errors were observed")

        expected_winner_count = hot_offer.get("expected_winner_count")
        persisted_trade_count = as_int(hot_offer.get("persisted_trade_count"))
        if expected_winner_count is not None and persisted_trade_count != as_int(expected_winner_count):
            failures.append(
                f"{name}: expected {as_int(expected_winner_count)} persisted trades, got {persisted_trade_count}"
            )

        remaining_quantity = hot_offer.get("remaining_quantity")
        if remaining_quantity is None or as_int(remaining_quantity, default=-1) < 0:
            failures.append(f"{name}: invalid remaining_quantity={remaining_quantity}")

        original_quantity = hot_offer.get("original_quantity")
        completed_trade_quantity = hot_offer.get("completed_trade_quantity")
        if original_quantity is not None and completed_trade_quantity is not None:
            if as_int(completed_trade_quantity) > as_int(original_quantity):
                failures.append(
                    f"{name}: over-traded quantity {completed_trade_quantity} > {original_quantity}"
                )

        if not hot_offer.get("trade_ledger_consistent"):
            failures.append(f"{name}: trade/request ledger is inconsistent")

    return failures


def collect_capacity_warnings(report: dict[str, Any], *, target_business_rps: float) -> list[str]:
    warnings: list[str] = []
    for scenario in report.get("scenarios") or []:
        attempt_start_rps = as_float(scenario.get("attempt_start_rps"), default=as_float(scenario.get("business_request_rps")))
        if attempt_start_rps < target_business_rps:
            warnings.append(
                f"{scenario.get('name', 'unknown')}: attempt_start_rps {attempt_start_rps} below target {target_business_rps}"
            )
    return warnings


def build_capacity_report(
    *,
    mixed_payload: dict[str, Any],
    observability: dict[str, Any],
    telegram_gateway_boundary: str,
    target_business_rps: float = 600.0,
) -> dict[str, Any]:
    mixed_load = extract_mixed_load_payload(mixed_payload)
    reports = mixed_load.get("reports") or {}
    if not reports:
        raise CapacityReportError("mixed-load payload does not contain any scenario reports")

    boundary = str(telegram_gateway_boundary or "").strip().lower()
    if boundary not in {"mock", "real"}:
        raise CapacityReportError("telegram gateway boundary must be either 'mock' or 'real'")

    scenarios = [scenario_from_report(name, report) for name, report in sorted(reports.items())]
    roles = merge_role_summaries(scenarios)
    report = {
        "schema_version": BOT_WEBAPP_CAPACITY_REPORT_SCHEMA_VERSION,
        "target_business_rps": float(target_business_rps),
        "attempt_start_rps": min((scenario["attempt_start_rps"] for scenario in scenarios), default=0.0),
        "business_request_rps": min((scenario["business_request_rps"] for scenario in scenarios), default=0.0),
        "telegram_update_rps": min((scenario["telegram_update_rps"] for scenario in scenarios), default=0.0),
        "roles": roles,
        "scenarios": scenarios,
        "observability": normalize_observability(
            observability,
            telegram_gateway_boundary=boundary,
        ),
        "telegram_gateway_boundary": boundary,
        "production_gate": {
            "status": PRODUCTION_GATE_BLOCKED_STATUS,
            "reason": "Owner-led staging validation must review this artifact before production consideration.",
        },
    }
    report["correctness_failures"] = collect_correctness_failures(report)
    report["capacity_warnings"] = collect_capacity_warnings(report, target_business_rps=target_business_rps)
    return report


def validate_capacity_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != BOT_WEBAPP_CAPACITY_REPORT_SCHEMA_VERSION:
        raise CapacityReportError("unsupported capacity report schema_version")

    gate = report.get("production_gate") or {}
    if gate.get("status") != PRODUCTION_GATE_BLOCKED_STATUS:
        raise CapacityReportError("production_gate must remain blocked until owner staging validation")

    if not isinstance(report.get("correctness_failures"), list):
        raise CapacityReportError("correctness_failures must be a list")
    if not isinstance(report.get("capacity_warnings"), list):
        raise CapacityReportError("capacity_warnings must be a list")
    if "business_request_rps" not in report or "telegram_update_rps" not in report or "attempt_start_rps" not in report:
        raise CapacityReportError("capacity report is missing top-level RPS fields")

    observability = report.get("observability") or {}
    missing_observability = [field for field in REQUIRED_OBSERVABILITY_FIELDS if field not in observability]
    if missing_observability:
        raise CapacityReportError(
            f"observability is missing required fields: {', '.join(missing_observability)}"
        )
    if report.get("telegram_gateway_boundary") != observability.get("telegram_gateway_boundary"):
        raise CapacityReportError("telegram_gateway_boundary must match observability.telegram_gateway_boundary")

    roles = report.get("roles") or {}
    for role_name in ("telegram_foreign", "webapp_iran"):
        role = roles.get(role_name) or {}
        missing_latency = [field for field in REQUIRED_LATENCY_FIELDS if field not in (role.get("latency") or {})]
        missing_counts = [field for field in ("total", *REQUIRED_COUNT_FIELDS) if field not in (role.get("counts") or {})]
        if missing_latency or missing_counts:
            raise CapacityReportError(
                f"{role_name} is missing latency fields {missing_latency} or count fields {missing_counts}"
            )

    scenarios = report.get("scenarios") or []
    if not scenarios:
        raise CapacityReportError("capacity report must contain at least one scenario")
    for scenario in scenarios:
        if (
            "business_request_rps" not in scenario
            or "telegram_update_rps" not in scenario
            or "attempt_start_rps" not in scenario
        ):
            raise CapacityReportError(f"{scenario.get('name', 'unknown')} is missing RPS fields")
        hot_offer = scenario.get("hot_offer") or {}
        for field in (
            "offer_status",
            "remaining_quantity",
            "persisted_trade_count",
            "trade_ledger_consistent",
        ):
            if field not in hot_offer:
                raise CapacityReportError(f"{scenario.get('name', 'unknown')} hot_offer is missing {field}")

    return {
        "schema_version": report["schema_version"],
        "status": "valid",
        "correctness_failure_count": len(report["correctness_failures"]),
        "capacity_warning_count": len(report["capacity_warnings"]),
        "production_gate": gate["status"],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate Bot/WebApp staging capacity reports")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a capacity report from mixed-load and observability JSON")
    build.add_argument("--mixed-payload", required=True, type=Path)
    build.add_argument("--observability", required=True, type=Path)
    build.add_argument("--telegram-gateway-boundary", required=True, choices=("mock", "real"))
    build.add_argument("--target-business-rps", type=float, default=600.0)
    build.add_argument("--output", required=True, type=Path)

    validate = subparsers.add_parser("validate", help="validate an existing capacity report")
    validate.add_argument("--artifact", required=True, type=Path)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "build":
            report = build_capacity_report(
                mixed_payload=read_json(args.mixed_payload),
                observability=read_json(args.observability),
                telegram_gateway_boundary=args.telegram_gateway_boundary,
                target_business_rps=args.target_business_rps,
            )
            write_json(args.output, report)
            print(json.dumps(validate_capacity_report(report), ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "validate":
            print(json.dumps(validate_capacity_report(read_json(args.artifact)), ensure_ascii=False, sort_keys=True))
            return 0

    except CapacityReportError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1

    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
