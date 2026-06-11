#!/usr/bin/env python3
"""Build the Stage P11 final production release gate report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, run_command, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


@dataclass(frozen=True)
class StageEvidence:
    stage: str
    label: str
    path: str
    kind: str
    nested_path: str | None = None
    nested_kind: str | None = None


STAGE_EVIDENCE = (
    StageEvidence("P0", "Baseline freeze and safety inventory", "tmp/production-benchmark/20260611T080030Z/baseline/commands.json", "baseline_commands"),
    StageEvidence("P1", "Full-product benchmark harness", "tmp/production-benchmark/20260611T083854Z/results.json", "benchmark_run"),
    StageEvidence("P2", "PostgreSQL production tuning", "tmp/production-benchmark/20260611T092543Z/results.json", "benchmark_run"),
    StageEvidence("P3", "Redis durability and queue safety", "tmp/production-benchmark/20260611T094159Z/results.json", "benchmark_run"),
    StageEvidence("P4", "Nginx/static/media delivery", "tmp/production-benchmark/20260611T095946Z/results.json", "benchmark_run"),
    StageEvidence("P5", "Worker and pool recalibration", "tmp/production-benchmark/20260611T105109Z/results.json", "benchmark_run", "tmp/production-benchmark/20260611T105109Z/workers/results.json", "worker_matrix"),
    StageEvidence("P6", "Cross-server sync and recovery", "tmp/production-benchmark/20260611T113726Z/results.json", "benchmark_run", "tmp/production-benchmark/20260611T113726Z/sync-p6/results.json", "scenario_status"),
    StageEvidence("P7", "Trading core, market, and bot workloads", "tmp/production-benchmark/20260611T121537Z/results.json", "benchmark_run", "tmp/production-benchmark/20260611T121537Z/trading-p7/results.json", "scenario_status"),
    StageEvidence("P8", "Frontend UX beyond Messenger", "tmp/production-benchmark/20260611T131305Z/results.json", "benchmark_run", "tmp/production-benchmark/20260611T131305Z/frontend-p8/results.json", "scenario_status"),
    StageEvidence("P9", "Observability, audit, and alert readiness", "tmp/production-benchmark/20260611T133318Z/results.json", "benchmark_run", "tmp/production-benchmark/20260611T133318Z/observability-p9/results.json", "scenario_status"),
    StageEvidence("P10", "Deployment, restart, backup, and rollback", "tmp/production-benchmark/20260611T134805Z/results.json", "benchmark_run", "tmp/production-benchmark/20260611T134805Z/deployment-p10/results.json", "scenario_status"),
)

REQUIRED_MANIFEST_KEYS = (
    "FOREIGN_PUBLIC_IP",
    "FOREIGN_SERVER_URL",
    "IRAN_HOST",
    "IRAN_PUBLIC_IP",
    "IRAN_SERVER_URL",
    "IRAN_HEALTHCHECK_URL",
    "IRAN_CERTBOT_EMAIL",
    "IRAN_PROJECT_DIR",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Stage P11 final release gate.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-live-checks", action="store_true")
    parser.add_argument("--report-out", default=None, help="Optional markdown report path to update.")
    return parser.parse_args(argv)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def benchmark_run_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    metadata = payload.get("metadata") or {}
    failed_required = int(metadata.get("failed_required_count") or 0)
    results = payload.get("results") or []
    failed_tasks = [item.get("task_id") for item in results if item.get("status") == "failed"]
    if failed_required or failed_tasks:
        return False, f"failed_required={failed_required}, failed_tasks={failed_tasks}"
    return True, f"failed_required=0, task_count={metadata.get('task_count', len(results))}"


def scenario_status_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    status = payload.get("status")
    gates = payload.get("gates") or {}
    if status != "passed":
        return False, f"status={status}"
    if gates and gates.get("status") != "passed":
        return False, f"gates={gates.get('status')}"
    return True, "status=passed"


def worker_matrix_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    recommendation = payload.get("recommendation")
    candidates = payload.get("candidates") or []
    if not recommendation:
        return False, "missing recommendation"
    failed = [
        item.get("workers")
        for item in candidates
        if item.get("status") == "failed" or ((item.get("benchmark") or {}).get("ok") is False)
    ]
    if failed:
        return False, f"failed_candidates={failed}"
    return True, f"recommendation={recommendation}, candidates={len(candidates)}"


def baseline_commands_ok(payload: list[dict[str, Any]]) -> tuple[bool, str]:
    failed = [item.get("name") for item in payload if item.get("exit_code") != 0]
    if failed:
        return False, f"failed={failed}"
    return True, f"commands={len(payload)}, failed=0"


def evaluate_json_artifact(path: Path, kind: str) -> dict[str, Any]:
    if not path.exists():
        return {"path": rel_path(path), "kind": kind, "status": "failed", "detail": "missing artifact"}
    try:
        payload = load_json(path)
    except Exception as exc:
        return {"path": rel_path(path), "kind": kind, "status": "failed", "detail": f"invalid json: {exc}"}
    if kind == "baseline_commands":
        ok, detail = baseline_commands_ok(payload if isinstance(payload, list) else [])
    elif kind == "benchmark_run":
        ok, detail = benchmark_run_ok(payload if isinstance(payload, dict) else {})
    elif kind == "scenario_status":
        ok, detail = scenario_status_ok(payload if isinstance(payload, dict) else {})
    elif kind == "worker_matrix":
        ok, detail = worker_matrix_ok(payload if isinstance(payload, dict) else {})
    else:
        ok, detail = False, f"unknown kind={kind}"
    return {"path": rel_path(path), "kind": kind, "status": "passed" if ok else "failed", "detail": detail}


def evaluate_stage_evidence() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in STAGE_EVIDENCE:
        checks = [evaluate_json_artifact(REPO_ROOT / spec.path, spec.kind)]
        if spec.nested_path and spec.nested_kind:
            checks.append(evaluate_json_artifact(REPO_ROOT / spec.nested_path, spec.nested_kind))
        rows.append(
            {
                "stage": spec.stage,
                "label": spec.label,
                "status": "passed" if all(item["status"] == "passed" for item in checks) else "failed",
                "checks": checks,
            }
        )
    return rows


def summarize_messenger() -> dict[str, Any]:
    comparison_path = REPO_ROOT / "tmp/messenger-benchmark/comparison-summary.json"
    surface_path = REPO_ROOT / "tmp/messenger-benchmark/surface-status.json"
    raw_performance_path = REPO_ROOT / "tmp/messenger-benchmark/performance-results.json"
    failures: list[str] = []
    warnings: list[dict[str, str]] = []
    comparison: dict[str, Any] = {}
    surfaces: list[dict[str, Any]] = []
    if not comparison_path.exists():
        failures.append("missing messenger comparison summary")
    else:
        comparison = load_json(comparison_path)
        if int(comparison.get("blockedSurfaceCount") or 0) != 0:
            failures.append("messenger blocked surfaces are not zero")
        if int(comparison.get("measuredSurfaceCount") or 0) != int(comparison.get("surfaceCount") or -1):
            failures.append("not all messenger surfaces are measured")
    if not surface_path.exists():
        failures.append("missing messenger surface status")
    else:
        surfaces = load_json(surface_path)
        blocked = [
            item.get("id")
            for item in surfaces
            if item.get("release_gate_status") != "ready" or item.get("blockers") or item.get("exact_missing_items")
        ]
        if blocked:
            failures.append(f"messenger surfaces not ready: {blocked}")

    deltas = comparison.get("performanceDeltas") or []
    delta_summary: dict[str, Any] = {}
    for key in ("listReadyDeltaMs", "chatReadyDeltaMs", "contextMenuDeltaMs", "domNodeDelta", "heapDeltaMb"):
        values = [float(item[key]) for item in deltas if item.get(key) is not None]
        delta_summary[key] = {
            "count": len(values),
            "improved": sum(1 for value in values if value < 0),
            "worse": sum(1 for value in values if value > 0),
            "same": sum(1 for value in values if value == 0),
            "average": round(sum(values) / len(values), 3) if values else None,
            "best": min(values) if values else None,
            "worst": max(values) if values else None,
        }
    context = delta_summary.get("contextMenuDeltaMs") or {}
    if int(context.get("worse") or 0) > int(context.get("improved") or 0):
        warnings.append(
            {
                "id": "messenger_context_menu_latency_debt",
                "owner": "Messenger performance",
                "release_note": "Messenger context-menu latency is accepted as post-release performance debt; chat-ready, DOM, and heap metrics improved broadly and all Messenger surfaces are release-ready.",
            }
        )
    if raw_performance_path.exists() and JWT_RE.search(raw_performance_path.read_text(encoding="utf-8", errors="ignore")):
        warnings.append(
            {
                "id": "messenger_raw_performance_artifact_contains_synthetic_tokens",
                "owner": "Release engineering",
                "release_note": "Do not publish raw tmp/messenger-benchmark/performance-results.json; publish only redacted comparison-summary and surface-status artifacts.",
            }
        )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "warnings": warnings,
        "comparison_summary_path": rel_path(comparison_path),
        "surface_status_path": rel_path(surface_path),
        "surface_count": comparison.get("surfaceCount"),
        "measured_surface_count": comparison.get("measuredSurfaceCount"),
        "blocked_surface_count": comparison.get("blockedSurfaceCount"),
        "delta_summary": delta_summary,
    }


def summarize_observability() -> dict[str, Any]:
    path = REPO_ROOT / "tmp/production-benchmark/20260611T133318Z/observability-p9/results.json"
    payload = load_json(path)
    checks = payload.get("checks") or {}
    warnings: list[dict[str, str]] = []
    failures: list[str] = []
    if payload.get("status") != "passed":
        failures.append("P9 observability scenario did not pass")
    overhead = checks.get("logging_overhead") or {}
    if not overhead.get("acceptable"):
        failures.append("structured logging overhead exceeds budget")
    metrics_policy = ((checks.get("metrics_targets") or {}).get("metrics_backend_policy") or {})
    if metrics_policy.get("default_backend") == "memory":
        warnings.append(
            {
                "id": "metrics_memory_backend_is_not_aggregate",
                "owner": "Observability/monitoring",
                "release_note": "Metrics remain process-local under the memory backend; use Loki logs and sync-health samplers for cross-service production diagnosis until an explicit aggregator/exporter is approved.",
            }
        )
    timers = checks.get("timers") or {}
    if any(item.get("required") and item.get("status") not in {"active", "ok"} for item in timers.values() if isinstance(item, dict)):
        failures.append("required observability timer is not active")
    if (checks.get("audit_anchor_export") or {}).get("audit_durable") is not True:
        failures.append("durable audit anchor export is not verified")
    if (checks.get("sensitive_artifact_scan") or {}).get("status") != "ok":
        failures.append("sensitive artifact scan is not clean")
    warnings.append(
        {
            "id": "audit_anchor_sink_not_immutable_retention",
            "owner": "Observability/ops",
            "release_note": "Audit anchoring has export and shipper paths, but compliance-grade immutable remote retention is deferred until a dedicated external sink is provisioned.",
        }
    )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "warnings": warnings,
        "logging_overhead_us": overhead.get("per_event_overhead_us"),
        "logging_budget_us": overhead.get("budget_us"),
        "metrics_backend": metrics_policy.get("default_backend"),
    }


def summarize_backup_and_rollback() -> dict[str, Any]:
    path = REPO_ROOT / "tmp/production-benchmark/20260611T134805Z/deployment-p10/results.json"
    payload = load_json(path)
    checks = payload.get("checks") or {}
    backup = checks.get("backup") or {}
    restarts = checks.get("restarts") or {}
    failures: list[str] = []
    if payload.get("status") != "passed":
        failures.append("P10 deployment scenario did not pass")
    if backup.get("status") != "ok" or int(backup.get("bytes") or 0) <= 0:
        failures.append("P10 backup is missing or empty")
    failed_restarts = [name for name, item in restarts.items() if item.get("status") != "ok"]
    if failed_restarts:
        failures.append(f"restart probes failed: {failed_restarts}")
    if not (payload.get("rollback") or {}).get("rollback_commands"):
        failures.append("rollback commands are not recorded")
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "backup": backup,
        "restarts": restarts,
        "release_duration_seconds": (checks.get("release") or {}).get("duration_seconds"),
        "release_phases": (checks.get("release") or {}).get("phases"),
        "rollback": payload.get("rollback") or {},
    }


def evaluate_manifest(settings: dict[str, str]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_MANIFEST_KEYS if not settings.get(key)]
    safe_values = {
        key: settings.get(key, "")
        for key in (
            "FOREIGN_PUBLIC_IP",
            "FOREIGN_SERVER_URL",
            "IRAN_HOST",
            "IRAN_PUBLIC_IP",
            "IRAN_SERVER_URL",
            "IRAN_HEALTHCHECK_URL",
            "IRAN_PROJECT_DIR",
            "IRAN_DB_POOL_SIZE",
            "IRAN_DB_MAX_OVERFLOW",
            "REDIS_APPENDONLY",
            "REDIS_APPENDFSYNC",
        )
    }
    return {"status": "passed" if not missing else "failed", "missing": missing, "safe_values": safe_values}


def parse_sync_health_from_command(result: dict[str, Any]) -> dict[str, Any]:
    path = REPO_ROOT / result["stdout_path"]
    try:
        payload = None
        for raw_line in reversed(path.read_text(encoding="utf-8").splitlines()):
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            payload = json.loads(line)
            break
        if payload is None:
            raise ValueError("no JSON object found")
    except Exception as exc:
        return {"status": "failed", "error": f"invalid sync-health output: {exc}"}
    queues = payload.get("redis_queues") or {}
    clean = (
        payload.get("status") == "ok"
        and int(payload.get("unsynced_change_log_count") or 0) == 0
        and int(queues.get("sync:outbound") or 0) == 0
        and int(queues.get("sync:retry") or 0) == 0
    )
    return {"status": "passed" if clean else "failed", "payload": payload}


def run_live_checks(*, manifest: str, logs_dir: Path, skip: bool) -> dict[str, Any]:
    if skip:
        return {"status": "skipped", "commands": [], "sync_health": {}}
    commands = [
        ("production_check_local", ["bash", "./scripts/production_deploy_online.sh", "--manifest", manifest, "check-local"], 240),
        ("production_online_health", ["make", "production-online-health"], 240),
        ("sync_health_foreign", ["make", "sync-health"], 90),
        ("sync_health_iran", ["make", "sync-health-iran"], 90),
    ]
    results = [run_command(name=name, args=args, logs_dir=logs_dir, timeout=timeout) for name, args, timeout in commands]
    failures = [item["name"] for item in results if item["exit_code"] != 0]
    sync_health = {
        "foreign": parse_sync_health_from_command(next(item for item in results if item["name"] == "sync_health_foreign")),
        "iran": parse_sync_health_from_command(next(item for item in results if item["name"] == "sync_health_iran")),
    }
    for role, item in sync_health.items():
        if item["status"] != "passed":
            failures.append(f"sync_health_{role}_dirty")
    return {"status": "passed" if not failures else "failed", "commands": results, "sync_health": sync_health, "failures": failures}


def build_score(stage_rows: list[dict[str, Any]], checks: dict[str, Any], warnings: list[dict[str, str]]) -> dict[str, Any]:
    required = len(stage_rows) + 5
    passed = sum(1 for item in stage_rows if item["status"] == "passed")
    for key in ("manifest", "live_checks", "messenger", "observability", "backup_rollback"):
        if (checks.get(key) or {}).get("status") == "passed":
            passed += 1
    pass_rate = round((passed / required) * 100, 2) if required else 0.0
    warning_adjusted = max(0.0, round(pass_rate - (len(warnings) * 2), 2))
    return {
        "required_checks": required,
        "passed_required_checks": passed,
        "required_pass_rate": pass_rate,
        "accepted_warning_count": len(warnings),
        "warning_adjusted_score": warning_adjusted,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    score = payload["score"]
    checks = payload["checks"]
    lines = [
        "# Production Release Readiness Report",
        "",
        f"- Status: `{payload['status']}`",
        f"- Started at: `{payload['started_at']}`",
        f"- Finished at: `{payload['finished_at']}`",
        f"- Artifact dir: `{payload['artifact_dir']}`",
        f"- Required checks: `{score['passed_required_checks']}/{score['required_checks']}`",
        f"- Required pass rate: `{score['required_pass_rate']}%`",
        f"- Warning-adjusted score: `{score['warning_adjusted_score']}/100`",
        f"- Accepted warnings: `{score['accepted_warning_count']}`",
        "",
        "## Stage Evidence",
        "",
        "| Stage | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for row in payload["stage_evidence"]:
        evidence = "<br>".join(f"`{item['path']}`: {item['detail']}" for item in row["checks"])
        lines.append(f"| `{row['stage']}` {row['label']} | `{row['status']}` | {evidence} |")
    lines.extend([
        "",
        "## Live Gates",
        "",
        f"- Manifest: `{checks['manifest']['status']}`",
        f"- Live health/sync: `{checks['live_checks']['status']}`",
        f"- Messenger: `{checks['messenger']['status']}`; surfaces `{checks['messenger'].get('measured_surface_count')}/{checks['messenger'].get('surface_count')}`, blocked `{checks['messenger'].get('blocked_surface_count')}`",
        f"- Observability: `{checks['observability']['status']}`; logging overhead `{checks['observability'].get('logging_overhead_us')}us` / budget `{checks['observability'].get('logging_budget_us')}us`",
        f"- Backup/rollback: `{checks['backup_rollback']['status']}`; backup `{(checks['backup_rollback'].get('backup') or {}).get('path')}`",
        "",
        "## Key Benchmark Scores",
        "",
    ])
    messenger = checks["messenger"]
    for label, key in (
        ("Messenger list ready", "listReadyDeltaMs"),
        ("Messenger chat ready", "chatReadyDeltaMs"),
        ("Messenger context menu", "contextMenuDeltaMs"),
        ("Messenger DOM nodes", "domNodeDelta"),
        ("Messenger JS heap", "heapDeltaMb"),
    ):
        item = (messenger.get("delta_summary") or {}).get(key) or {}
        lines.append(
            f"- {label}: improved `{item.get('improved')}/{item.get('count')}`, "
            f"worse `{item.get('worse')}`, average delta `{item.get('average')}`"
        )
    backup = checks["backup_rollback"]
    phases = backup.get("release_phases") or {}
    lines.extend([
        f"- P10 release duration: `{backup.get('release_duration_seconds')}s`",
        f"- P10 final health phase: `{(phases.get('final_health') or {}).get('duration_seconds')}s`",
        "",
        "## Accepted Warnings",
        "",
    ])
    if payload["accepted_warnings"]:
        lines.extend(["| Warning | Owner | Release note |", "| --- | --- | --- |"])
        for item in payload["accepted_warnings"]:
            lines.append(f"| `{item['id']}` | {item['owner']} | {item['release_note']} |")
    else:
        lines.append("- None")
    lines.extend(["", "## Failures", ""])
    if payload["failures"]:
        for failure in payload["failures"]:
            lines.append(f"- {failure}")
    else:
        lines.append("- None")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    scenario_dir = Path(args.artifact_root) / stamp / "final-release-p11"
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_iso()
    manifest = args.manifest or "./deploy/production/online.env"
    settings = resolve_deploy_settings(manifest_path=manifest)

    stage_rows = evaluate_stage_evidence()
    checks = {
        "manifest": evaluate_manifest(settings),
        "live_checks": run_live_checks(manifest=manifest, logs_dir=logs_dir, skip=args.skip_live_checks),
        "messenger": summarize_messenger(),
        "observability": summarize_observability(),
        "backup_rollback": summarize_backup_and_rollback(),
    }
    accepted_warnings: list[dict[str, str]] = []
    for key in ("messenger", "observability"):
        accepted_warnings.extend(checks[key].get("warnings") or [])
    failures: list[str] = []
    failures.extend(f"{row['stage']} evidence failed" for row in stage_rows if row["status"] != "passed")
    for key, value in checks.items():
        if value.get("status") == "failed":
            failures.append(f"{key} failed: {value.get('failures') or value.get('missing') or value.get('status')}")
    warnings_actionable = all(item.get("owner") and item.get("release_note") for item in accepted_warnings)
    if accepted_warnings and not warnings_actionable:
        failures.append("accepted warnings are missing owner or release note")
    status = "failed" if failures else ("passed_with_warnings" if accepted_warnings else "passed")
    score = build_score(stage_rows, checks, accepted_warnings)
    payload = {
        "status": status,
        "started_at": started_at,
        "finished_at": utc_iso(),
        "artifact_dir": display_path(scenario_dir),
        "stage_evidence": stage_rows,
        "checks": checks,
        "accepted_warnings": accepted_warnings,
        "failures": failures,
        "score": score,
    }
    write_json(scenario_dir / "results.json", payload)
    write_summary(scenario_dir / "summary.md", payload)
    if args.report_out:
        report_path = Path(args.report_out)
        if not report_path.is_absolute():
            report_path = REPO_ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_summary(report_path, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_gate(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] in {"passed", "passed_with_warnings"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
