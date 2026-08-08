#!/usr/bin/env python3
"""Plan / preflight / execute the combined market×queue×OT×estimate staging matrix.

``plan`` and ``preflight`` are non-mutating. ``execute`` is fail-closed until
``STAGING_COMBINED_MATRIX_CONFIRM=execute-staging-combined-matrix`` is set and
preflight is green.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_staging_combined_matrix_manifest as manifest_builder
from scripts import staging_combined_matrix_wave_driver as wave_driver


SCHEMA_VERSION = "staging_combined_matrix_runner_v1"
DEFAULT_EXPECTED_BRANCH = "candidate/combined-staging-overtime-coin"
DEFAULT_IRAN_BASE_URL = "https://staging.gold-trade.ir"
DEFAULT_FOREIGN_BASE_URL = "https://staging.362514.ir"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "staging-combined-matrix"
EXECUTION_CONFIRM_ENV = "STAGING_COMBINED_MATRIX_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-staging-combined-matrix"
DEFAULT_IRAN_SSH_HOST = "65.109.220.59"
DEFAULT_IRAN_SSH_PORT = "37067"
DEFAULT_IRAN_APP_CONTAINER = "trading_bot_staging_iran-app-1"
DEFAULT_FOREIGN_APP_CONTAINER = "trading_bot_staging-foreign_app-1"
DEFAULT_IRAN_WORKDIR = "/srv/trading-bot/staging-iran"
DEFAULT_SNAPSHOT_HOST = (
    "/srv/trading-bot/production-data/coin-intelligence/private-gold-live/staging/coin-rates.json"
)
DEFAULT_SNAPSHOT_CONTAINER = "/tmp/combined-matrix-coin-rates.json"
DRIVER_SCRIPTS = (
    "scripts/staging_combined_matrix_mutating_wave.py",
    "scripts/staging_combined_matrix_estimate_hooks.py",
    "scripts/staging_combined_matrix_queue_sampler.py",
    "scripts/staging_combined_matrix_wave_driver.py",
    "scripts/staging_combined_matrix_heal.py",
    "scripts/staging_combined_matrix_actor_guards.py",
    "scripts/build_staging_combined_matrix_manifest.py",
    "scripts/staging_set_trading_setting.py",
)

WAVE_PROFILES = ("burst", "realtime-30m")
CHANNEL_BASE_INTERVAL_SECONDS = 0.9
CHANNEL_IDLE_BURST_CAPACITY = 2
ACTIVE_OFFERS_PER_SYNTHETIC_OWNER = 10


def _driver_scenarios() -> list[dict[str, Any]]:
    """Load runtime-heavy two-server driver catalog only when it is needed.

    Keeping this import lazy lets ``--mode plan`` run in a neutral environment
    without database/Redis settings.
    """

    from scripts.run_staging_two_server_full_matrix import DRIVER_SCENARIOS

    return list(DRIVER_SCENARIOS)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_run_id() -> str:
    return f"CMB-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or "").strip() if completed.returncode == 0 else ""


def _run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=merged,
        timeout=timeout,
    )


def _child_secret_env(args: argparse.Namespace) -> dict[str, str]:
    """Pass child credentials without exposing them in process arguments."""

    values = {
        "STAGING_BASIC_AUTH_USER": getattr(args, "basic_auth_user", None),
        "STAGING_BASIC_AUTH_PASSWORD": getattr(args, "basic_auth_password", None),
        "STAGING_OBSERVABILITY_API_KEY": getattr(args, "observability_api_key", None),
    }
    return {name: str(value) for name, value in values.items() if value}


def _parse_json_stdout(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        return {"raw": text[-2000:]}


def iran_ssh(args: argparse.Namespace, remote: str) -> list[str]:
    host = args.iran_ssh_host
    if "@" not in host:
        host = f"root@{host}"
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-p",
        str(args.iran_ssh_port),
        host,
        remote,
    ]


def sync_driver_scripts(args: argparse.Namespace) -> dict[str, Any]:
    """Copy combined-matrix scripts into Iran workdir and foreign container."""

    host = args.iran_ssh_host
    if "@" not in host:
        host = f"root@{host}"
    remote_dir = f"{args.iran_workdir}/scripts"
    copied: list[str] = []
    for rel in DRIVER_SCRIPTS:
        local = REPO_ROOT / rel
        if not local.is_file():
            return {"ok": False, "error": f"required driver script missing: {rel}"}
        remote_path = f"{host}:{args.iran_workdir}/{rel}"
        scp = [
            "scp",
            "-P",
            str(args.iran_ssh_port),
            "-o",
            "BatchMode=yes",
            str(local),
            remote_path,
        ]
        completed = _run(scp, timeout=60)
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": f"scp failed for {rel}: {(completed.stderr or completed.stdout)[-400:]}",
            }
        # Ensure file is visible inside the running Iran app container.
        docker_cp = iran_ssh(
            args,
            f"docker cp {args.iran_workdir}/{rel} {args.iran_app_container}:/app/{rel}",
        )
        completed = _run(docker_cp, timeout=60)
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": f"iran docker cp failed for {rel}: {(completed.stderr or '')[-400:]}",
            }
        # Foreign local container.
        foreign_cp = [
            "docker",
            "cp",
            str(local),
            f"{args.foreign_app_container}:/app/{rel}",
        ]
        completed = _run(foreign_cp, timeout=60)
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": f"foreign docker cp failed for {rel}: {(completed.stderr or '')[-400:]}",
            }
        copied.append(rel)
    # Snapshot into Iran + foreign for estimate probes.
    foreign_snap_ok = False
    iran_snap_ok = False
    if Path(args.snapshot_host_path).is_file():
        completed = _run(
            [
                "docker",
                "cp",
                args.snapshot_host_path,
                f"{args.foreign_app_container}:{args.snapshot_container_path}",
            ],
            timeout=60,
        )
        foreign_snap_ok = completed.returncode == 0
        remote_snap = f"{args.iran_workdir}/tmp/combined-matrix-coin-rates.json"
        _run(iran_ssh(args, f"mkdir -p {args.iran_workdir}/tmp"), timeout=30)
        completed = _run(
            [
                "scp",
                "-P",
                str(args.iran_ssh_port),
                "-o",
                "BatchMode=yes",
                args.snapshot_host_path,
                f"{host}:{remote_snap}",
            ],
            timeout=120,
        )
        if completed.returncode == 0:
            completed = _run(
                iran_ssh(
                    args,
                    f"docker cp {remote_snap} {args.iran_app_container}:{args.snapshot_container_path}",
                ),
                timeout=60,
            )
            iran_snap_ok = completed.returncode == 0
    return {
        "ok": True,
        "copied": copied,
        "remote_scripts_dir": remote_dir,
        "snapshot_foreign": foreign_snap_ok,
        "snapshot_iran": iran_snap_ok,
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    manifest = manifest_builder.build_manifest(seed=args.seed)
    errors = manifest_builder.validate_combined_manifest(manifest)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_json(artifact_dir / "manifest.json", {k: v for k, v in manifest.items() if k != "wave_events"})
    write_json(artifact_dir / "wave-events.json", {"events": manifest["wave_events"]})

    budget = wave_driver.WaveBudget(
        valid_target=int(manifest["wave"]["valid_target"]),
        invalid_target=int(manifest["wave"]["invalid_target"]),
        scale=float(args.wave_scale),
        reduction_reason=args.wave_reduction_reason,
    )
    selected = wave_driver.scale_events(list(manifest["wave_events"]), budget)
    actions = wave_driver.replay_schedule(selected, realtime=False, speed=100.0)
    wave_report = {
        "schema_version": "staging_combined_wave_driver_v1",
        "mode": "plan-replay",
        "run_prefix": args.run_prefix,
        "schedule_sha256": manifest["wave"]["schedule_sha256"],
        "summary": wave_driver.summarise(actions, budget),
        "actions_sample": actions[:50],
        "action_count": len(actions),
    }
    write_json(artifact_dir / "wave-plan.json", wave_report)

    status = "plan_ready" if not errors else "plan_invalid"
    summary = {
        "schema_version": SCHEMA_VERSION,
        "mode": "plan",
        "status": status,
        "run_id": args.run_id,
        "checked_at_utc": utc_now(),
        "expected_branch": args.expected_branch,
        "expected_release_sha": args.expected_release_sha or run_git(["rev-parse", "HEAD"]),
        "manifest_errors": errors,
        "mandatory_cells": manifest["summary"],
        "wave": {
            "profile": args.wave_profile,
            "schedule_sha256": manifest["wave"]["schedule_sha256"],
            "full_event_count": manifest["wave"]["event_count"],
            "scaled_action_count": len(actions),
            "scale": budget.scale,
            "reduction_reason": budget.reduction_reason,
            "valid_limit": budget.valid_limit,
            "invalid_limit": budget.invalid_limit,
        },
        "confirm_env": EXECUTION_CONFIRM_ENV,
        "confirm_value": EXECUTION_CONFIRM_VALUE,
        "artifact_dir": str(artifact_dir),
    }
    write_json(artifact_dir / "summary.json", summary)
    (artifact_dir / "README.md").write_text(
        "\n".join(
            [
                f"# Combined staging matrix ({args.run_id})",
                "",
                f"- status: `{status}`",
                f"- scale: `{budget.scale}`",
                f"- schedule: `{manifest['wave']['schedule_sha256'][:16]}…`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {"summary": summary, "manifest": manifest, "wave_report": wave_report}


def run_child_preflight(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    """Reuse OT + S2FM preflights as topology evidence."""

    results: dict[str, Any] = {}
    ot_dir = artifact_dir / "child-ot-preflight"
    s2fm_dir = artifact_dir / "child-s2fm-preflight"
    ot_argv = [
        sys.executable,
        "scripts/run_staging_offer_overtime_acceptance.py",
        "--mode",
        "preflight",
        "--run-id",
        f"{args.run_id}-OT-PRE",
        "--artifact-dir",
        str(ot_dir),
        "--expected-branch",
        args.expected_branch,
        "--expected-release-sha",
        args.expected_release_sha or run_git(["rev-parse", "HEAD"]),
        "--iran-base-url",
        args.iran_base_url,
        "--foreign-base-url",
        args.foreign_base_url,
    ]
    child_secret_env = _child_secret_env(args)
    ot = _run(ot_argv, env=child_secret_env, timeout=180)
    results["overtime"] = {
        "returncode": ot.returncode,
        "summary": _parse_json_stdout(ot.stdout),
        "stderr_tail": (ot.stderr or "")[-500:],
        "artifact_dir": str(ot_dir),
    }

    s2fm_argv = [
        sys.executable,
        "scripts/run_staging_two_server_full_matrix.py",
        "--mode",
        "preflight",
        "--run-id",
        f"{args.run_id}-S2FM-PRE",
        "--artifact-dir",
        str(s2fm_dir),
        "--expected-branch",
        args.expected_branch,
        "--expected-release-sha",
        args.expected_release_sha or run_git(["rev-parse", "HEAD"]),
        "--iran-base-url",
        args.iran_base_url,
        "--foreign-base-url",
        args.foreign_base_url,
        "--iran-ssh-host",
        args.iran_ssh_host,
        "--iran-ssh-port",
        str(args.iran_ssh_port),
        "--iran-workdir",
        args.iran_workdir,
        "--iran-app-container",
        args.iran_app_container,
        "--foreign-app-container",
        args.foreign_app_container,
        "--parity-mode",
        args.parity_mode,
    ]
    s2fm = _run(s2fm_argv, env=child_secret_env, timeout=600)
    results["two_server_matrix"] = {
        "returncode": s2fm.returncode,
        "summary": _parse_json_stdout(s2fm.stdout),
        "stderr_tail": (s2fm.stderr or "")[-500:],
        "artifact_dir": str(s2fm_dir),
    }

    coin_report = artifact_dir / "coin-gate-report.json"
    coin = _run(
        [
            sys.executable,
            "scripts/run_staging_coin_intelligence_gate.py",
            "--report",
            str(coin_report),
        ],
        timeout=300,
    )
    results["coin_gate"] = {
        "returncode": coin.returncode,
        "report": str(coin_report),
        "stdout_tail": (coin.stdout or "")[-500:],
        "stderr_tail": (coin.stderr or "")[-500:],
        # Gate may be non-zero when historical publish is skipped; readiness file is enough for matrix.
        "soft": True,
    }
    readiness = REPO_ROOT / "tmp" / "matrix-ready" / "estimator-readiness.json"
    results["estimator_readiness"] = {
        "path": str(readiness),
        "present": readiness.is_file(),
        "payload": json.loads(readiness.read_text(encoding="utf-8")) if readiness.is_file() else {},
    }
    return results


def run_preflight(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    plan = build_plan(args)
    artifact_dir = Path(args.artifact_dir)
    checks: list[dict[str, Any]] = []

    branch = run_git(["branch", "--show-current"])
    head = run_git(["rev-parse", "HEAD"])
    expected = args.expected_release_sha or head
    checks.append(
        {
            "name": "git_branch",
            "status": "passed" if branch == args.expected_branch else "failed",
            "detail": {"branch": branch, "expected": args.expected_branch},
        }
    )
    checks.append(
        {
            "name": "release_sha",
            "status": "passed" if expected == head else "failed",
            "detail": {"expected": expected, "head": head},
        }
    )
    execution_owner = str(
        os.getenv("TELEGRAM_DELIVERY_EXECUTION_OWNER", "legacy")
    ).strip().lower()
    producer_owner = str(
        os.getenv("TELEGRAM_DELIVERY_PRODUCER_MODE", execution_owner)
    ).strip().lower()
    expected_owner = str(
        os.getenv("TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER", execution_owner)
    ).strip().lower()
    queue_worker_enabled = str(
        os.getenv("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED", "false")
    ).strip().lower() in {"1", "true", "yes", "on"}
    queue_cutover_ready = str(
        os.getenv("TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY", "false")
    ).strip().lower() in {"1", "true", "yes", "on"}
    expected_primary_bot_id = str(
        os.getenv("TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID", "")
    ).strip()
    destination_interval = float(
        os.getenv(
            "TELEGRAM_DELIVERY_QUEUE_DESTINATION_MIN_INTERVAL_SECONDS",
            "0.9",
        )
    )
    queue_runtime_ok = (
        execution_owner == "queue-v1"
        and producer_owner == "queue-v1"
        and expected_owner == "queue-v1"
        and queue_worker_enabled
        and queue_cutover_ready
        and expected_primary_bot_id.isdigit()
        and int(expected_primary_bot_id) > 0
        and destination_interval >= CHANNEL_BASE_INTERVAL_SECONDS
    )
    checks.append(
        {
            "name": "telegram_queue_runtime",
            "status": "passed" if queue_runtime_ok else "failed",
            "detail": {
                "execution_owner": execution_owner,
                "producer_owner": producer_owner,
                "expected_owner": expected_owner,
                "queue_worker_enabled": queue_worker_enabled,
                "queue_cutover_ready": queue_cutover_ready,
                "expected_primary_bot_id_configured": bool(
                    expected_primary_bot_id.isdigit()
                    and int(expected_primary_bot_id) > 0
                ),
                "destination_min_interval_seconds": destination_interval,
                "required_destination_min_interval_seconds": (
                    CHANNEL_BASE_INTERVAL_SECONDS
                ),
            },
        }
    )
    checks.append(
        {
            "name": "manifest_valid",
            "status": "passed" if not plan["summary"].get("manifest_errors") else "failed",
            "detail": plan["summary"].get("manifest_errors") or [],
        }
    )
    checks.append(
        {
            "name": "matrix_ready_env",
            "status": "passed" if (REPO_ROOT / "tmp" / "matrix-ready" / "env.sh").is_file() else "failed",
            "detail": "tmp/matrix-ready/env.sh",
        }
    )
    checks.append(
        {
            "name": "snapshot_host",
            "status": "passed" if Path(args.snapshot_host_path).is_file() else "failed",
            "detail": args.snapshot_host_path,
        }
    )

    child = run_child_preflight(args, artifact_dir)
    write_json(artifact_dir / "child-preflights.json", child)
    ot_ok = child["overtime"]["returncode"] == 0
    s2fm_ok = child["two_server_matrix"]["returncode"] == 0
    checks.append(
        {
            "name": "overtime_preflight",
            "status": "passed" if ot_ok else "failed",
            "detail": child["overtime"].get("summary"),
        }
    )
    checks.append(
        {
            "name": "two_server_preflight",
            "status": "passed" if s2fm_ok else "failed",
            "detail": child["two_server_matrix"].get("summary"),
        }
    )
    checks.append(
        {
            "name": "estimator_readiness",
            "status": "passed" if child["estimator_readiness"]["present"] else "failed",
            "detail": child["estimator_readiness"].get("payload"),
        }
    )

    failed = [item for item in checks if item["status"] != "passed"]
    status = "preflight_passed" if not failed else "preflight_failed"
    summary = {
        **plan["summary"],
        "mode": "preflight",
        "status": status,
        "failed_checks": [item["name"] for item in failed],
        "checks": checks,
        "child_preflights": {
            "overtime_returncode": child["overtime"]["returncode"],
            "s2fm_returncode": child["two_server_matrix"]["returncode"],
            "coin_gate_returncode": child["coin_gate"]["returncode"],
        },
    }
    write_json(artifact_dir / "preflight.json", {"checks": checks})
    write_json(artifact_dir / "summary.json", summary)
    return {"summary": summary, "manifest": plan["manifest"]}, 0 if not failed else 1


def _container_python(
    args: argparse.Namespace,
    *,
    server: str,
    script: str,
    script_args: list[str],
    timeout: float | None = None,
    container_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    env_args = [
        part
        for key, value in sorted((container_env or {}).items())
        for part in ("-e", f"{key}={value}")
    ]
    if server == "iran":
        remote = (
            "docker exec "
            + " ".join(shlex.quote(part) for part in env_args)
            + (" " if env_args else "")
            + f"{shlex.quote(args.iran_app_container)} python {shlex.quote(script)} "
            + " ".join(shlex.quote(part) for part in script_args)
        )
        argv = iran_ssh(args, remote)
    else:
        argv = [
            "docker",
            "exec",
            *env_args,
            args.foreign_app_container,
            "python",
            script,
            *script_args,
        ]
    completed = _run(argv, timeout=timeout if timeout is not None else args.wave_timeout_seconds)
    payload = _parse_json_stdout(completed.stdout)
    if completed.returncode != 0 and "ok" not in payload:
        payload = {
            "ok": False,
            "returncode": completed.returncode,
            "stdout_tail": (completed.stdout or "")[-1500:],
            "stderr_tail": (completed.stderr or "")[-1500:],
        }
    return payload, completed.returncode


def _set_offer_expiry_minutes(
    args: argparse.Namespace, *, server: str, value: int
) -> dict[str, Any]:
    payload, _ = _container_python(
        args,
        server=server,
        script="scripts/staging_set_trading_setting.py",
        script_args=["--key", "offer_expiry_minutes", "--set", str(value)],
        timeout=120,
    )
    return payload


def _read_trading_setting(
    args: argparse.Namespace,
    *,
    server: str,
    key: str,
) -> dict[str, Any]:
    payload, _ = _container_python(
        args,
        server=server,
        script="scripts/staging_set_trading_setting.py",
        script_args=["--key", key],
        timeout=120,
    )
    return payload


def _apply_queue_offer_expiry_override(
    args: argparse.Namespace, *, override_minutes: int
) -> dict[str, Any]:
    """Raise offer lifetime on both servers so queued offers survive the peak.

    With the staging default of 2 minutes, offers deep in a peak-sized send
    backlog would expire before their channel post, which never happens with
    real users. Returns the original values for the later restore.
    """
    override = int(override_minutes)
    if override <= 0:
        return {"enabled": False}
    result: dict[str, Any] = {"enabled": True, "override": override, "servers": {}}
    for server in ("iran", "foreign"):
        payload = _set_offer_expiry_minutes(args, server=server, value=override)
        result["servers"][server] = payload
    return result


def _restore_queue_offer_expiry(
    args: argparse.Namespace, override_state: dict[str, Any]
) -> dict[str, Any]:
    if not override_state.get("enabled"):
        return {"enabled": False}
    result: dict[str, Any] = {"enabled": True, "servers": {}}
    for server in ("iran", "foreign"):
        payload = (override_state.get("servers") or {}).get(server) or {}
        previous = payload.get("previous")
        if previous is None:
            result["servers"][server] = {"ok": False, "error": "no previous value captured"}
            continue
        result["servers"][server] = _set_offer_expiry_minutes(
            args, server=server, value=int(previous)
        )
    return result


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_step(
    label: str,
    argv: list[str],
    *,
    timeout: float,
) -> tuple[bool, dict[str, Any], subprocess.CompletedProcess[str]]:
    completed = _run(argv, timeout=timeout)
    evidence = {
        "label": label,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-500:],
        "stderr_tail": (completed.stderr or "")[-500:],
    }
    return completed.returncode == 0, evidence, completed


def _verified_event_transfer(
    args: argparse.Namespace,
    *,
    events_path: Path,
) -> dict[str, Any]:
    """Copy one immutable events file and verify its bytes in both containers."""

    expected_sha256 = _sha256_file(events_path)
    host = args.iran_ssh_host if "@" in args.iran_ssh_host else f"root@{args.iran_ssh_host}"
    safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(args.run_id))[:120]
    filename = f"{safe_run}-wave-events-{expected_sha256[:12]}.json"
    remote_events = f"{args.iran_workdir}/tmp/{filename}"
    container_events = f"/tmp/{filename}"
    steps: list[dict[str, Any]] = []

    def checksum_output(completed: subprocess.CompletedProcess[str]) -> str:
        parts = (completed.stdout or "").strip().split()
        return parts[0].lower() if parts else ""

    def run_step(
        label: str, argv: list[str], *, timeout: float = 60.0
    ) -> subprocess.CompletedProcess[str] | None:
        ok, evidence, completed = _command_step(label, argv, timeout=timeout)
        steps.append(evidence)
        return completed if ok else None

    if run_step(
        "iran_remote_tmp",
        iran_ssh(args, f"mkdir -p {shlex.quote(args.iran_workdir + '/tmp')}"),
        timeout=30,
    ) is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    if run_step(
        "scp_to_iran_host",
        [
            "scp",
            "-P",
            str(args.iran_ssh_port),
            "-o",
            "BatchMode=yes",
            str(events_path),
            f"{host}:{remote_events}",
        ],
        timeout=120,
    ) is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    host_hash = run_step(
        "iran_host_checksum",
        iran_ssh(args, f"sha256sum {shlex.quote(remote_events)}"),
    )
    if host_hash is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    if checksum_output(host_hash) != expected_sha256:
        steps[-1]["ok"] = False
        steps[-1]["error"] = "Iran host checksum mismatch"
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    if run_step(
        "docker_cp_iran",
        iran_ssh(
            args,
            "docker cp "
            f"{shlex.quote(remote_events)} "
            f"{shlex.quote(args.iran_app_container + ':' + container_events)}",
        ),
    ) is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    iran_hash = run_step(
        "iran_container_checksum",
        iran_ssh(
            args,
            "docker exec "
            f"{shlex.quote(args.iran_app_container)} "
            f"sha256sum {shlex.quote(container_events)}",
        ),
    )
    if iran_hash is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    if checksum_output(iran_hash) != expected_sha256:
        steps[-1]["ok"] = False
        steps[-1]["error"] = "Iran container checksum mismatch"
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    if run_step(
        "docker_cp_foreign",
        [
            "docker",
            "cp",
            str(events_path),
            f"{args.foreign_app_container}:{container_events}",
        ],
    ) is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    foreign_hash = run_step(
        "foreign_container_checksum",
        [
            "docker",
            "exec",
            args.foreign_app_container,
            "sha256sum",
            container_events,
        ],
    )
    if foreign_hash is None:
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    if checksum_output(foreign_hash) != expected_sha256:
        steps[-1]["ok"] = False
        steps[-1]["error"] = "foreign container checksum mismatch"
        return {"ok": False, "expected_sha256": expected_sha256, "steps": steps}
    return {
        "ok": True,
        "expected_sha256": expected_sha256,
        "container_events_path": container_events,
        "iran_remote_path": remote_events,
        "steps": steps,
    }


def _queue_sample(
    args: argparse.Namespace,
    *,
    since_utc: str | None = None,
    timing: bool = False,
) -> dict[str, Any]:
    script_args = ["--run-prefix", _queue_run_prefix(args)]
    if timing:
        script_args.append("--timing")
    if since_utc:
        script_args.extend(["--since-utc", since_utc])
    else:
        script_args.extend(
            ["--lookback-minutes", str(int(args.timing_lookback_minutes))]
        )
    payload, code = _container_python(
        args,
        server="foreign",
        script="scripts/staging_combined_matrix_queue_sampler.py",
        script_args=script_args,
        timeout=180,
    )
    if code != 0:
        payload = {**payload, "ok": False, "returncode": code}
    return payload


def _queue_run_prefix(args: argparse.Namespace) -> str:
    """Return a queue-only namespace that cannot match other matrix lanes.

    The comprehensive market lane intentionally runs before the queue lane and
    uses ``{run_prefix}_CLM_``.  Sampling the broad combined prefix therefore
    counts comprehensive delivery jobs as queue-wave baseline residue.  Keep
    the queue namespace below the combined prefix so final healing can still
    remove the whole run, while isolating queue SLO evidence from every other
    lane.
    """

    return f"{args.run_prefix}_QUEUE"


def _wave_prefix_sync_catchup(args: argparse.Namespace) -> dict[str, Any]:
    """Push committed wave rows while deferred reactions await publication."""

    results: dict[str, Any] = {}
    codes: list[int] = []
    queue_prefix = _queue_run_prefix(args)
    for server, suffix in (("iran", "IR"), ("foreign", "FO")):
        payload, code = _container_python(
            args,
            server=server,
            script="scripts/trading_core_probe_worker.py",
            script_args=[
                "sync-prefix-catchup",
                "--prefix",
                f"{queue_prefix}_{suffix}",
                "--batch-size",
                "500",
            ],
            timeout=300,
        )
        results[server] = payload
        codes.append(code)
    return {
        "ok": all(code == 0 for code in codes)
        and all(
            str(payload.get("status") or "") == "ok"
            for payload in results.values()
        ),
        "results": results,
    }


def _effective_wave_limits(
    args: argparse.Namespace, *, expected_valid: int
) -> dict[str, Any]:
    action_share = min(
        1.0,
        max(
            0.0,
            (int(args.wave_trade_percent) + int(args.wave_manual_expire_percent))
            / 100.0,
        ),
    )
    estimated_channel_operations = max(
        1, int(round(expected_valid * (1.0 + action_share)))
    )
    drain_seconds = max(
        0.0,
        (estimated_channel_operations - CHANNEL_IDLE_BURST_CAPACITY)
        * CHANNEL_BASE_INTERVAL_SECONDS,
    )
    schedule_seconds = (
        float(manifest_builder.WAVE_SECONDS)
        if args.wave_profile == "realtime-30m"
        else 0.0
    )
    required_total_seconds = schedule_seconds + drain_seconds
    # Include the latest scheduled reaction. With the real two-minute offer
    # lifetime, a live wave must fit this wall-clock budget instead of silently
    # extending the product setting for the test.
    latest_action_delay = (
        float(args.wave_action_delay_seconds) * 1.4
        if not args.wave_immediate_actions
        else 0.0
    )
    required_lifecycle_seconds = required_total_seconds + latest_action_delay
    required_expiry_minutes = max(
        1,
        int((required_lifecycle_seconds + 59.999) // 60.0),
    )
    configured_expiry = max(0, int(args.queue_offer_expiry_minutes))
    override_enabled = bool(args.allow_temporary_queue_expiry_override)
    effective_expiry = (
        max(required_expiry_minutes, configured_expiry)
        if override_enabled
        else configured_expiry
    )
    if override_enabled:
        publish_wait = max(
            float(args.wave_publish_wait_timeout_seconds),
            required_total_seconds * 1.20,
        )
        action_drain = max(
            float(args.wave_action_drain_timeout_seconds),
            required_total_seconds * 1.25,
        )
        wave_timeout = max(
            float(args.wave_timeout_seconds),
            schedule_seconds + action_drain + 900.0,
        )
    else:
        # Waiting beyond the real offer lifetime cannot produce a valid user
        # reaction.  Bound the default 30/40-minute diagnostic timeouts to the
        # two-minute product contract so a publication failure fails promptly.
        lifetime_seconds = max(60.0, float(effective_expiry) * 60.0)
        publish_wait = min(
            float(args.wave_publish_wait_timeout_seconds),
            max(15.0, lifetime_seconds - latest_action_delay - 5.0),
        )
        action_drain = min(
            float(args.wave_action_drain_timeout_seconds),
            max(required_total_seconds * 1.25, lifetime_seconds + 30.0),
        )
        wave_timeout = max(
            required_total_seconds + 60.0,
            min(
                float(args.wave_timeout_seconds),
                schedule_seconds + action_drain + 300.0,
            ),
        )
    return {
        "estimated_channel_operations": estimated_channel_operations,
        "channel_base_interval_seconds": CHANNEL_BASE_INTERVAL_SECONDS,
        "channel_sustained_operations_per_minute": (
            60.0 / CHANNEL_BASE_INTERVAL_SECONDS
        ),
        "channel_idle_burst_capacity": CHANNEL_IDLE_BURST_CAPACITY,
        "estimated_channel_drain_seconds": round(drain_seconds, 3),
        "schedule_seconds": schedule_seconds,
        "required_offer_expiry_minutes": required_expiry_minutes,
        "required_offer_lifecycle_seconds": round(required_lifecycle_seconds, 3),
        "configured_offer_expiry_minutes": configured_expiry,
        "temporary_expiry_override_enabled": override_enabled,
        "fits_configured_offer_lifetime": (
            required_lifecycle_seconds <= configured_expiry * 60.0
        ),
        "effective_offer_expiry_minutes": effective_expiry,
        "effective_publish_wait_timeout_seconds": round(publish_wait, 3),
        "effective_action_drain_timeout_seconds": round(action_drain, 3),
        "effective_wave_timeout_seconds": round(wave_timeout, 3),
        "default_p50_slo_seconds": round(max(60.0, required_total_seconds * 0.65), 3),
        "default_p95_slo_seconds": round(max(120.0, required_total_seconds * 1.15), 3),
    }


def _assertion(
    name: str,
    *,
    passed: bool,
    expected: Any,
    actual: Any,
    cells: list[str] | None = None,
    identifiers: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
        "cells": cells or [],
        "identifiers": identifiers or [],
    }


def run_wave(args: argparse.Namespace, manifest: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    budget = wave_driver.WaveBudget(
        valid_target=int(manifest["wave"]["valid_target"]),
        invalid_target=int(manifest["wave"]["invalid_target"]),
        scale=float(args.wave_scale),
        reduction_reason=args.wave_reduction_reason
        or (
            "staging max_active_offers + controlled combined-matrix time budget; "
            "full 4800 schedule hash retained in plan artifact"
            if float(args.wave_scale) < 1.0
            else None
        ),
    )
    selected = wave_driver.scale_events(list(manifest["wave_events"]), budget)
    selected_schedule_sha256 = hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_schedule_sha256 = str(manifest["wave"]["schedule_sha256"])
    full_schedule_hash_matches = (
        float(budget.scale) < 1.0
        or selected_schedule_sha256 == manifest_schedule_sha256
    )
    events_path = artifact_dir / "wave-selected-events.json"
    write_json(
        events_path,
        {
            "events": selected,
            "budget": {
                "scale": budget.scale,
                "valid_limit": budget.valid_limit,
                "invalid_limit": budget.invalid_limit,
                "reduction_reason": budget.reduction_reason,
            },
            "selected_schedule_sha256": selected_schedule_sha256,
            "manifest_schedule_sha256": manifest_schedule_sha256,
        },
    )
    selected_valid = [item for item in selected if item.get("kind") == "valid"]
    selected_invalid = [item for item in selected if item.get("kind") == "invalid"]
    limits = _effective_wave_limits(args, expected_valid=len(selected_valid))
    write_json(artifact_dir / "wave-effective-limits.json", limits)

    # Capture a true pre-wave baseline before any offer is created.
    queue_baseline = _queue_sample(args)
    write_json(artifact_dir / "queue-baseline.json", queue_baseline)
    baseline_scoped = queue_baseline.get("scoped") or {}
    baseline_clean = (
        bool(queue_baseline.get("ok"))
        and int(baseline_scoped.get("offer_count") or 0) == 0
        and int(baseline_scoped.get("job_count") or 0) == 0
    )
    if not baseline_clean:
        return {
            "ok": False,
            "profile": args.wave_profile,
            "budget": {
                "scale": budget.scale,
                "valid_limit": budget.valid_limit,
                "invalid_limit": budget.invalid_limit,
                "selected_count": len(selected),
            },
            "failure": "run-scoped queue baseline is not clean",
            "queue": {"baseline": queue_baseline},
        }

    transfer = _verified_event_transfer(args, events_path=events_path)
    write_json(artifact_dir / "wave-event-transfer.json", transfer)
    if not transfer.get("ok"):
        return {
            "ok": False,
            "profile": args.wave_profile,
            "budget": {
                "scale": budget.scale,
                "valid_limit": budget.valid_limit,
                "invalid_limit": budget.invalid_limit,
                "selected_count": len(selected),
            },
            "failure": "wave event transfer/checksum verification failed",
            "event_transfer": transfer,
            "queue": {"baseline": queue_baseline},
        }
    container_events_path = str(transfer["container_events_path"])
    events_file_sha256 = str(transfer["expected_sha256"])

    dwell = float(args.publish_dwell_seconds)
    expected_webapp = sum(
        1 for item in selected_valid if item.get("surface") == "webapp"
    )
    expected_bot = len(selected_valid) - expected_webapp
    required_owner_pool = max(
        1,
        (
            max(expected_webapp, expected_bot)
            + ACTIVE_OFFERS_PER_SYNTHETIC_OWNER
            - 1
        )
        // ACTIVE_OFFERS_PER_SYNTHETIC_OWNER,
    )
    owner_pool = max(int(args.owner_pool_size), required_owner_pool)
    limits["configured_owner_pool_size"] = int(args.owner_pool_size)
    limits["required_owner_pool_size"] = required_owner_pool
    limits["effective_owner_pool_size"] = owner_pool

    trading_settings_observed: dict[str, dict[str, Any]] = {}
    for server in ("iran", "foreign"):
        expiry = _read_trading_setting(
            args,
            server=server,
            key="offer_expiry_minutes",
        )
        max_active = _read_trading_setting(
            args,
            server=server,
            key="max_active_offers",
        )
        trading_settings_observed[server] = {
            "offer_expiry_minutes": expiry,
            "max_active_offers": max_active,
        }
    settings_ok = all(
        bool(values["offer_expiry_minutes"].get("ok"))
        and str(values["offer_expiry_minutes"].get("value"))
        == str(int(args.queue_offer_expiry_minutes))
        and bool(values["max_active_offers"].get("ok"))
        and str(values["max_active_offers"].get("value"))
        == str(ACTIVE_OFFERS_PER_SYNTHETIC_OWNER)
        for values in trading_settings_observed.values()
    )
    write_json(
        artifact_dir / "queue-trading-settings.json",
        {
            "ok": settings_ok,
            "expected": {
                "offer_expiry_minutes": int(args.queue_offer_expiry_minutes),
                "max_active_offers": ACTIVE_OFFERS_PER_SYNTHETIC_OWNER,
            },
            "servers": trading_settings_observed,
        },
    )
    if not settings_ok:
        return {
            "ok": False,
            "profile": args.wave_profile,
            "failure": "queue trading settings differ from the matrix contract",
            "effective_limits": limits,
            "trading_settings": trading_settings_observed,
            "event_transfer": transfer,
            "queue": {"baseline": queue_baseline},
        }
    if (
        not bool(limits["fits_configured_offer_lifetime"])
        and not bool(args.allow_temporary_queue_expiry_override)
    ):
        return {
            "ok": False,
            "profile": args.wave_profile,
            "failure": (
                "selected live wave cannot finish inside the real two-minute "
                "offer lifetime; reduce --wave-scale/action delay or explicitly "
                "run a separate infrastructure-only expiry-override diagnostic"
            ),
            "effective_limits": limits,
            "trading_settings": trading_settings_observed,
            "event_transfer": transfer,
            "queue": {"baseline": queue_baseline},
        }
    wave_args_common = [
        "--events-file",
        container_events_path,
        "--events-sha256",
        events_file_sha256,
        "--owner-pool-size",
        str(owner_pool),
        "--max-active-offers",
        str(ACTIVE_OFFERS_PER_SYNTHETIC_OWNER),
        "--publish-dwell-seconds",
        str(dwell),
        "--speed",
        str(float(args.wave_speed)),
    ]
    if args.wave_profile == "realtime-30m":
        wave_args_common.append("--realtime")
    if not args.wave_immediate_actions:
        wave_args_common.extend(
            [
                "--defer-actions",
                "--action-delay-seconds",
                str(float(args.wave_action_delay_seconds)),
                "--trade-percent",
                str(int(args.wave_trade_percent)),
                "--manual-expire-percent",
                str(int(args.wave_manual_expire_percent)),
                "--publish-wait-timeout-seconds",
                str(float(limits["effective_publish_wait_timeout_seconds"])),
                "--action-drain-timeout-seconds",
                str(float(limits["effective_action_drain_timeout_seconds"])),
            ]
        )

    queue_prefix = _queue_run_prefix(args)
    iran_script_args = [
        "--run-prefix",
        f"{queue_prefix}_IR",
        "--surface-filter",
        "webapp",
        "--snapshot-path",
        args.snapshot_container_path,
        *wave_args_common,
    ]
    foreign_script_args = [
        "--run-prefix",
        f"{queue_prefix}_FO",
        "--surface-filter",
        "bot",
        *wave_args_common,
    ]

    # Default execution preserves the real two-minute lifetime. A temporary
    # override is available only as an explicit infrastructure diagnostic and
    # is never silently enabled by the gate.
    expiry_override: dict[str, Any] = {"enabled": False}
    if args.allow_temporary_queue_expiry_override:
        expiry_override = _apply_queue_offer_expiry_override(
            args,
            override_minutes=int(limits["effective_offer_expiry_minutes"]),
        )
    write_json(artifact_dir / "offer-expiry-override.json", expiry_override)
    override_servers = (expiry_override.get("servers") or {}).values()
    if expiry_override.get("enabled") and not all(
        bool(payload.get("ok")) for payload in override_servers
    ):
        restore_state = _restore_queue_offer_expiry(args, expiry_override)
        write_json(artifact_dir / "offer-expiry-restore.json", restore_state)
        return {
            "ok": False,
            "profile": args.wave_profile,
            "failure": "offer expiry override failed on at least one server",
            "offer_expiry_override": expiry_override,
            "offer_expiry_restore": restore_state,
            "event_transfer": transfer,
            "queue": {"baseline": queue_baseline},
        }

    # Iran (webapp) and foreign (bot) must pace the same 30-minute wall clock.
    iran_payload: dict[str, Any] = {}
    foreign_payload: dict[str, Any] = {}
    iran_code = 1
    foreign_code = 1
    wave_started = time.perf_counter()
    wave_started_utc = utc_now()
    queue_monitor_samples: list[dict[str, Any]] = []
    queue_post_wave: dict[str, Any] = {}
    queue_final: dict[str, Any] = {}
    iran_cleanup: dict[str, Any] = {}
    foreign_cleanup: dict[str, Any] = {}
    timing: dict[str, Any] = {}
    wave_exception: str | None = None
    wave_wall_seconds = 0.0
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(
                    _container_python,
                    args,
                    server="iran",
                    script="scripts/staging_combined_matrix_mutating_wave.py",
                    script_args=iran_script_args,
                    timeout=float(limits["effective_wave_timeout_seconds"]),
                ): "iran",
                pool.submit(
                    _container_python,
                    args,
                    server="foreign",
                    script="scripts/staging_combined_matrix_mutating_wave.py",
                    script_args=foreign_script_args,
                    timeout=float(limits["effective_wave_timeout_seconds"]),
                ): "foreign",
            }
            pending = set(futures)
            sample_interval = max(5.0, float(args.queue_sample_interval_seconds))
            while pending:
                done, _ = wait(
                    pending,
                    timeout=sample_interval,
                    return_when=FIRST_COMPLETED,
                )
                sample = _queue_sample(args, since_utc=wave_started_utc)
                sample["wave_elapsed_seconds"] = round(
                    time.perf_counter() - wave_started, 3
                )
                sample["prefix_sync_catchup"] = _wave_prefix_sync_catchup(args)
                queue_monitor_samples.append(sample)
                for future in done:
                    name = futures[future]
                    pending.remove(future)
                    try:
                        payload, code = future.result()
                    except Exception as exc:  # noqa: BLE001
                        payload = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        code = 1
                    if name == "iran":
                        iran_payload, iran_code = payload, code
                    else:
                        foreign_payload, foreign_code = payload, code
        wave_wall_seconds = round(time.perf_counter() - wave_started, 3)
        write_json(artifact_dir / "wave-iran.json", iran_payload)
        write_json(artifact_dir / "wave-foreign.json", foreign_payload)
        write_json(
            artifact_dir / "queue-monitor.json",
            {"samples": queue_monitor_samples},
        )

        queue_post_wave = _queue_sample(
            args, since_utc=wave_started_utc, timing=True
        )
        time.sleep(max(15.0, float(args.drain_wait_seconds)))
        queue_final = _queue_sample(
            args, since_utc=wave_started_utc, timing=True
        )
        write_json(
            artifact_dir / "queue-sample.json",
            {
                "baseline": queue_baseline,
                "post_wave": queue_post_wave,
                "after_drain_wait": queue_final,
            },
        )
        timing = (queue_final.get("scoped") or {}).get("timing") or {}
        global_timing = (queue_final.get("global") or {}).get("timing") or {}
        write_json(
            artifact_dir / "telegram-send-timing.json",
            {
                "ok": bool(queue_final.get("ok")),
                "at_utc": queue_final.get("at_utc"),
                "prefix": args.run_prefix,
                "scoped": {
                    "pending_jobs": (queue_final.get("scoped") or {}).get(
                        "pending_jobs"
                    ),
                    "timing": timing,
                    "provider_timing": (
                        queue_final.get("scoped") or {}
                    ).get("provider_timing"),
                },
                "global": {
                    "pending_jobs": (queue_final.get("global") or {}).get(
                        "pending_jobs"
                    ),
                    "timing": global_timing,
                },
                "recommendation": {
                    "best_send_minute_utc": timing.get("best_send_minute_utc"),
                    "best_send_minute_mean_latency_seconds": timing.get(
                        "best_send_minute_mean_latency_seconds"
                    ),
                    "p50_seconds": (timing.get("latency_seconds") or {}).get(
                        "p50"
                    ),
                    "p95_seconds": (timing.get("latency_seconds") or {}).get(
                        "p95"
                    ),
                },
            },
        )

        iran_cleanup, iran_cleanup_code = _container_python(
            args,
            server="iran",
            script="scripts/staging_combined_matrix_mutating_wave.py",
            script_args=[
                "--run-prefix",
                f"{queue_prefix}_IR",
                "--events-file",
                container_events_path,
                "--cleanup-only",
            ],
            timeout=300,
        )
        iran_cleanup["returncode"] = iran_cleanup_code
        foreign_cleanup, foreign_cleanup_code = _container_python(
            args,
            server="foreign",
            script="scripts/staging_combined_matrix_mutating_wave.py",
            script_args=[
                "--run-prefix",
                f"{queue_prefix}_FO",
                "--events-file",
                container_events_path,
                "--cleanup-only",
            ],
            timeout=300,
        )
        foreign_cleanup["returncode"] = foreign_cleanup_code
        write_json(
            artifact_dir / "wave-cleanup.json",
            {"iran": iran_cleanup, "foreign": foreign_cleanup},
        )
    except Exception as exc:  # noqa: BLE001 - return structured gate failure
        wave_exception = f"{type(exc).__name__}: {exc}"
        wave_wall_seconds = round(time.perf_counter() - wave_started, 3)
    finally:
        # Cleanup is best-effort even when sampling or a child process fails.
        if not iran_cleanup:
            try:
                iran_cleanup, iran_cleanup_code = _container_python(
                    args,
                    server="iran",
                    script="scripts/staging_combined_matrix_mutating_wave.py",
                    script_args=[
                        "--run-prefix",
                        f"{queue_prefix}_IR",
                        "--events-file",
                        container_events_path,
                        "--cleanup-only",
                    ],
                    timeout=300,
                )
                iran_cleanup["returncode"] = iran_cleanup_code
            except Exception as exc:  # noqa: BLE001
                iran_cleanup = {
                    "ok": False,
                    "returncode": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        if not foreign_cleanup:
            try:
                foreign_cleanup, foreign_cleanup_code = _container_python(
                    args,
                    server="foreign",
                    script="scripts/staging_combined_matrix_mutating_wave.py",
                    script_args=[
                        "--run-prefix",
                        f"{queue_prefix}_FO",
                        "--events-file",
                        container_events_path,
                        "--cleanup-only",
                    ],
                    timeout=300,
                )
                foreign_cleanup["returncode"] = foreign_cleanup_code
            except Exception as exc:  # noqa: BLE001
                foreign_cleanup = {
                    "ok": False,
                    "returncode": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        write_json(
            artifact_dir / "wave-cleanup.json",
            {"iran": iran_cleanup, "foreign": foreign_cleanup},
        )
        # Restore only after the wave offers are cleaned up; restoring earlier
        # would mass-expire queued offers mid-drain and skew send metrics.
        restore_state = _restore_queue_offer_expiry(args, expiry_override)
        write_json(artifact_dir / "offer-expiry-restore.json", restore_state)
    time.sleep(max(20.0, min(60.0, float(args.drain_wait_seconds))))

    created_iran = int(iran_payload.get("created_count") or 0)
    created_foreign = int(foreign_payload.get("created_count") or 0)
    expected_valid_seq = sorted(int(item["seq"]) for item in selected_valid)
    expected_invalid_seq = sorted(int(item["seq"]) for item in selected_invalid)
    actual_created_seq = [
        int(value)
        for payload in (iran_payload, foreign_payload)
        for value in (payload.get("created_seq_ids") or [])
    ]
    actual_invalid_seq = [
        int(value)
        for payload in (iran_payload, foreign_payload)
        for value in (payload.get("invalid_rejected_seq_ids") or [])
    ]
    expected_offer_public_ids = sorted(
        str(value)
        for payload in (iran_payload, foreign_payload)
        for value in (payload.get("offer_public_ids") or [])
    )
    trade_selected = [
        item
        for item in selected_valid
        if int(item["seq"]) % 100 < int(args.wave_trade_percent)
    ]
    expected_request_web = sum(
        1 for item in trade_selected if item.get("request_surface") == "webapp"
    )
    expected_request_tg = len(trade_selected) - expected_request_web
    iran_request_mix = iran_payload.get("request_execution_mix") or {}
    foreign_request_mix = foreign_payload.get("request_execution_mix") or {}
    actual_request_web = int(iran_request_mix.get("webapp") or 0) + int(
        foreign_request_mix.get("webapp") or 0
    )
    actual_request_tg = int(iran_request_mix.get("telegram") or 0) + int(
        foreign_request_mix.get("telegram") or 0
    )
    actual_request_total = actual_request_web + actual_request_tg
    actual_request_web_share = (
        actual_request_web / float(actual_request_total)
        if actual_request_total
        else 0.0
    )
    payload_assertions_passed = all(
        bool(value)
        for payload in (iran_payload, foreign_payload)
        for value in (payload.get("assertions") or {}).values()
    ) and all(
        bool((payload.get("assertions") or {}))
        for payload in (iran_payload, foreign_payload)
    )
    prefix_sync_catchup_ok = bool(queue_monitor_samples) and all(
        bool((sample.get("prefix_sync_catchup") or {}).get("ok"))
        for sample in queue_monitor_samples
    )
    checksum_passed = all(
        payload.get("events_file_sha256") == events_file_sha256
        and payload.get("expected_events_file_sha256") == events_file_sha256
        for payload in (iran_payload, foreign_payload)
    )
    monitor_scoped = [
        ((sample.get("scoped") or {}).get("public") or sample.get("scoped") or {})
        for sample in queue_monitor_samples
        if sample.get("ok")
    ]
    post_wave_scoped = queue_post_wave.get("scoped") or {}
    monitor_scoped.append(post_wave_scoped.get("public") or post_wave_scoped)
    peak_pending = max(
        (int(sample.get("pending_jobs") or 0) for sample in monitor_scoped),
        default=0,
    )
    dynamic_min_peak = max(1, int(round(len(selected_valid) * 0.25)))
    min_peak = (
        int(args.queue_slo_min_peak_pending)
        if args.queue_slo_min_peak_pending is not None
        else dynamic_min_peak
    )
    final_all_scoped = queue_final.get("scoped") or {}
    final_scoped = final_all_scoped.get("public") or final_all_scoped
    final_private = final_all_scoped.get("synthetic_private") or {}
    latency = (final_scoped.get("timing") or {}).get("latency_seconds") or {}
    provider_timing = final_scoped.get("provider_timing") or {}
    edit_provider_latency = provider_timing.get("edit_latency_seconds") or {}
    p50 = latency.get("p50")
    p95 = latency.get("p95")
    edit_provider_p95 = edit_provider_latency.get("p95")
    max_p50 = (
        float(args.queue_slo_max_p50_seconds)
        if args.queue_slo_max_p50_seconds is not None
        else float(limits["default_p50_slo_seconds"])
    )
    max_p95 = (
        float(args.queue_slo_max_p95_seconds)
        if args.queue_slo_max_p95_seconds is not None
        else float(limits["default_p95_slo_seconds"])
    )
    retried_jobs = int(final_scoped.get("retried_jobs") or 0)
    retry_recovered_jobs = int(final_scoped.get("retry_recovered_jobs") or 0)
    rate_limited_jobs = int(final_scoped.get("rate_limited_jobs") or 0)
    rate_limit_recovered_jobs = int(
        final_scoped.get("rate_limit_recovered_jobs") or 0
    )
    retry_limit_ok = (
        args.queue_slo_max_retried_jobs is None
        or retried_jobs <= int(args.queue_slo_max_retried_jobs)
    )
    rate_limit_limit_ok = (
        args.queue_slo_max_rate_limited_jobs is None
        or rate_limited_jobs <= int(args.queue_slo_max_rate_limited_jobs)
    )
    edit_sample_count = int(provider_timing.get("edit_sample_count") or 0)
    slow_edit_count = int(provider_timing.get("slow_edit_count") or 0)
    slow_edit_ratio = (
        slow_edit_count / float(edit_sample_count) if edit_sample_count else 0.0
    )
    slow_edit_count_ok = (
        args.queue_slo_max_slow_edit_responses is None
        or slow_edit_count <= int(args.queue_slo_max_slow_edit_responses)
    )
    assertions = [
        _assertion(
            "wave_orchestration_completed",
            passed=wave_exception is None,
            expected={"exception": None},
            actual={"exception": wave_exception},
        ),
        _assertion(
            "event_transfer_checksums",
            passed=checksum_passed and full_schedule_hash_matches,
            expected={
                "file_sha256": events_file_sha256,
                "manifest_schedule_sha256": manifest_schedule_sha256,
            },
            actual={
                "iran": iran_payload.get("events_file_sha256"),
                "foreign": foreign_payload.get("events_file_sha256"),
                "selected_schedule_sha256": selected_schedule_sha256,
            },
        ),
        _assertion(
            "all_valid_events_created_exactly_once",
            passed=sorted(actual_created_seq) == expected_valid_seq
            and len(actual_created_seq) == len(set(actual_created_seq)),
            expected=len(expected_valid_seq),
            actual=len(actual_created_seq),
            cells=["queue:wave:valid"],
            identifiers=actual_created_seq,
        ),
        _assertion(
            "all_invalid_events_rejected_exactly_once",
            passed=sorted(actual_invalid_seq) == expected_invalid_seq
            and len(actual_invalid_seq) == len(set(actual_invalid_seq)),
            expected=len(expected_invalid_seq),
            actual=len(actual_invalid_seq),
            cells=["queue:wave:invalid"],
            identifiers=actual_invalid_seq,
        ),
        _assertion(
            "webapp_surface_exact",
            passed=created_iran == expected_webapp,
            expected=expected_webapp,
            actual=created_iran,
            cells=["queue:surface:webapp"],
        ),
        _assertion(
            "bot_surface_exact",
            passed=created_foreign == expected_bot,
            expected=expected_bot,
            actual=created_foreign,
            cells=["queue:surface:bot"],
        ),
        _assertion(
            "telegram_heavy_surface_mix",
            passed=(created_foreign + created_iran) > 0
            and created_foreign / float(created_foreign + created_iran) >= 0.60
            and created_foreign == expected_bot,
            expected={"bot_min_share": 0.60, "bot_count": expected_bot},
            actual={
                "bot_count": created_foreign,
                "bot_share": (
                    created_foreign / float(created_foreign + created_iran)
                    if created_foreign + created_iran
                    else 0.0
                ),
            },
            cells=["queue:surface:telegram_heavy"],
        ),
        _assertion(
            "request_surface_exact_and_balanced",
            passed=actual_request_web == expected_request_web
            and actual_request_tg == expected_request_tg
            and abs(actual_request_web_share - 0.50)
            <= float(args.request_surface_balance_tolerance),
            expected={
                "webapp": expected_request_web,
                "telegram": expected_request_tg,
                "webapp_share": 0.50,
                "tolerance": float(args.request_surface_balance_tolerance),
            },
            actual={
                "webapp": actual_request_web,
                "telegram": actual_request_tg,
                "webapp_share": round(actual_request_web_share, 6),
            },
            cells=["market:request_surface:balanced"],
        ),
        _assertion(
            "actions_only_after_publication",
            passed=not args.wave_immediate_actions and payload_assertions_passed,
            expected={
                "deferred_actions": True,
                "publish_timeouts": 0,
                "action_timeouts": 0,
                "trade_failures": 0,
            },
            actual={
                "iran": iran_payload.get("deferred_action_stats"),
                "foreign": foreign_payload.get("deferred_action_stats"),
            },
            cells=["queue:wave:valid"],
        ),
        _assertion(
            "wave_prefix_sync_catchup",
            passed=prefix_sync_catchup_ok,
            expected={"all_monitor_rounds_ok": True},
            actual={
                "round_count": len(queue_monitor_samples),
                "failed_rounds": sum(
                    not bool(
                        (sample.get("prefix_sync_catchup") or {}).get("ok")
                    )
                    for sample in queue_monitor_samples
                ),
            },
            cells=["queue:wave:valid"],
        ),
        _assertion(
            "run_scoped_baseline_clean",
            passed=baseline_clean,
            expected={"offer_count": 0, "job_count": 0},
            actual=baseline_scoped,
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "peak_backlog_reached",
            passed=peak_pending >= min_peak,
            expected={"minimum_pending": min_peak},
            actual={"peak_pending": peak_pending},
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "final_backlog_slo",
            passed=int(final_scoped.get("pending_jobs") or 0)
            <= int(args.queue_slo_max_final_pending),
            expected={"max_pending": int(args.queue_slo_max_final_pending)},
            actual={"pending": int(final_scoped.get("pending_jobs") or 0)},
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "queue_failure_slo",
            passed=int(final_scoped.get("failed_jobs") or 0)
            <= int(args.queue_slo_max_failed_jobs),
            expected={"max_failed": int(args.queue_slo_max_failed_jobs)},
            actual={"failed": int(final_scoped.get("failed_jobs") or 0)},
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "queue_retry_recovery_slo",
            passed=retry_recovered_jobs == retried_jobs and retry_limit_ok,
            expected={
                "all_retried_public_jobs_recovered": True,
                "max_retried": args.queue_slo_max_retried_jobs,
            },
            actual={
                "retried": retried_jobs,
                "recovered": retry_recovered_jobs,
            },
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "queue_rate_limit_recovery_slo",
            passed=rate_limit_recovered_jobs == rate_limited_jobs
            and rate_limit_limit_ok,
            expected={
                "all_rate_limited_public_jobs_recovered": True,
                "max_rate_limited": args.queue_slo_max_rate_limited_jobs,
            },
            actual={
                "rate_limited": rate_limited_jobs,
                "recovered": rate_limit_recovered_jobs,
            },
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "queue_latency_slo",
            passed=p50 is not None
            and p95 is not None
            and float(p50) <= max_p50
            and float(p95) <= max_p95,
            expected={"p50_max_seconds": max_p50, "p95_max_seconds": max_p95},
            actual={"p50_seconds": p50, "p95_seconds": p95},
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "edit_provider_latency_slo",
            passed=edit_provider_p95 is not None
            and float(edit_provider_p95)
            <= float(args.queue_slo_max_edit_provider_p95_seconds)
            and slow_edit_count_ok
            and slow_edit_ratio <= float(args.queue_slo_max_slow_edit_ratio),
            expected={
                "edit_p95_max_seconds": float(
                    args.queue_slo_max_edit_provider_p95_seconds
                ),
                "max_slow_edit_responses": args.queue_slo_max_slow_edit_responses,
                "max_slow_edit_ratio": float(args.queue_slo_max_slow_edit_ratio),
            },
            actual={
                "edit_p95_seconds": edit_provider_p95,
                "slow_edit_count": slow_edit_count,
                "edit_sample_count": edit_sample_count,
                "slow_edit_ratio": round(slow_edit_ratio, 6),
            },
            cells=["queue:regime:peak"],
        ),
        _assertion(
            "synthetic_private_jobs_reached_terminal_state",
            passed=int(final_private.get("pending_jobs") or 0) == 0
            and int(final_private.get("unexpected_failed_jobs") or 0) == 0,
            expected={
                "pending_synthetic_private_jobs": 0,
                "unexpected_failed_jobs": 0,
            },
            actual={
                "job_count": int(final_private.get("job_count") or 0),
                "pending": int(final_private.get("pending_jobs") or 0),
                "failed": int(final_private.get("failed_jobs") or 0),
                "expected_failed": int(
                    final_private.get("expected_failed_jobs") or 0
                ),
                "unexpected_failed": int(
                    final_private.get("unexpected_failed_jobs") or 0
                ),
                "failure_reason_counts": final_private.get(
                    "failure_reason_counts"
                )
                or {},
                "state_counts": final_private.get("state_counts") or {},
            },
            cells=["queue:wave:valid"],
        ),
        _assertion(
            "every_offer_publish_sent_exactly_once",
            passed=sorted(final_scoped.get("sent_offer_public_ids") or [])
            == expected_offer_public_ids
            and len(expected_offer_public_ids) == len(selected_valid)
            and int(
                (final_scoped.get("sent_action_counts") or {}).get(
                    "offer_publish"
                )
                or 0
            )
            == len(selected_valid),
            expected={
                "offer_publish_sent": len(selected_valid),
                "offer_public_ids": expected_offer_public_ids,
            },
            actual={
                "offer_publish_sent": int(
                    (final_scoped.get("sent_action_counts") or {}).get(
                        "offer_publish"
                    )
                    or 0
                ),
                "offer_public_ids": final_scoped.get(
                    "sent_offer_public_ids"
                )
                or [],
            },
            cells=["queue:wave:valid"],
            identifiers=list(final_scoped.get("sent_offer_public_ids") or []),
        ),
    ]
    cleanup_ok = all(
        bool(payload.get("ok")) and int(payload.get("returncode") or 0) == 0
        for payload in (iran_cleanup, foreign_cleanup)
    )
    restore_ok = not restore_state.get("enabled") or all(
        bool(payload.get("ok"))
        for payload in (restore_state.get("servers") or {}).values()
    )
    execution_ok = (
        iran_code == 0
        and foreign_code == 0
        and bool(iran_payload.get("ok"))
        and bool(foreign_payload.get("ok"))
        and wave_exception is None
        and all(item["passed"] for item in assertions)
        and cleanup_ok
        and restore_ok
    )
    return {
        "ok": execution_ok,
        "profile": args.wave_profile,
        "budget": {
            "scale": budget.scale,
            "valid_limit": budget.valid_limit,
            "invalid_limit": budget.invalid_limit,
            "reduction_reason": budget.reduction_reason,
            "selected_count": len(selected),
        },
        "effective_limits": limits,
        "trading_settings": trading_settings_observed,
        "event_transfer": transfer,
        "realtime_wave": args.wave_profile == "realtime-30m",
        "wave_speed": float(args.wave_speed),
        "wave_wall_seconds": wave_wall_seconds,
        "wave_exception": wave_exception,
        "created_total": created_iran + created_foreign,
        "iran": {"returncode": iran_code, "payload": iran_payload},
        "foreign": {"returncode": foreign_code, "payload": foreign_payload},
        "assertions": assertions,
        "failed_assertions": [
            item["name"] for item in assertions if not item["passed"]
        ],
        "cleanup_ok": cleanup_ok,
        "restore_ok": restore_ok,
        "queue": {
            "baseline": queue_baseline,
            "monitor": queue_monitor_samples,
            "post_wave": queue_post_wave,
            "after_drain_wait": queue_final,
            "peak_pending": peak_pending,
        },
        "telegram_send_timing": timing,
        "telegram_provider_timing": provider_timing,
    }


def run_estimate_hooks(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    payload, code = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_estimate_hooks.py",
        script_args=["--snapshot-path", args.snapshot_container_path, "--price", "100000"],
    )
    write_json(artifact_dir / "estimate-hooks.json", payload)
    return {"ok": code == 0 and bool(payload.get("ok")), "returncode": code, "payload": payload}


def run_post_wave_sync_barrier(
    args: argparse.Namespace, artifact_dir: Path
) -> dict[str, Any]:
    """Flush both home-owned wave prefixes before cross-server lanes run.

    The queue wave deliberately creates enough Iran and foreign mutations to
    exercise backpressure.  Overtime cross-forward must not start behind that
    synthetic backlog or it measures queue residue instead of its own sync
    contract.
    """

    results: dict[str, Any] = {}
    codes: list[int] = []
    for server, suffix in (("iran", "IR"), ("foreign", "FO")):
        payload, code = _container_python(
            args,
            server=server,
            script="scripts/trading_core_probe_worker.py",
            script_args=[
                "sync-prefix-catchup",
                "--prefix",
                f"{args.run_prefix}_{suffix}",
                "--include-synced",
            ],
            timeout=600,
        )
        results[server] = payload
        codes.append(code)
    ok = all(code == 0 for code in codes) and all(
        str(payload.get("status") or "") == "ok"
        for payload in results.values()
    )
    report = {
        "ok": ok,
        "returncode": max(codes or [1]),
        "results": results,
    }
    write_json(artifact_dir / "post-wave-sync-barrier.json", report)
    return report


def run_overtime_execute(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    ot_dir = artifact_dir / "child-ot-execute"
    argv = [
        sys.executable,
        "scripts/run_staging_offer_overtime_acceptance.py",
        "--mode",
        "execute",
        "--run-id",
        f"{args.run_id}-OT",
        "--artifact-dir",
        str(ot_dir),
        "--expected-branch",
        args.expected_branch,
        "--expected-release-sha",
        args.expected_release_sha or run_git(["rev-parse", "HEAD"]),
        "--iran-base-url",
        args.iran_base_url,
        "--foreign-base-url",
        args.foreign_base_url,
    ]
    env = {EXECUTION_CONFIRM_ENV: ""}  # not used by OT
    env.update(_child_secret_env(args))
    env["STAGING_OFFER_OVERTIME_ACCEPTANCE_CONFIRM"] = "execute-staging-offer-overtime-acceptance"
    env["STAGING_IRAN_SSH_HOST"] = args.iran_ssh_host.split("@")[-1]
    env["STAGING_IRAN_SSH_PORT"] = str(args.iran_ssh_port)
    env["STAGING_IRAN_APP_CONTAINER"] = args.iran_app_container
    env["STAGING_FOREIGN_APP_CONTAINER"] = args.foreign_app_container
    completed = _run(argv, env=env, timeout=args.ot_timeout_seconds)
    summary = _parse_json_stdout(completed.stdout)
    write_json(
        artifact_dir / "overtime-execute.json",
        {
            "returncode": completed.returncode,
            "summary": summary,
            "stderr_tail": (completed.stderr or "")[-800:],
            "artifact_dir": str(ot_dir),
        },
    )
    scenario_results: list[dict[str, Any]] = []
    driver_path = ot_dir / "driver-results.json"
    if driver_path.is_file():
        drivers = json.loads(driver_path.read_text(encoding="utf-8"))
        scenario_results = list(drivers.get("results") or [])
        failed = [
            item for item in scenario_results if item.get("status") == "failed"
        ]
        blocked = [
            item for item in scenario_results if item.get("status") == "blocked"
        ]
        summary["failed_count"] = len(failed)
        summary["blocked_count"] = len(blocked)
    expected_ids = {scenario_id for scenario_id, _cell in manifest_builder.OT_FAMILIES}
    passed_ids = {
        str(item.get("id"))
        for item in scenario_results
        if item.get("status") == "passed"
    }
    missing_ids = sorted(expected_ids - passed_ids)
    ok = completed.returncode == 0 and not missing_ids
    return {
        "ok": ok,
        "returncode": completed.returncode,
        "summary": summary,
        "artifact_dir": str(ot_dir),
        "scenario_results": scenario_results,
        "expected_scenario_ids": sorted(expected_ids),
        "passed_scenario_ids": sorted(passed_ids),
        "missing_scenario_ids": missing_ids,
    }


def _overtime_cleanup_prefixes(overtime: dict[str, Any]) -> list[str]:
    """Return exact OT execution stamps safe for scoped hard cleanup.

    The overtime runner owns a separate OTACC_ namespace from the combined
    matrix CMB_ namespace. Its product-level retirement flow intentionally
    leaves audit rows behind; those synthetic rows can retain two equally
    terminal peer states and poison the next parity preflight. A 14-digit
    execution stamp is shared by every scenario in one OT run and is narrow
    enough for staging_combined_matrix_heal to remove only that run after
    artifacts have been written.
    """

    prefixes: set[str] = set()
    for item in overtime.get("scenario_results") or []:
        raw = str(item.get("run_prefix") or "").strip()
        match = re.fullmatch(r"(OTACC_[0-9]{14})_(?:F)?[0-9]{2}", raw)
        if match is not None:
            prefixes.add(match.group(1))
    return sorted(prefixes)


def _heal_prefix_on_both_peers(
    args: argparse.Namespace,
    *,
    run_prefix: str,
) -> dict[str, Any]:
    iran, iran_code = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--run-prefix", run_prefix],
        timeout=300,
    )
    foreign, foreign_code = _container_python(
        args,
        server="foreign",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--run-prefix", run_prefix],
        timeout=300,
    )
    return {
        "run_prefix": run_prefix,
        "ok": iran_code == 0
        and foreign_code == 0
        and bool(iran.get("ok"))
        and bool(foreign.get("ok")),
        "returncode": max(iran_code, foreign_code),
        "iran": iran,
        "foreign": foreign,
    }


def run_market_drivers(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    s2fm_dir = artifact_dir / "child-s2fm-execute"
    driver_limit = int(args.market_driver_limit or len(_driver_scenarios()))
    argv = [
        sys.executable,
        "scripts/run_staging_two_server_full_matrix.py",
        "--mode",
        "execute",
        "--run-id",
        f"{args.run_id}-S2FM",
        "--artifact-dir",
        str(s2fm_dir),
        "--expected-branch",
        args.expected_branch,
        "--expected-release-sha",
        args.expected_release_sha or run_git(["rev-parse", "HEAD"]),
        "--iran-base-url",
        args.iran_base_url,
        "--foreign-base-url",
        args.foreign_base_url,
        "--iran-ssh-host",
        args.iran_ssh_host,
        "--iran-ssh-port",
        str(args.iran_ssh_port),
        "--iran-workdir",
        args.iran_workdir,
        "--iran-app-container",
        args.iran_app_container,
        "--foreign-app-container",
        args.foreign_app_container,
        "--parity-mode",
        args.parity_mode,
        "--driver-scenario-limit",
        str(driver_limit),
    ]
    for scenario_id in args.market_driver_id or []:
        argv.extend(["--driver-scenario-id", scenario_id])
    env = {
        "STAGING_TWO_SERVER_FULL_MATRIX_CONFIRM": "execute-staging-two-server-full-matrix",
    }
    env.update(_child_secret_env(args))
    completed = _run(argv, env=env, timeout=args.market_timeout_seconds)
    summary = _parse_json_stdout(completed.stdout)
    suite_path = s2fm_dir / "driver-suite-summary.json"
    suite = (
        json.loads(suite_path.read_text(encoding="utf-8"))
        if suite_path.is_file()
        else {}
    )
    write_json(
        artifact_dir / "market-execute.json",
        {
            "returncode": completed.returncode,
            "summary": summary,
            "stderr_tail": (completed.stderr or "")[-800:],
            "artifact_dir": str(s2fm_dir),
        },
    )
    return {
        "ok": completed.returncode == 0 and suite.get("status") == "passed",
        "returncode": completed.returncode,
        "summary": summary,
        "driver_suite": suite,
        "scenario_results": list(suite.get("results") or []),
        "artifact_dir": str(s2fm_dir),
    }


def run_comprehensive_market_matrix(
    args: argparse.Namespace,
    artifact_dir: Path,
) -> dict[str, Any]:
    """Run every offer/request/trade/terminal-state scenario in staging.

    This lane complements the real two-server drivers. It exercises the real
    staging database and product service/router paths while replacing external
    Telegram/network side effects with the deterministic staging harness.
    """

    lane_dir = artifact_dir / "child-comprehensive-market"
    argv = [
        "scripts/run_staging_comprehensive_load_matrix.sh",
        "--prefix",
        f"{args.run_prefix}_CLM_",
        "--artifact-dir",
        str(lane_dir),
        "--users",
        str(args.comprehensive_market_users),
        "--attempts-per-scenario",
        str(args.comprehensive_market_attempts_per_scenario),
        "--target-rps",
        str(args.comprehensive_market_target_rps),
        "--telegram-ratio",
        "0.6",
        "--write-max-concurrency",
        str(args.comprehensive_market_write_max_concurrency),
        "--health-base-url",
        str(args.iran_base_url),
    ]
    completed = _run(
        argv,
        timeout=float(args.comprehensive_market_timeout_seconds),
    )
    report_path = lane_dir / "comprehensive-matrix.json"
    payload = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file()
        else {}
    )
    reports = list(payload.get("reports") or [])
    scenario_results: list[dict[str, Any]] = []
    for report in reports:
        scenario = dict(report.get("scenario") or {})
        scenario_results.append(
            {
                "scenario_id": str(scenario.get("scenario_id") or ""),
                "family": str(scenario.get("family") or ""),
                "offer_origin": scenario.get("offer_origin"),
                "request_surface": scenario.get("request_surface"),
                "expire_surface": scenario.get("expire_surface"),
                "offer_type": scenario.get("offer_type"),
                "shape": scenario.get("shape"),
                "terminal_state": scenario.get("terminal_state"),
                "status": str(report.get("status") or "failed"),
                "correctness_failures": list(
                    report.get("correctness_failures") or []
                ),
            }
        )
    expected_ids = {
        f"CLM-{index:03d}"
        for index in range(
            1,
            manifest_builder.COMPREHENSIVE_MARKET_SCENARIO_COUNT + 1,
        )
    }
    observed_ids = {
        item["scenario_id"] for item in scenario_results if item["scenario_id"]
    }
    passed_ids = {
        item["scenario_id"]
        for item in scenario_results
        if item["status"] == "ok"
    }
    missing_ids = sorted(expected_ids - observed_ids)
    failed_ids = sorted(expected_ids - passed_ids)
    ok = (
        completed.returncode == 0
        and payload.get("status") == "ok"
        and observed_ids == expected_ids
        and passed_ids == expected_ids
    )
    result = {
        "ok": ok,
        "returncode": completed.returncode,
        "artifact_dir": str(lane_dir),
        "report_path": str(report_path),
        "scenario_count": len(scenario_results),
        "expected_scenario_count": len(expected_ids),
        "total_business_requests": int(
            payload.get("total_business_requests") or 0
        ),
        "family_counts": dict(payload.get("family_counts") or {}),
        "scenario_results": scenario_results,
        "missing_scenario_ids": missing_ids,
        "failed_scenario_ids": failed_ids,
        "stderr_tail": (completed.stderr or "")[-1200:],
        "stdout_tail": (completed.stdout or "")[-1200:],
    }
    write_json(artifact_dir / "comprehensive-market-execute.json", result)
    return result


def archive_evidence(artifact_dir: Path) -> str:
    zip_path = artifact_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in artifact_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(artifact_dir.parent)))
    return str(zip_path)


def run_execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    artifact_dir = Path(args.artifact_dir)
    if os.getenv(EXECUTION_CONFIRM_ENV) != EXECUTION_CONFIRM_VALUE:
        summary = {
            "schema_version": SCHEMA_VERSION,
            "mode": "execute",
            "status": "execute_blocked",
            "detail": f"set {EXECUTION_CONFIRM_ENV}={EXECUTION_CONFIRM_VALUE}",
            "run_id": args.run_id,
        }
        write_json(artifact_dir / "summary.json", summary)
        return {"summary": summary}, 2

    preflight, code = run_preflight(args)
    if code != 0:
        summary = {
            **preflight["summary"],
            "mode": "execute",
            "status": "execute_blocked_by_preflight",
        }
        write_json(artifact_dir / "summary.json", summary)
        return {"summary": summary}, 1

    sync = sync_driver_scripts(args)
    write_json(artifact_dir / "script-sync.json", sync)
    if not sync.get("ok"):
        summary = {
            **preflight["summary"],
            "mode": "execute",
            "status": "execute_failed_script_sync",
            "script_sync": sync,
        }
        write_json(artifact_dir / "summary.json", summary)
        return {"summary": summary}, 1

    lanes: dict[str, Any] = {}
    # Remove only remnants owned by this exact run prefix. Unrelated staging
    # drift must remain visible and block the normal parity preflight.
    pre_heal_iran, pre_heal_iran_code = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--run-prefix", args.run_prefix],
        timeout=300,
    )
    pre_heal_foreign, pre_heal_foreign_code = _container_python(
        args,
        server="foreign",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--run-prefix", args.run_prefix],
        timeout=300,
    )
    write_json(
        artifact_dir / "pre-execute-heal.json",
        {"iran": pre_heal_iran, "foreign": pre_heal_foreign},
    )
    lanes["pre_heal"] = {
        "ok": pre_heal_iran_code == 0
        and pre_heal_foreign_code == 0
        and bool(pre_heal_iran.get("ok"))
        and bool(pre_heal_foreign.get("ok")),
        "returncode": max(pre_heal_iran_code, pre_heal_foreign_code),
        "iran": pre_heal_iran,
        "foreign": pre_heal_foreign,
    }
    if not lanes["pre_heal"]["ok"]:
        summary = {
            **preflight["summary"],
            "mode": "execute",
            "status": "execute_blocked_by_pre_heal",
            "failed_lanes": ["pre_heal"],
            "lanes": {"pre_heal": lanes["pre_heal"]},
            "finished_at_utc": utc_now(),
        }
        write_json(artifact_dir / "summary.json", summary)
        write_json(artifact_dir / "lanes.json", lanes)
        return {"summary": summary, "lanes": lanes}, 1

    # Market drivers first: S2FM execute re-checks parity, so run them while the
    # topology is still clean after combined preflight.
    market = run_market_drivers(args, artifact_dir)
    lanes["market"] = market

    # Exhaustive logical states from the earlier matrices. This is deliberately
    # a separate lane so a green nine-driver topology suite cannot hide a gap
    # in one of the 228 offer/request/trade/terminal-state combinations.
    comprehensive_market = run_comprehensive_market_matrix(args, artifact_dir)
    lanes["market_comprehensive"] = comprehensive_market

    # The comprehensive lane records all evidence before returning, but its
    # deterministic Telegram boundary still creates thousands of synthetic
    # delivery rows. Leaving those rows in the physical queue would put the
    # queue-wave and overtime markers behind unrelated CLM work even though
    # their metrics now use isolated prefixes. Remove only the CLM namespace
    # from both peers before measuring the queue lane.
    post_comprehensive_heal = _heal_prefix_on_both_peers(
        args,
        run_prefix=f"{args.run_prefix}_CLM",
    )
    write_json(
        artifact_dir / "post-comprehensive-heal.json",
        post_comprehensive_heal,
    )
    lanes["post_comprehensive_heal"] = post_comprehensive_heal

    # Queue wave before actor-guards: guards create prefix-scoped offers/notes
    # (``{run_prefix}_AG``) that would falsely dirty the wave baseline sampler.
    wave = run_wave(args, preflight["manifest"], artifact_dir)
    lanes["queue_wave"] = wave
    write_json(artifact_dir / "lane-queue-wave.json", wave)

    post_wave_sync = run_post_wave_sync_barrier(args, artifact_dir)
    lanes["post_wave_sync"] = post_wave_sync

    # This is an in-process aiogram load harness, not the live queue worker.
    # Mark only this exec process as a load runner so callback answers remain
    # observable synchronously, per telegram_delivery_runtime_policy.
    actor_guards, actor_code = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_actor_guards.py",
        script_args=["--run-prefix", f"{args.run_prefix}_AG"],
        timeout=1800,
        container_env={"TRADING_BOT_SERVICE": "load_runner"},
    )
    write_json(artifact_dir / "actor-guards.json", actor_guards)
    lanes["actor_guards"] = {
        "ok": actor_code == 0 and bool(actor_guards.get("ok")),
        "returncode": actor_code,
        "payload": actor_guards,
    }

    estimate = run_estimate_hooks(args, artifact_dir)
    lanes["estimate"] = estimate
    if args.defer_live_estimate_reason:
        lanes["estimate_live"] = {
            "ok": True,
            "status": "deferred",
            "deferred": True,
            "reason": str(args.defer_live_estimate_reason),
            "returncode": None,
        }

    overtime = run_overtime_execute(args, artifact_dir)
    lanes["overtime"] = overtime

    coverage = build_live_coverage_report(
        manifest=preflight["manifest"],
        lanes=lanes,
        artifact_dir=artifact_dir,
    )
    write_json(artifact_dir / "live-coverage.json", coverage)
    lanes["live_coverage"] = coverage

    # Final synthetic cleanup so leftover tombstones do not poison the next
    # run. Overtime uses its own OTACC timestamp namespace, so clean both the
    # combined prefix and every exact OT execution stamp found in evidence.
    heal_reports = [
        _heal_prefix_on_both_peers(args, run_prefix=args.run_prefix),
        *(
            _heal_prefix_on_both_peers(args, run_prefix=prefix)
            for prefix in _overtime_cleanup_prefixes(overtime)
        ),
    ]
    write_json(
        artifact_dir / "post-execute-heal.json",
        {"prefixes": heal_reports},
    )
    lanes["cleanup_heal"] = {
        "ok": all(report.get("ok") for report in heal_reports),
        "returncode": max(
            (int(report.get("returncode") or 0) for report in heal_reports),
            default=1,
        ),
        "prefixes": heal_reports,
    }

    failed_lanes = [name for name, payload in lanes.items() if not payload.get("ok")]
    status = "execute_passed" if not failed_lanes else "execute_failed"
    zip_path = archive_evidence(artifact_dir)
    summary = {
        **preflight["summary"],
        "mode": "execute",
        "status": status,
        "failed_lanes": failed_lanes,
        "lanes": {
            name: {
                "ok": payload.get("ok"),
                "returncode": payload.get("returncode"),
                "budget": payload.get("budget"),
                "artifact_dir": payload.get("artifact_dir"),
            }
            for name, payload in lanes.items()
        },
        "wave_budget_reduction": wave.get("budget"),
        "wave_wall_seconds": wave.get("wave_wall_seconds"),
        "created_total": wave.get("created_total"),
        "telegram_send_timing": wave.get("telegram_send_timing"),
        "telegram_provider_timing": wave.get("telegram_provider_timing"),
        "evidence_zip": zip_path,
        "deferred_lanes": [
            name for name, payload in lanes.items() if payload.get("deferred")
        ],
        "finished_at_utc": utc_now(),
    }
    write_json(artifact_dir / "summary.json", summary)
    write_json(artifact_dir / "lanes.json", lanes)
    return {"summary": summary, "lanes": lanes}, 0 if not failed_lanes else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["plan", "preflight", "execute"], default="plan")
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--run-prefix", default=None)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument(
        "--wave-scale",
        type=float,
        default=float(os.getenv("STAGING_COMBINED_WAVE_SCALE", "1.0")),
        help="Scale factor for the 4800-event wave (1.0 = full 4000 valid + 800 invalid).",
    )
    parser.add_argument("--wave-reduction-reason", default=None)
    parser.add_argument(
        "--wave-profile",
        choices=WAVE_PROFILES,
        required=True,
        help=(
            "Required execution model: burst creates selected events immediately; "
            "realtime-30m honors the deterministic 1800-second schedule."
        ),
    )
    parser.add_argument(
        "--wave-speed",
        type=float,
        default=1.0,
        help="Realtime compression factor (>1 shortens waits). Keep 1.0 for true 30-minute wave.",
    )
    parser.add_argument(
        "--owner-pool-size",
        type=int,
        default=0,
        help=(
            "Minimum synthetic owner pool per side. Zero auto-sizes from the "
            "selected wave so the ten-active-offer staging quota cannot throttle queue load."
        ),
    )
    parser.add_argument(
        "--publish-dwell-seconds",
        type=float,
        default=1.0,
        help="Bot-side pause after create so Telegram publication can enqueue.",
    )
    parser.add_argument("--drain-wait-seconds", type=float, default=180.0)
    parser.add_argument("--timing-lookback-minutes", type=int, default=45)
    parser.add_argument("--queue-sample-interval-seconds", type=float, default=30.0)
    parser.add_argument("--queue-slo-min-peak-pending", type=int, default=None)
    parser.add_argument("--queue-slo-max-final-pending", type=int, default=0)
    parser.add_argument("--queue-slo-max-failed-jobs", type=int, default=0)
    parser.add_argument("--queue-slo-max-retried-jobs", type=int, default=None)
    parser.add_argument("--queue-slo-max-rate-limited-jobs", type=int, default=None)
    parser.add_argument("--queue-slo-max-p50-seconds", type=float, default=None)
    parser.add_argument("--queue-slo-max-p95-seconds", type=float, default=None)
    parser.add_argument(
        "--queue-slo-max-edit-provider-p95-seconds",
        type=float,
        default=2.0,
    )
    parser.add_argument(
        "--queue-slo-max-slow-edit-responses",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--queue-slo-max-slow-edit-ratio",
        type=float,
        default=0.025,
    )
    parser.add_argument(
        "--request-surface-balance-tolerance",
        type=float,
        default=0.02,
        help="Maximum absolute deviation from the 50/50 WebApp/Telegram request mix.",
    )
    parser.add_argument(
        "--wave-immediate-actions",
        action="store_true",
        help=(
            "legacy behaviour: trade/expire offers right after creation. "
            "Default is the realistic mode where actions wait for the channel post."
        ),
    )
    parser.add_argument(
        "--wave-action-delay-seconds",
        type=float,
        default=45.0,
        help="base delay between offer creation and the simulated user reaction",
    )
    parser.add_argument("--wave-trade-percent", type=int, default=40)
    parser.add_argument("--wave-manual-expire-percent", type=int, default=20)
    parser.add_argument(
        "--wave-publish-wait-timeout-seconds",
        type=float,
        default=1800.0,
        help="deferred action gives up waiting for the channel post after this long",
    )
    parser.add_argument(
        "--wave-action-drain-timeout-seconds",
        type=float,
        default=2400.0,
        help="after the last create, keep draining deferred actions this long",
    )
    parser.add_argument(
        "--queue-offer-expiry-minutes",
        type=int,
        default=2,
        help=(
            "Expected real staging offer lifetime. The gate reads both servers "
            "and refuses a mismatch; default is the product value of two minutes."
        ),
    )
    parser.add_argument(
        "--allow-temporary-queue-expiry-override",
        action="store_true",
        help=(
            "Infrastructure-only diagnostic: temporarily extend offer lifetime "
            "to fit a larger queue wave, then restore it. Never enabled by default."
        ),
    )
    parser.add_argument(
        "--wave-timeout-seconds",
        type=float,
        default=5400.0,
        help=(
            "Per-side wave docker/ssh timeout. Deferred-action waves need "
            "creates (up to 1800s realtime) plus the action drain window."
        ),
    )
    parser.add_argument("--ot-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--market-timeout-seconds", type=float, default=3600.0)
    parser.add_argument(
        "--defer-live-estimate-reason",
        default=None,
        help=(
            "Record that the real live-market estimator gate is intentionally "
            "deferred (for example because the market is closed). Deterministic "
            "estimate contract hooks still run and are not reported as live evidence."
        ),
    )
    parser.add_argument("--comprehensive-market-users", type=int, default=500)
    parser.add_argument(
        "--comprehensive-market-attempts-per-scenario",
        type=int,
        default=40,
    )
    parser.add_argument(
        "--comprehensive-market-target-rps",
        type=float,
        default=200.0,
    )
    parser.add_argument(
        "--comprehensive-market-write-max-concurrency",
        type=int,
        default=24,
    )
    parser.add_argument(
        "--comprehensive-market-timeout-seconds",
        type=float,
        default=5400.0,
    )
    parser.add_argument(
        "--market-driver-limit",
        type=int,
        default=0,
        help="S2FM driver count; 0 means all DRIVER_SCENARIOS (full market coverage).",
    )
    parser.add_argument(
        "--market-driver-id",
        action="append",
        default=None,
        help="Optional S2FM driver scenario id(s); default is the first N drivers.",
    )
    parser.add_argument("--parity-mode", choices=("quick", "deep"), default="quick")
    parser.add_argument("--iran-base-url", default=os.getenv("STAGING_IRAN_BASE_URL", DEFAULT_IRAN_BASE_URL))
    parser.add_argument(
        "--foreign-base-url",
        default=os.getenv("STAGING_FOREIGN_BASE_URL", DEFAULT_FOREIGN_BASE_URL),
    )
    parser.add_argument("--basic-auth-user", default=os.getenv("STAGING_BASIC_AUTH_USER"))
    parser.add_argument("--basic-auth-password", default=os.getenv("STAGING_BASIC_AUTH_PASSWORD"))
    parser.add_argument("--observability-api-key", default=os.getenv("STAGING_OBSERVABILITY_API_KEY"))
    parser.add_argument("--expected-release-sha", default=os.getenv("STAGING_EXPECTED_RELEASE_SHA"))
    parser.add_argument(
        "--expected-branch",
        default=os.getenv("STAGING_EXPECTED_BRANCH", DEFAULT_EXPECTED_BRANCH),
    )
    parser.add_argument("--iran-ssh-host", default=os.getenv("STAGING_IRAN_SSH_HOST", DEFAULT_IRAN_SSH_HOST))
    parser.add_argument("--iran-ssh-port", default=os.getenv("STAGING_IRAN_SSH_PORT", DEFAULT_IRAN_SSH_PORT))
    parser.add_argument("--iran-workdir", default=os.getenv("STAGING_IRAN_WORKDIR", DEFAULT_IRAN_WORKDIR))
    parser.add_argument(
        "--iran-app-container",
        default=os.getenv("STAGING_IRAN_APP_CONTAINER", DEFAULT_IRAN_APP_CONTAINER),
    )
    parser.add_argument(
        "--foreign-app-container",
        default=os.getenv("STAGING_FOREIGN_APP_CONTAINER", DEFAULT_FOREIGN_APP_CONTAINER),
    )
    parser.add_argument("--snapshot-host-path", default=os.getenv("STAGING_COMBINED_SNAPSHOT_PATH", DEFAULT_SNAPSHOT_HOST))
    parser.add_argument("--snapshot-container-path", default=DEFAULT_SNAPSHOT_CONTAINER)
    args = parser.parse_args(argv)
    if args.run_prefix is None:
        # Keep one CMB_ head even when --run-id already starts with CMB-...
        rid = str(args.run_id).strip().replace("-", "_")
        if rid.startswith("CMB_"):
            args.run_prefix = rid
        elif rid.startswith("CMB"):
            args.run_prefix = f"CMB_{rid[3:].lstrip('_')}"
        else:
            args.run_prefix = f"CMB_{rid}"
    if not str(args.run_prefix).startswith("CMB_"):
        raise SystemExit("run prefix must start with CMB_")
    if args.artifact_dir is None:
        args.artifact_dir = DEFAULT_ARTIFACT_ROOT / args.run_id
    if not 0.0 < float(args.wave_scale) <= 1.0:
        raise SystemExit("wave scale must be greater than 0 and at most 1.0")
    if float(args.wave_scale) < 1.0 and not args.wave_reduction_reason:
        args.wave_reduction_reason = (
            "controlled staging combined-matrix budget "
            f"(scale={args.wave_scale}); full schedule hash retained"
        )
    if int(args.market_driver_limit) <= 0:
        # Keep plan mode free of runtime-heavy imports. Execution resolves the
        # full driver count lazily in run_market_drivers.
        args.market_driver_limit = 0
    if args.wave_profile == "realtime-30m" and float(args.wave_speed) != 1.0:
        raise SystemExit("realtime-30m requires --wave-speed 1.0")
    if args.mode == "execute" and args.wave_immediate_actions:
        raise SystemExit(
            "--wave-immediate-actions is diagnostic-only and cannot be used by the gate"
        )
    if int(args.wave_trade_percent) < 0 or int(args.wave_manual_expire_percent) < 0:
        raise SystemExit("wave action percentages cannot be negative")
    if (
        int(args.wave_trade_percent) + int(args.wave_manual_expire_percent)
        > 100
    ):
        raise SystemExit("wave action percentages cannot exceed 100 combined")
    if not 0.0 <= float(args.request_surface_balance_tolerance) <= 0.5:
        raise SystemExit("request surface balance tolerance must be between 0 and 0.5")
    if float(args.queue_slo_max_edit_provider_p95_seconds) <= 0:
        raise SystemExit("edit provider P95 SLO must be positive")
    if (
        args.queue_slo_max_slow_edit_responses is not None
        and int(args.queue_slo_max_slow_edit_responses) < 0
    ):
        raise SystemExit("slow edit response SLO cannot be negative")
    if not 0.0 <= float(args.queue_slo_max_slow_edit_ratio) <= 1.0:
        raise SystemExit("slow edit response ratio must be between 0 and 1")
    if int(args.comprehensive_market_users) < 2:
        raise SystemExit("comprehensive market matrix needs at least two users")
    if int(args.comprehensive_market_attempts_per_scenario) < 40:
        raise SystemExit(
            "comprehensive market matrix requires at least 40 attempts per scenario"
        )
    if float(args.comprehensive_market_target_rps) <= 0:
        raise SystemExit("comprehensive market target RPS must be positive")
    if float(args.comprehensive_market_timeout_seconds) <= 0:
        raise SystemExit("comprehensive market timeout must be positive")
    # S2FM expects user@host; normalize bare IP/hostname.
    if args.iran_ssh_host and "@" not in str(args.iran_ssh_host):
        args.iran_ssh_host = f"root@{args.iran_ssh_host}"
    return args


def build_live_coverage_report(
    *,
    manifest: dict[str, Any],
    lanes: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    """Require independent, countable evidence for every mandatory cell."""

    evidence: dict[str, dict[str, Any]] = {
        cell: {
            "status": "failed",
            "expected": {"minimum_evidence": 1},
            "actual": {"evidence_count": 0},
            "identifiers": [],
            "sources": [],
        }
        for cell in manifest_builder.MANDATORY_CELLS
    }

    def record(
        cell: str,
        *,
        passed: bool,
        expected: Any,
        actual: Any,
        identifiers: list[Any],
        source: str,
    ) -> None:
        if cell not in evidence:
            return
        current = evidence[cell]
        # A cell may have alternative independent sources (for example actor
        # guards and market drivers). Preserve every source and pass when at
        # least one complete source proves the cell.
        current["sources"].append(
            {
                "source": source,
                "passed": bool(passed),
                "expected": expected,
                "actual": actual,
                "identifiers": identifiers,
            }
        )
        current["identifiers"] = sorted(
            {
                str(value)
                for item in current["sources"]
                for value in item.get("identifiers") or []
            }
        )
        current["status"] = (
            "passed"
            if any(item.get("passed") for item in current["sources"])
            else "failed"
        )
        current["expected"] = expected
        current["actual"] = actual

    market_results = (lanes.get("market") or {}).get("scenario_results") or []
    passed_market_ids = {
        str(item.get("scenario_id") or item.get("manifest_id"))
        for item in market_results
        if item.get("status") == "passed"
    }
    for cell in (
        item
        for item in manifest_builder.MANDATORY_CELLS
        if item.startswith("market:")
        and not item.startswith("market:comprehensive:")
        and item != "market:request_surface:balanced"
    ):
        expected_ids = sorted(
            str(scenario["id"])
            for scenario in _driver_scenarios()
            if cell in (scenario.get("manifest_cells") or [])
        )
        actual_ids = sorted(set(expected_ids) & passed_market_ids)
        if expected_ids:
            record(
                cell,
                passed=bool(actual_ids),
                expected={"minimum_passed": 1, "eligible_scenarios": expected_ids},
                actual={"passed_count": len(actual_ids)},
                identifiers=actual_ids,
                source="market_driver_suite",
            )

    comprehensive_results = (
        (lanes.get("market_comprehensive") or {}).get("scenario_results") or []
    )
    comprehensive_passed = {
        str(item.get("scenario_id"))
        for item in comprehensive_results
        if item.get("status") == "ok"
    }
    comprehensive_all_ids = {
        f"CLM-{index:03d}"
        for index in range(
            1,
            manifest_builder.COMPREHENSIVE_MARKET_SCENARIO_COUNT + 1,
        )
    }
    record(
        "market:comprehensive:all_228",
        passed=comprehensive_passed == comprehensive_all_ids,
        expected={
            "passed_count": len(comprehensive_all_ids),
            "scenario_ids": sorted(comprehensive_all_ids),
        },
        actual={"passed_count": len(comprehensive_passed)},
        identifiers=sorted(comprehensive_passed),
        source="comprehensive_market_matrix",
    )
    for family, expected_count in (
        manifest_builder.COMPREHENSIVE_MARKET_FAMILY_COUNTS.items()
    ):
        passed_ids = sorted(
            str(item.get("scenario_id"))
            for item in comprehensive_results
            if item.get("status") == "ok" and item.get("family") == family
        )
        record(
            f"market:comprehensive:family:{family}",
            passed=len(passed_ids) == expected_count,
            expected={"passed_count": expected_count, "family": family},
            actual={"passed_count": len(passed_ids)},
            identifiers=passed_ids,
            source="comprehensive_market_matrix",
        )

    actor_payload = (lanes.get("actor_guards") or {}).get("payload") or {}
    actor_cases = {
        str(item.get("case_id") or ""): item
        for item in actor_payload.get("cases") or []
    }
    actor_cell_cases = {
        "market:actor:tier1_customer": ("tier1_customer_success",),
        "market:actor:tier2_customer": (
            "tier2_offer_creation",
            "tier2_telegram_request",
        ),
        "market:terminal:rejected": (
            "tier2_offer_creation",
            "tier2_telegram_request",
            "invalid_request_amount",
            "own_offer_request",
        ),
    }
    for cell, required_cases in actor_cell_cases.items():
        statuses = {
            case_id: bool((actor_cases.get(case_id) or {}).get("ok"))
            for case_id in required_cases
        }
        record(
            cell,
            passed=bool(statuses) and all(statuses.values()),
            expected={"required_cases": list(required_cases)},
            actual={"case_statuses": statuses},
            identifiers=[
                case_id for case_id, passed in statuses.items() if passed
            ],
            source="actor_guards",
        )

    wave_assertions = (lanes.get("queue_wave") or {}).get("assertions") or []
    assertions_by_cell: dict[str, list[dict[str, Any]]] = {}
    for assertion in wave_assertions:
        for cell in assertion.get("cells") or []:
            assertions_by_cell.setdefault(str(cell), []).append(assertion)
    for cell, assertions in assertions_by_cell.items():
        passed_ids = [
            str(item.get("name")) for item in assertions if item.get("passed")
        ]
        expected_ids = [str(item.get("name")) for item in assertions]
        record(
            cell,
            passed=bool(assertions)
            and len(passed_ids) == len(assertions),
            expected={
                "assertion_count": len(assertions),
                "assertions": expected_ids,
            },
            actual={"passed_count": len(passed_ids)},
            identifiers=passed_ids,
            source="queue_wave_assertions",
        )

    estimate_payload = (lanes.get("estimate") or {}).get("payload") or {}
    for item in estimate_payload.get("checks") or []:
        cell = str(item.get("cell") or "")
        record(
            cell,
            passed=bool(item.get("passed")),
            expected={"passed": True},
            actual={"passed": bool(item.get("passed")), "status": item.get("status")},
            identifiers=[cell] if item.get("passed") else [],
            source="estimate_hook",
        )

    overtime_results = (lanes.get("overtime") or {}).get("scenario_results") or []
    passed_overtime_ids = {
        str(item.get("id"))
        for item in overtime_results
        if item.get("status") == "passed"
    }
    overtime_by_cell: dict[str, set[str]] = {}
    for scenario_id, cell in manifest_builder.OT_FAMILIES:
        overtime_by_cell.setdefault(cell, set()).add(scenario_id)
    for cell, expected_set in overtime_by_cell.items():
        actual_ids = sorted(expected_set & passed_overtime_ids)
        expected_ids = sorted(expected_set)
        record(
            cell,
            passed=actual_ids == expected_ids,
            expected={
                "passed_count": len(expected_ids),
                "scenario_ids": expected_ids,
            },
            actual={"passed_count": len(actual_ids)},
            identifiers=actual_ids,
            source="overtime_scenarios",
        )

    missing = [
        cell
        for cell in manifest_builder.MANDATORY_CELLS
        if evidence[cell]["status"] != "passed"
    ]
    return {
        "ok": not missing,
        "mandatory_cell_count": len(manifest_builder.MANDATORY_CELLS),
        "covered_cell_count": len(manifest_builder.MANDATORY_CELLS) - len(missing),
        "missing_cells": missing,
        "evidence": evidence,
        "artifact_dir": str(artifact_dir),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "plan":
        payload = build_plan(args)
        print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
        return 0 if payload["summary"]["status"] == "plan_ready" else 1
    if args.mode == "preflight":
        payload, code = run_preflight(args)
        print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
        return code
    payload, code = run_execute(args)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
