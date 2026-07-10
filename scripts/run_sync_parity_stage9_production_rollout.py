#!/usr/bin/env python3
"""Plan and guard the Stage 9 production rollout for sync parity.

The default mode only writes a plan. Production reads require an explicit
read-only confirmation variable. Backup creation and production release are
separate guarded modes so a preflight cannot accidentally mutate production.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.deploy_config import resolve_deploy_settings
from core.sync_transport import sync_transport_security_status_from_values
from core.sync_parity_observability import strict_alert_gate_from_parity_summary, summarize_parity_comparison


SCHEMA_VERSION = "sync_parity_stage9_production_rollout_v1"
PLANNING_BRANCHES = {"candidate/sync-parity-hardening", "main"}
RELEASE_BRANCH = "main"
PREFLIGHT_CONFIRM_ENV = "SYNC_PARITY_STAGE9_PRODUCTION_PREFLIGHT_CONFIRM"
PREFLIGHT_CONFIRM_VALUE = "run-production-readonly-preflight"
BACKUP_CONFIRM_ENV = "SYNC_PARITY_STAGE9_PRODUCTION_BACKUP_CONFIRM"
BACKUP_CONFIRM_VALUE = "create-production-backups"
RELEASE_CONFIRM_ENV = "SYNC_PARITY_STAGE9_PRODUCTION_RELEASE_CONFIRM"
RELEASE_CONFIRM_VALUE = "execute-production-rollout"
STRICT_ALERT_CONFIRM_ENV = "SYNC_PARITY_STAGE9_STRICT_ALERT_CONFIRM"
STRICT_ALERT_CONFIRM_VALUE = "enable-strict-parity-alerts"
SSH_STRICT_HOST_KEY_CHECKING = "accept-new"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    args: list[str]
    phase: str
    description: str
    reads_production: bool = False
    mutates_production: bool = False
    required: bool = True
    timeout_seconds: int = 300
    stdout_path: Path | None = None
    stderr_path: Path | None = None


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_git_value(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_payload(command: CommandSpec) -> dict[str, Any]:
    return {
        "name": command.name,
        "args": command.args,
        "phase": command.phase,
        "description": command.description,
        "reads_production": command.reads_production,
        "mutates_production": command.mutates_production,
        "required": command.required,
        "timeout_seconds": command.timeout_seconds,
        "stdout_path": str(command.stdout_path) if command.stdout_path else None,
        "stderr_path": str(command.stderr_path) if command.stderr_path else None,
    }


def ssh_args(settings: dict[str, str], command: str) -> list[str]:
    return [
        "ssh",
        "-o",
        f"StrictHostKeyChecking={SSH_STRICT_HOST_KEY_CHECKING}",
        "-p",
        settings.get("IRAN_SSH_PORT", "37067"),
        f"{settings.get('IRAN_SSH_USER', 'root')}@{settings['IRAN_HOST']}",
        command,
    ]


def compose_probe_script(compose_file: str, body: str) -> str:
    return (
        "if docker compose version >/dev/null 2>&1; then compose_cmd='docker compose'; "
        "elif command -v docker-compose >/dev/null 2>&1; then compose_cmd='docker-compose'; "
        "else echo 'No Docker Compose command is available.' >&2; exit 125; fi; "
        f"$compose_cmd -f {shlex.quote(compose_file)} {body}"
    )


def foreign_compose_exec(*args: str) -> list[str]:
    body = "exec -T app " + " ".join(shlex.quote(arg) for arg in args)
    return ["bash", "-lc", compose_probe_script("docker-compose.yml", body)]


def iran_compose_exec(settings: dict[str, str], *args: str) -> list[str]:
    body = "exec -T app " + " ".join(shlex.quote(arg) for arg in args)
    command = (
        f"cd {shlex.quote(settings['IRAN_PROJECT_DIR'])} && "
        + compose_probe_script("docker-compose.iran.yml", body)
    )
    return ssh_args(settings, command)


def parity_snapshot_command(
    *,
    role: str,
    settings: dict[str, str],
    mode: str,
    max_rows: int,
) -> list[str]:
    args = [
        "python",
        "scripts/compare_sync_parity.py",
        "snapshot",
        "--mode",
        mode,
        "--max-rows-per-table",
        str(max_rows),
    ]
    if role == "foreign":
        return foreign_compose_exec(*args)
    if role == "iran":
        return iran_compose_exec(settings, *args)
    raise ValueError(f"unsupported role: {role}")


def build_local_release_gates(args: argparse.Namespace) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="local_sync_guarantee_matrix",
            args=[sys.executable, "-m", "unittest", "tests.test_sync_guarantee_matrix"],
            phase="local_release_gate",
            description="Synced-table receiver, payload, parity, repair, and rule coverage must remain complete.",
            timeout_seconds=900,
            stdout_path=artifact_dir / "local-sync-guarantee-matrix.stdout.log",
            stderr_path=artifact_dir / "local-sync-guarantee-matrix.stderr.log",
        ),
        CommandSpec(
            name="local_stage8_rollout_contract",
            args=[sys.executable, "-m", "unittest", "tests.test_sync_parity_stage8_staging_rollout"],
            phase="local_release_gate",
            description="The staging rollout gate must remain fail-closed before production rollout planning.",
            timeout_seconds=300,
            stdout_path=artifact_dir / "local-stage8-rollout-contract.stdout.log",
            stderr_path=artifact_dir / "local-stage8-rollout-contract.stderr.log",
        ),
        CommandSpec(
            name="local_stage9_rollout_contract",
            args=[sys.executable, "-m", "unittest", "tests.test_sync_parity_stage9_production_rollout"],
            phase="local_release_gate",
            description="The production rollout gate itself must remain fail-closed.",
            timeout_seconds=300,
            stdout_path=artifact_dir / "local-stage9-rollout-contract.stdout.log",
            stderr_path=artifact_dir / "local-stage9-rollout-contract.stderr.log",
        ),
    ]


def build_read_only_preflight(args: argparse.Namespace, settings: dict[str, str]) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="production_online_check",
            args=["make", "production-online-check"],
            phase="production_read_only_preflight",
            description="Validate production manifest, local release prerequisites, and SSH reachability.",
            reads_production=True,
            timeout_seconds=300,
            stdout_path=artifact_dir / "production-online-check.stdout.log",
            stderr_path=artifact_dir / "production-online-check.stderr.log",
        ),
        CommandSpec(
            name="production_online_health",
            args=["make", "production-online-health"],
            phase="production_read_only_preflight",
            description="Read-only healthcheck for the current foreign/Iran production deployment.",
            reads_production=True,
            timeout_seconds=300,
            stdout_path=artifact_dir / "production-online-health.stdout.log",
            stderr_path=artifact_dir / "production-online-health.stderr.log",
        ),
        CommandSpec(
            name="sync_health_foreign",
            args=["make", "sync-health"],
            phase="production_read_only_preflight",
            description="Foreign sync backlog and lag must be clean before production rollout.",
            reads_production=True,
            timeout_seconds=120,
            stdout_path=artifact_dir / "sync-health-foreign.stdout.log",
            stderr_path=artifact_dir / "sync-health-foreign.stderr.log",
        ),
        CommandSpec(
            name="sync_health_iran",
            args=["make", "sync-health-iran"],
            phase="production_read_only_preflight",
            description="Iran sync backlog and lag must be clean before production rollout.",
            reads_production=True,
            timeout_seconds=120,
            stdout_path=artifact_dir / "sync-health-iran.stdout.log",
            stderr_path=artifact_dir / "sync-health-iran.stderr.log",
        ),
        CommandSpec(
            name="production_alerts_warning_only",
            args=[
                sys.executable,
                "scripts/report_production_alerts.py",
                "--manifest",
                args.manifest,
                "--timestamp",
                args.stamp,
                "--json",
                "--fail-on",
                "never",
                "--report-out",
                str(artifact_dir / "production-alerts-report.md"),
            ],
            phase="production_read_only_preflight",
            description="Collect DB/Redis/sync/disk/backup alerts without failing on warnings during the warning-only window.",
            reads_production=True,
            timeout_seconds=360,
            stdout_path=artifact_dir / "production-alerts-warning-only.stdout.json",
            stderr_path=artifact_dir / "production-alerts-warning-only.stderr.log",
        ),
        CommandSpec(
            name="production_baseline_snapshot",
            args=[
                sys.executable,
                "scripts/capture_production_baseline.py",
                "--manifest",
                args.manifest,
                "--artifact-root",
                str(artifact_dir / "baseline"),
                "--timestamp",
                args.stamp,
                "--allow-dirty-sync",
            ],
            phase="production_read_only_preflight",
            description="Capture a redacted read-only baseline for rollback and post-deploy comparison.",
            reads_production=True,
            timeout_seconds=600,
            stdout_path=artifact_dir / "production-baseline.stdout.log",
            stderr_path=artifact_dir / "production-baseline.stderr.log",
        ),
        CommandSpec(
            name="foreign_parity_snapshot_quick",
            args=parity_snapshot_command(role="foreign", settings=settings, mode="quick", max_rows=args.quick_max_rows),
            phase="production_read_only_preflight",
            description="Capture bounded foreign quick parity snapshot before deploy.",
            reads_production=True,
            timeout_seconds=300,
            stdout_path=artifact_dir / "foreign-parity-quick.json",
            stderr_path=artifact_dir / "foreign-parity-quick.stderr.log",
        ),
        CommandSpec(
            name="iran_parity_snapshot_quick",
            args=parity_snapshot_command(role="iran", settings=settings, mode="quick", max_rows=args.quick_max_rows),
            phase="production_read_only_preflight",
            description="Capture bounded Iran quick parity snapshot before deploy.",
            reads_production=True,
            timeout_seconds=300,
            stdout_path=artifact_dir / "iran-parity-quick.json",
            stderr_path=artifact_dir / "iran-parity-quick.stderr.log",
        ),
        CommandSpec(
            name="foreign_parity_snapshot_deep",
            args=parity_snapshot_command(role="foreign", settings=settings, mode="deep", max_rows=args.deep_max_rows),
            phase="production_read_only_preflight",
            description="Capture foreign deep parity snapshot before deploy.",
            reads_production=True,
            timeout_seconds=600,
            stdout_path=artifact_dir / "foreign-parity-deep.json",
            stderr_path=artifact_dir / "foreign-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="iran_parity_snapshot_deep",
            args=parity_snapshot_command(role="iran", settings=settings, mode="deep", max_rows=args.deep_max_rows),
            phase="production_read_only_preflight",
            description="Capture Iran deep parity snapshot before deploy.",
            reads_production=True,
            timeout_seconds=600,
            stdout_path=artifact_dir / "iran-parity-deep.json",
            stderr_path=artifact_dir / "iran-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="production_predeploy_parity_compare_quick",
            args=[
                sys.executable,
                "scripts/compare_sync_parity.py",
                "compare",
                "--local-snapshot",
                str(artifact_dir / "foreign-parity-quick.json"),
                "--peer-snapshot",
                str(artifact_dir / "iran-parity-quick.json"),
                "--sample-limit",
                "10",
            ],
            phase="production_read_only_preflight",
            description="Compare quick parity snapshots and classify accepted local-only differences.",
            reads_production=True,
            timeout_seconds=120,
            stdout_path=artifact_dir / "production-predeploy-parity-quick.json",
            stderr_path=artifact_dir / "production-predeploy-parity-quick.stderr.log",
        ),
        CommandSpec(
            name="production_predeploy_parity_compare_deep",
            args=[
                sys.executable,
                "scripts/compare_sync_parity.py",
                "compare",
                "--local-snapshot",
                str(artifact_dir / "foreign-parity-deep.json"),
                "--peer-snapshot",
                str(artifact_dir / "iran-parity-deep.json"),
                "--sample-limit",
                "10",
            ],
            phase="production_read_only_preflight",
            description="Compare deep parity snapshots before deploy; business drift must be documented or fixed.",
            reads_production=True,
            timeout_seconds=180,
            stdout_path=artifact_dir / "production-predeploy-parity-deep.json",
            stderr_path=artifact_dir / "production-predeploy-parity-deep.stderr.log",
        ),
    ]


def build_backup_commands(args: argparse.Namespace) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="production_backup_both_with_restore_smoke",
            args=[
                sys.executable,
                "scripts/run_production_backup.py",
                "--manifest",
                args.manifest,
                "--role",
                "both",
                "--timestamp",
                args.stamp,
                "--json",
                "--restore-smoke",
            ],
            phase="production_backup",
            description="Create DB/Redis/uploads/audit backups on both servers and restore-smoke the DB dumps.",
            reads_production=True,
            mutates_production=True,
            timeout_seconds=3600,
            stdout_path=artifact_dir / "production-backup-both.stdout.json",
            stderr_path=artifact_dir / "production-backup-both.stderr.log",
        )
    ]


def build_release_commands(args: argparse.Namespace) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="production_release",
            args=["make", "production-release"],
            phase="production_release",
            description="Run the full foreign-controlled production release flow after merge to main and fresh backups.",
            reads_production=True,
            mutates_production=True,
            timeout_seconds=3600,
            stdout_path=artifact_dir / "production-release.stdout.log",
            stderr_path=artifact_dir / "production-release.stderr.log",
        )
    ]


def build_post_deploy_checks(args: argparse.Namespace, settings: dict[str, str]) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="postdeploy_production_online_health",
            args=["make", "production-online-health"],
            phase="post_deploy_read_only",
            description="Healthcheck after production rollout.",
            reads_production=True,
            timeout_seconds=300,
            stdout_path=artifact_dir / "postdeploy-production-online-health.stdout.log",
            stderr_path=artifact_dir / "postdeploy-production-online-health.stderr.log",
        ),
        CommandSpec(
            name="postdeploy_sync_health_foreign",
            args=["make", "sync-health"],
            phase="post_deploy_read_only",
            description="Foreign sync backlog and lag after rollout.",
            reads_production=True,
            timeout_seconds=120,
            stdout_path=artifact_dir / "postdeploy-sync-health-foreign.stdout.log",
            stderr_path=artifact_dir / "postdeploy-sync-health-foreign.stderr.log",
        ),
        CommandSpec(
            name="postdeploy_sync_health_iran",
            args=["make", "sync-health-iran"],
            phase="post_deploy_read_only",
            description="Iran sync backlog and lag after rollout.",
            reads_production=True,
            timeout_seconds=120,
            stdout_path=artifact_dir / "postdeploy-sync-health-iran.stdout.log",
            stderr_path=artifact_dir / "postdeploy-sync-health-iran.stderr.log",
        ),
        CommandSpec(
            name="postdeploy_foreign_parity_snapshot_deep",
            args=parity_snapshot_command(role="foreign", settings=settings, mode="deep", max_rows=args.deep_max_rows),
            phase="post_deploy_read_only",
            description="Foreign deep parity snapshot after rollout.",
            reads_production=True,
            timeout_seconds=600,
            stdout_path=artifact_dir / "postdeploy-foreign-parity-deep.json",
            stderr_path=artifact_dir / "postdeploy-foreign-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="postdeploy_iran_parity_snapshot_deep",
            args=parity_snapshot_command(role="iran", settings=settings, mode="deep", max_rows=args.deep_max_rows),
            phase="post_deploy_read_only",
            description="Iran deep parity snapshot after rollout.",
            reads_production=True,
            timeout_seconds=600,
            stdout_path=artifact_dir / "postdeploy-iran-parity-deep.json",
            stderr_path=artifact_dir / "postdeploy-iran-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="postdeploy_parity_compare_deep",
            args=[
                sys.executable,
                "scripts/compare_sync_parity.py",
                "compare",
                "--local-snapshot",
                str(artifact_dir / "postdeploy-foreign-parity-deep.json"),
                "--peer-snapshot",
                str(artifact_dir / "postdeploy-iran-parity-deep.json"),
                "--sample-limit",
                "10",
            ],
            phase="post_deploy_read_only",
            description="Post-deploy deep parity comparison.",
            reads_production=True,
            timeout_seconds=180,
            stdout_path=artifact_dir / "postdeploy-parity-deep.json",
            stderr_path=artifact_dir / "postdeploy-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="postdeploy_production_alerts_warning_only",
            args=[
                sys.executable,
                "scripts/report_production_alerts.py",
                "--manifest",
                args.manifest,
                "--timestamp",
                args.stamp,
                "--json",
                "--fail-on",
                "never",
                "--report-out",
                str(artifact_dir / "postdeploy-production-alerts-report.md"),
            ],
            phase="post_deploy_read_only",
            description="Keep parity/ops alerts warning-only immediately after rollout.",
            reads_production=True,
            timeout_seconds=360,
            stdout_path=artifact_dir / "postdeploy-production-alerts-warning-only.stdout.json",
            stderr_path=artifact_dir / "postdeploy-production-alerts-warning-only.stderr.log",
        ),
    ]


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_latest_parity_evidence(args: argparse.Namespace) -> dict[str, Any] | None:
    candidates: list[Path] = []
    if args.latest_parity_status:
        candidates.append(Path(args.latest_parity_status))
    candidates.extend(
        [
            args.artifact_dir / "postdeploy-parity-deep.json",
            args.artifact_dir / "production-predeploy-parity-deep.json",
            args.artifact_dir / "production-predeploy-parity-quick.json",
        ]
    )
    for candidate in candidates:
        payload = _read_json_file(candidate)
        if payload is None:
            continue
        if "summary" in payload and isinstance(payload["summary"], dict):
            return dict(payload["summary"])
        return summarize_parity_comparison(payload, mode=str(payload.get("mode") or "unknown"))
    return None


def build_strict_alert_plan(args: argparse.Namespace) -> dict[str, Any]:
    latest_parity = load_latest_parity_evidence(args)
    return {
        "status": "reserved_for_after_warning_window",
        "confirm_env": STRICT_ALERT_CONFIRM_ENV,
        "confirm_value": STRICT_ALERT_CONFIRM_VALUE,
        "minimum_warning_window_hours": args.warning_window_hours,
        "latest_parity_evidence": latest_parity,
        "artifact_metadata_required": bool(args.require_artifact_backed_parity),
        "activation_gate": strict_alert_gate_from_parity_summary(
            latest_parity,
            require_artifact_metadata=bool(args.require_artifact_backed_parity),
        ),
        "required_evidence": [
            "postdeploy delivery health clean on both servers",
            "postdeploy parity comparison clean or only accepted documented exemptions",
            "artifact metadata complete when artifact-backed parity is required",
            "no unexpected retry backlog",
            "operator-reviewed logs for receiver rejection and stale ignored events",
            "repair tool dry-run available and tested",
        ],
        "fail_closed_scope": [
            "critical missing-row business drift",
            "business-hash mismatch on synced tables",
            "unknown synced table or policy-forbidden receiver item",
        ],
        "warning_only_scope": [
            "known local-only Telegram/WebApp runtime fields",
            "documented legacy inactive offer public-id exemptions",
            "volatile timestamps and counters excluded by parity policy",
        ],
    }


def build_transport_security_gate(settings: dict[str, str]) -> dict[str, Any]:
    status = sync_transport_security_status_from_values(
        environment="production",
        sync_verify_tls=os.environ.get("SYNC_VERIFY_TLS", settings.get("SYNC_VERIFY_TLS", "true")),
        sync_ca_bundle=os.environ.get("SYNC_CA_BUNDLE", settings.get("SYNC_CA_BUNDLE", "")),
    )
    return {
        "status": "passed" if status["production_allowed"] else "blocked_insecure_sync_transport",
        "sync_verify_tls": status["verify_setting"],
        "sync_ca_bundle_configured": status["ca_bundle_configured"],
        "reason": status["reason"],
        "production_allowed": status["production_allowed"],
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    current_branch = run_git_value(["branch", "--show-current"])
    local_gates = build_local_release_gates(args)
    preflight = build_read_only_preflight(args, settings)
    backups = build_backup_commands(args)
    release = build_release_commands(args)
    post_deploy = build_post_deploy_checks(args, settings)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "planned",
        "environment": "production",
        "mode": args.mode,
        "branch": current_branch,
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "artifact_dir": str(args.artifact_dir),
        "manifest": args.manifest,
        "branch_gate": {
            "planning_allowed_branches": sorted(PLANNING_BRANCHES),
            "release_branch": RELEASE_BRANCH,
            "actual_branch": current_branch,
            "planning_passed": current_branch in PLANNING_BRANCHES,
            "release_passed": current_branch == RELEASE_BRANCH,
        },
        "execution_contract": {
            "default_mutates_production": False,
            "read_only_preflight_confirm_env": PREFLIGHT_CONFIRM_ENV,
            "read_only_preflight_confirm_value": PREFLIGHT_CONFIRM_VALUE,
            "backup_confirm_env": BACKUP_CONFIRM_ENV,
            "backup_confirm_value": BACKUP_CONFIRM_VALUE,
            "release_confirm_env": RELEASE_CONFIRM_ENV,
            "release_confirm_value": RELEASE_CONFIRM_VALUE,
            "release_requires_branch": RELEASE_BRANCH,
            "ssh_strict_host_key_checking": SSH_STRICT_HOST_KEY_CHECKING,
            "repair_policy": "production repair remains manual and dry-run-first; this rollout does not auto-repair drift",
        },
        "transport_security_gate": build_transport_security_gate(settings),
        "local_release_gates": {"status": "planned", "commands": [command_payload(command) for command in local_gates]},
        "read_only_preflight": {"status": "blocked_until_explicit_confirm", "commands": [command_payload(command) for command in preflight]},
        "backup_confirmation": {"status": "blocked_until_explicit_confirm", "commands": [command_payload(command) for command in backups]},
        "release_plan": {"status": "blocked_until_main_and_explicit_confirm", "commands": [command_payload(command) for command in release]},
        "post_deploy_checks": {"status": "planned_after_release", "commands": [command_payload(command) for command in post_deploy]},
        "warning_only_alert_window": {
            "status": "required_after_release",
            "duration_hours": args.warning_window_hours,
            "alert_fail_on": "never",
            "operator_review": [
                "receiver rejections",
                "stale ignored events",
                "retry backlog",
                "parity business drift",
                "known local-only parity differences",
            ],
        },
        "strict_alert_enablement_plan": build_strict_alert_plan(args),
        "exit_criteria": [
            "fresh production backups exist on both servers with DB restore-smoke evidence",
            "predeploy health, sync health, and parity reports are clean or documented",
            "release runs from main only",
            "postdeploy health, sync health, and parity reports are clean",
            "warning-only window completes before strict critical-drift alerting is enabled",
        ],
    }


def run_command(command: dict[str, Any]) -> dict[str, Any]:
    stdout_path = Path(command["stdout_path"]) if command.get("stdout_path") else None
    stderr_path = Path(command["stderr_path"]) if command.get("stderr_path") else None
    if stdout_path:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if stderr_path:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command["args"]),
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=int(command["timeout_seconds"]),
            check=False,
        )
        if stdout_path:
            stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        if stderr_path:
            stderr_path.write_text(completed.stderr or "", encoding="utf-8")
        return {
            "name": command["name"],
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout_path": str(stdout_path) if stdout_path else None,
            "stderr_path": str(stderr_path) if stderr_path else None,
        }
    except subprocess.TimeoutExpired as exc:
        if stdout_path and exc.stdout:
            stdout_path.write_text(str(exc.stdout), encoding="utf-8")
        if stderr_path and exc.stderr:
            stderr_path.write_text(str(exc.stderr), encoding="utf-8")
        return {
            "name": command["name"],
            "status": "timeout",
            "returncode": 124,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout_path": str(stdout_path) if stdout_path else None,
            "stderr_path": str(stderr_path) if stderr_path else None,
        }


def execute_section(plan: dict[str, Any], section_name: str) -> tuple[dict[str, Any], list[str]]:
    section = plan[section_name]
    failed_required: list[str] = []
    results: list[dict[str, Any]] = []
    for command in section["commands"]:
        result = run_command(command)
        results.append(result)
        if command.get("required", True) and result["status"] != "passed":
            failed_required.append(command["name"])
            break
    section["results"] = results
    section["status"] = "passed" if not failed_required else "failed"
    return plan, failed_required


def execute_plan(plan: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if not plan["branch_gate"]["planning_passed"]:
        plan["status"] = "blocked_wrong_branch"
        return plan, 2

    if args.mode in {"preflight", "execute", "postdeploy"} and not plan.get("transport_security_gate", {}).get("production_allowed", False):
        plan["status"] = "blocked_insecure_sync_transport"
        return plan, 2

    failed_required: list[str] = []
    if args.mode == "local-gates":
        plan, failed_required = execute_section(plan, "local_release_gates")
    elif args.mode == "preflight":
        if os.environ.get(PREFLIGHT_CONFIRM_ENV) != PREFLIGHT_CONFIRM_VALUE:
            plan["status"] = "blocked_preflight_confirmation_missing"
            plan["read_only_preflight"]["status"] = "blocked_confirmation_missing"
            return plan, 2
        plan, failed_required = execute_section(plan, "local_release_gates")
        if not failed_required:
            plan, failed_required = execute_section(plan, "read_only_preflight")
    elif args.mode == "backup":
        if os.environ.get(BACKUP_CONFIRM_ENV) != BACKUP_CONFIRM_VALUE:
            plan["status"] = "blocked_backup_confirmation_missing"
            plan["backup_confirmation"]["status"] = "blocked_confirmation_missing"
            return plan, 2
        plan, failed_required = execute_section(plan, "backup_confirmation")
    elif args.mode == "execute":
        if not plan["branch_gate"]["release_passed"]:
            plan["status"] = "blocked_release_requires_main"
            return plan, 2
        if os.environ.get(PREFLIGHT_CONFIRM_ENV) != PREFLIGHT_CONFIRM_VALUE:
            plan["status"] = "blocked_preflight_confirmation_missing"
            plan["read_only_preflight"]["status"] = "blocked_confirmation_missing"
            return plan, 2
        if os.environ.get(BACKUP_CONFIRM_ENV) != BACKUP_CONFIRM_VALUE:
            plan["status"] = "blocked_backup_confirmation_missing"
            plan["backup_confirmation"]["status"] = "blocked_confirmation_missing"
            return plan, 2
        if os.environ.get(RELEASE_CONFIRM_ENV) != RELEASE_CONFIRM_VALUE:
            plan["status"] = "blocked_release_confirmation_missing"
            plan["release_plan"]["status"] = "blocked_confirmation_missing"
            return plan, 2
        for section_name in ("local_release_gates", "read_only_preflight", "backup_confirmation", "release_plan", "post_deploy_checks"):
            plan, failed_required = execute_section(plan, section_name)
            if failed_required:
                break
    elif args.mode == "postdeploy":
        if os.environ.get(PREFLIGHT_CONFIRM_ENV) != PREFLIGHT_CONFIRM_VALUE:
            plan["status"] = "blocked_preflight_confirmation_missing"
            plan["post_deploy_checks"]["status"] = "blocked_confirmation_missing"
            return plan, 2
        plan, failed_required = execute_section(plan, "post_deploy_checks")

    plan["status"] = "passed" if not failed_required else "failed"
    if failed_required:
        plan["failed_required_commands"] = failed_required
    return plan, 0 if not failed_required else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("plan", "local-gates", "preflight", "backup", "execute", "postdeploy"),
        default="plan",
    )
    parser.add_argument("--manifest", default="./deploy/production/online.env")
    parser.add_argument("--stamp", default=os.environ.get("STAMP") or utc_stamp())
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quick-max-rows", type=int, default=5000)
    parser.add_argument("--deep-max-rows", type=int, default=10000)
    parser.add_argument("--warning-window-hours", type=int, default=24)
    parser.add_argument("--latest-parity-status", type=Path)
    parser.add_argument(
        "--require-artifact-backed-parity",
        action="store_true",
        help="Require retained artifact metadata before the strict parity activation gate can pass.",
    )
    args = parser.parse_args(argv)
    if args.artifact_dir is None:
        args.artifact_dir = Path("tmp") / "sync-parity-stage9-production" / args.stamp
    if args.output is None:
        args.output = args.artifact_dir / "stage9-production-rollout-plan.json"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    plan = build_plan(args)
    exit_code = 0
    if args.mode != "plan":
        plan, exit_code = execute_plan(plan, args)
    write_json(args.output, plan)
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
