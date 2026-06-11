#!/usr/bin/env python3
"""Run the Stage P9 production observability, audit, and alert readiness gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import (
    DEFAULT_ARTIFACT_ROOT,
    compose_probe_script,
    display_path,
    quote_remote,
    remote_args,
    utc_iso,
    utc_stamp,
)
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_ARTIFACT_PATTERNS = (
    ("authorization_header", re.compile(r"(?i)\bauthorization\s*[:=]")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")),
    ("raw_access_token", re.compile(r"(?i)\b(access_token|refresh_token|auth_token|otp_code)\s*[:=]\s*['\"]?[^\s,'\"]{6,}")),
    ("iran_mobile", re.compile(r"\b09\d{9}\b")),
)


@dataclass
class CommandResult:
    name: str
    args: list[str]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


class ObservabilityReadinessError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run production observability readiness probes.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--overhead-iterations", type=int, default=5000)
    parser.add_argument("--overhead-budget-us", type=float, default=1000.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def parse_json_output(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        raise ObservabilityReadinessError("Command did not print JSON on stdout")
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except ValueError:
        pass
    for line in reversed([item.strip() for item in stripped.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ObservabilityReadinessError("Command did not print a JSON object on stdout")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Runner:
    def __init__(self, *, settings: dict[str, str], logs_dir: Path):
        self.settings = settings
        self.logs_dir = logs_dir
        self.results: list[dict[str, Any]] = []

    def run(self, name: str, args: list[str], *, timeout: int = 60, check: bool = True) -> CommandResult:
        started = time.perf_counter()
        completed = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        result = CommandResult(
            name=name,
            args=args,
            exit_code=completed.returncode,
            duration_seconds=round(time.perf_counter() - started, 3),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        self._write_logs(result)
        if check and result.exit_code != 0:
            raise ObservabilityReadinessError(f"{name} failed with exit code {result.exit_code}")
        return result

    def run_json(self, name: str, args: list[str], *, timeout: int = 60, check: bool = True) -> dict[str, Any]:
        result = self.run(name, args, timeout=timeout, check=check)
        payload = parse_json_output(result.stdout)
        self.results[-1]["json"] = payload
        return payload

    def _write_logs(self, result: CommandResult) -> None:
        stdout_path = self.logs_dir / f"{result.name}.stdout.log"
        stderr_path = self.logs_dir / f"{result.name}.stderr.log"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        self.results.append(
            {
                "name": result.name,
                "command": command_display(result.args),
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "stdout_path": display_path(stdout_path),
                "stderr_path": display_path(stderr_path),
            }
        )

    def compose_args(self, role: str, body: str) -> list[str]:
        if role == "foreign":
            command = f"cd {shlex.quote(str(REPO_ROOT))} && " + compose_probe_script("docker-compose.yml", body)
            return ["bash", "-lc", command]
        command = f"cd {quote_remote(self.settings['IRAN_PROJECT_DIR'])} && " + compose_probe_script("docker-compose.iran.yml", body)
        return remote_args(self.settings, command)

    def app_python_json(self, role: str, name: str, *script_args: str, timeout: int = 60) -> dict[str, Any]:
        body = "exec -T app " + " ".join(shlex.quote(part) for part in ("python", *script_args))
        return self.run_json(f"{role}_{name}", self.compose_args(role, body), timeout=timeout)

    def host_python_json(self, role: str, name: str, *script_args: str, timeout: int = 60, check: bool = True) -> dict[str, Any]:
        command = " ".join(shlex.quote(part) for part in ("python3", *script_args))
        if role == "foreign":
            return self.run_json(f"{role}_{name}", ["bash", "-lc", f"cd {shlex.quote(str(REPO_ROOT))} && {command}"], timeout=timeout, check=check)
        remote_command = f"cd {quote_remote(self.settings['IRAN_PROJECT_DIR'])} && {command}"
        return self.run_json(f"{role}_{name}", remote_args(self.settings, remote_command), timeout=timeout, check=check)


def metrics_contract_is_acceptable(payload: dict[str, Any]) -> bool:
    surfaces = {item.get("service"): item for item in payload.get("surfaces", []) if isinstance(item, dict)}
    policy = payload.get("metrics_backend_policy") or {}
    return (
        policy.get("default_backend") == "memory"
        and policy.get("authoritative_cross_service_source") == "loki_logs_until_explicit_multi_surface_scrape_is_deployed"
        and (surfaces.get("api") or {}).get("collection_mode") == "http"
        and "single_api_process" in str((surfaces.get("api") or {}).get("authoritative_scope", ""))
        and (surfaces.get("bot") or {}).get("collection_mode") == "logs_only"
        and (surfaces.get("sync_worker") or {}).get("collection_mode") == "logs_only"
    )


def sync_health_is_clean(payload: dict[str, Any]) -> bool:
    queues = payload.get("redis_queues") or {}
    return (
        payload.get("status") == "ok"
        and int(payload.get("unsynced_change_log_count") or 0) == 0
        and int(queues.get("sync:outbound") or 0) == 0
        and int(queues.get("sync:retry") or 0) == 0
    )


def scan_artifacts_for_sensitive_values(root: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"results.json", "summary.md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in SENSITIVE_ARTIFACT_PATTERNS:
            if pattern.search(text):
                findings.append({"pattern": name, "path": display_path(path)})
                break
    return {"status": "ok" if not findings else "failed", "findings": findings}


def evaluate_gates(checks: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    overhead = checks.get("logging_overhead") or {}
    if overhead.get("acceptable") is not True:
        failures.append("structured logging overhead exceeded the configured budget")
    if not metrics_contract_is_acceptable(checks.get("metrics_targets") or {}):
        failures.append("metrics target contract does not state the accepted memory-backend limitations")
    anchor = checks.get("audit_anchor_export") or {}
    if not anchor.get("event_hash") or int(anchor.get("audit_trail_records") or 0) <= 0:
        failures.append("audit anchor export did not produce a valid durable head")
    if anchor.get("audit_durable") is not True:
        failures.append("latest audit anchor head is not marked durable")
    if not (checks.get("audit_anchor_ship") or {}).get("shipped_to"):
        failures.append("audit anchor shipper did not append to the relay target")
    for role in ("iran", "foreign"):
        if not sync_health_is_clean((checks.get("sync_health") or {}).get(role) or {}):
            failures.append(f"{role} sync-health is not clean")
    timers = checks.get("timers") or {}
    for name, timer in timers.items():
        if timer.get("required") and timer.get("status") != "ok":
            failures.append(f"{name} required timer is not active")
        if timer.get("status") == "failed":
            failures.append(f"{name} optional timer check failed")
    if (checks.get("sensitive_artifact_scan") or {}).get("status") != "ok":
        failures.append("benchmark artifact sensitive-data scan found blocked patterns")
    return {"status": "failed" if failures else "passed", "failures": failures}


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    checks = payload.get("checks") or {}
    gates = payload.get("gates") or {}
    overhead = checks.get("logging_overhead") or {}
    anchor = checks.get("audit_anchor_export") or {}
    metrics = checks.get("metrics_targets") or {}
    policy = metrics.get("metrics_backend_policy") or {}
    lines = [
        "# Stage P9 Observability Readiness Benchmark",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Started at: `{payload.get('started_at')}`",
        f"- Finished at: `{payload.get('finished_at')}`",
        "",
        "## Checks",
        "",
        f"- Logging overhead: `{overhead.get('per_event_overhead_us')}us/event` / budget `{overhead.get('budget_us')}us`, acceptable `{overhead.get('acceptable')}`",
        f"- Metrics backend: `{policy.get('default_backend')}`; cross-service source: `{policy.get('authoritative_cross_service_source')}`",
        f"- Audit anchor records: `{anchor.get('audit_trail_records')}`; durable head: `{anchor.get('audit_durable')}`",
        f"- Audit anchor relay targets: `{len((checks.get('audit_anchor_ship') or {}).get('shipped_to') or [])}`",
        f"- Sensitive artifact scan findings: `{len((checks.get('sensitive_artifact_scan') or {}).get('findings') or [])}`",
        "",
        "## Timers",
        "",
        "| Timer | Required | Status |",
        "| --- | ---: | --- |",
    ]
    for name, timer in sorted((checks.get("timers") or {}).items()):
        lines.append(f"| `{name}` | `{timer.get('required')}` | `{timer.get('status')}` |")
    lines.extend(["", "## Sync Health", ""])
    for role, health in sorted((checks.get("sync_health") or {}).items()):
        queues = health.get("redis_queues") or {}
        lines.append(
            f"- `{role}`: status `{health.get('status')}`, unsynced `{health.get('unsynced_change_log_count')}`, "
            f"outbound `{queues.get('sync:outbound')}`, retry `{queues.get('sync:retry')}`"
        )
    lines.extend(["", "## Gates", ""])
    if gates.get("failures"):
        for failure in gates["failures"]:
            lines.append(f"- Failure: {failure}")
    else:
        lines.append("- Failures: `0`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    artifact_root = Path(args.artifact_root)
    run_dir = artifact_root / stamp
    scenario_dir = run_dir / "observability-p9"
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    runner = Runner(settings=settings, logs_dir=logs_dir)
    checks: dict[str, Any] = {"timers": {}, "sync_health": {}}
    started_at = utc_iso()
    status = "passed"
    error: str | None = None

    try:
        checks["logging_overhead"] = runner.app_python_json(
            "iran",
            "logging_overhead",
            "scripts/measure_logging_overhead.py",
            "--iterations",
            str(args.overhead_iterations),
            "--budget-us",
            str(args.overhead_budget_us),
            timeout=60,
        )
        checks["metrics_targets"] = runner.app_python_json(
            "iran",
            "metrics_targets",
            "scripts/render_metrics_targets.py",
            "--server-mode",
            "iran",
            "--compose-file",
            "docker-compose.iran.yml",
            timeout=45,
        )
        checks["audit_probe"] = runner.app_python_json(
            "iran",
            "audit_probe",
            "scripts/write_observability_audit_probe.py",
            "--probe-id",
            f"P9_OBSERVABILITY_{stamp}",
            timeout=45,
        )
        checks["audit_anchor_export"] = runner.app_python_json(
            "iran",
            "audit_anchor_export",
            "scripts/export_audit_anchor.py",
            "--input",
            "/app/audit_trail/audit.jsonl",
            "--output",
            "-",
            "--release-id",
            stamp,
            "--host-id",
            "iran",
            "--source-name",
            "iran",
            timeout=120,
        )
        anchor_path = scenario_dir / "audit-anchor.jsonl"
        anchor_path.write_text(json.dumps(checks["audit_anchor_export"], ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        relay_path = scenario_dir / "audit-anchor-relay.jsonl"
        checks["audit_anchor_ship"] = runner.run_json(
            "audit_anchor_ship_local_relay",
            [
                sys.executable,
                "scripts/ship_audit_anchor.py",
                "--input",
                str(anchor_path),
                "--output",
                str(relay_path),
            ],
            timeout=30,
        )
        checks["timers"]["foreign_sync_health_sampler"] = runner.host_python_json(
            "foreign",
            "sync_health_sampler_timer",
            "scripts/check_observability_timer.py",
            "--label",
            "foreign_sync_health_sampler",
            "--timer-name",
            "trading-bot-sync-health-sampler.timer",
            "--fallback-pattern",
            "sample_sync_health.py",
            timeout=30,
        )
        checks["timers"]["iran_sync_health_sampler"] = runner.host_python_json(
            "iran",
            "sync_health_sampler_timer",
            "scripts/check_observability_timer.py",
            "--label",
            "iran_sync_health_sampler",
            "--timer-name",
            "trading-bot-sync-health-sampler.timer",
            "--fallback-pattern",
            "sample_sync_health.py",
            timeout=60,
        )
        for role in ("foreign", "iran"):
            checks["timers"][f"{role}_audit_anchor_export_timer"] = runner.host_python_json(
                role,
                "audit_anchor_export_timer",
                "scripts/check_observability_timer.py",
                "--label",
                f"{role}_audit_anchor_export_timer",
                "--timer-name",
                "trading-bot-audit-anchor-export.timer",
                "--fallback-pattern",
                "export_audit_anchor.py",
                "--optional",
                timeout=60,
            )
            checks["timers"][f"{role}_audit_anchor_shipper_timer"] = runner.host_python_json(
                role,
                "audit_anchor_shipper_timer",
                "scripts/check_observability_timer.py",
                "--label",
                f"{role}_audit_anchor_shipper_timer",
                "--timer-name",
                "trading-bot-audit-anchor-shipper.timer",
                "--fallback-pattern",
                "ship_audit_anchor.py",
                "--optional",
                timeout=60,
            )
        checks["sync_health"]["iran"] = runner.app_python_json("iran", "sync_health", "scripts/sync_probe_worker.py", "health", timeout=60)
        checks["sync_health"]["foreign"] = runner.app_python_json("foreign", "sync_health", "scripts/sync_probe_worker.py", "health", timeout=60)
        checks["sensitive_artifact_scan"] = scan_artifacts_for_sensitive_values(scenario_dir)
    except Exception as exc:
        status = "failed"
        error = str(exc)

    gates = evaluate_gates(checks)
    if gates["status"] != "passed":
        status = "failed"
        error = error or "observability readiness gates failed"
    payload = {
        "status": status,
        "error": error,
        "started_at": started_at,
        "finished_at": utc_iso(),
        "artifact_dir": display_path(scenario_dir),
        "checks": checks,
        "gates": gates,
        "commands": runner.results,
    }
    write_json(scenario_dir / "results.json", payload)
    write_summary(scenario_dir / "summary.md", payload)
    if status != "passed":
        raise ObservabilityReadinessError(error or "Stage P9 observability readiness benchmark failed")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_benchmark(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
