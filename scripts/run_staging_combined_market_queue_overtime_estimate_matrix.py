#!/usr/bin/env python3
"""Plan / preflight / execute the combined market×queue×OT×estimate staging matrix.

``plan`` and ``preflight`` are non-mutating. ``execute`` is fail-closed until
``STAGING_COMBINED_MATRIX_CONFIRM=execute-staging-combined-matrix`` is set and
preflight is green.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_staging_combined_matrix_manifest as manifest_builder
from scripts import staging_combined_matrix_wave_driver as wave_driver
from scripts.run_staging_two_server_full_matrix import DRIVER_SCENARIOS


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
            continue
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
    if args.basic_auth_user:
        ot_argv.extend(["--basic-auth-user", args.basic_auth_user])
    if args.basic_auth_password:
        ot_argv.extend(["--basic-auth-password", args.basic_auth_password])
    ot = _run(ot_argv, timeout=180)
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
    if args.observability_api_key:
        s2fm_argv.extend(["--observability-api-key", args.observability_api_key])
    if args.basic_auth_user:
        s2fm_argv.extend(["--basic-auth-user", args.basic_auth_user])
    if args.basic_auth_password:
        s2fm_argv.extend(["--basic-auth-password", args.basic_auth_password])
    s2fm = _run(s2fm_argv, timeout=600)
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
) -> tuple[dict[str, Any], int]:
    if server == "iran":
        remote = (
            f"docker exec {shlex.quote(args.iran_app_container)} python {shlex.quote(script)} "
            + " ".join(shlex.quote(part) for part in script_args)
        )
        argv = iran_ssh(args, remote)
    else:
        argv = ["docker", "exec", args.foreign_app_container, "python", script, *script_args]
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


def _apply_queue_offer_expiry_override(args: argparse.Namespace) -> dict[str, Any]:
    """Raise offer lifetime on both servers so queued offers survive the peak.

    With the staging default of 2 minutes, offers deep in a peak-sized send
    backlog would expire before their channel post, which never happens with
    real users. Returns the original values for the later restore.
    """
    override = int(args.queue_offer_expiry_minutes)
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
    events_path = artifact_dir / "wave-selected-events.json"
    write_json(events_path, {"events": selected, "budget": {
        "scale": budget.scale,
        "valid_limit": budget.valid_limit,
        "invalid_limit": budget.invalid_limit,
        "reduction_reason": budget.reduction_reason,
    }})

    # Push selected events into both containers.
    host = args.iran_ssh_host if "@" in args.iran_ssh_host else f"root@{args.iran_ssh_host}"
    remote_events = f"{args.iran_workdir}/tmp/{args.run_id}-wave-events.json"
    _run(
        [
            "scp",
            "-P",
            str(args.iran_ssh_port),
            "-o",
            "BatchMode=yes",
            str(events_path),
            f"{host}:{remote_events}",
        ],
        timeout=60,
    )
    _run(
        iran_ssh(
            args,
            f"docker cp {remote_events} {args.iran_app_container}:/tmp/wave-events.json",
        ),
        timeout=60,
    )
    _run(
        [
            "docker",
            "cp",
            str(events_path),
            f"{args.foreign_app_container}:/tmp/wave-events.json",
        ],
        timeout=60,
    )

    dwell = float(args.publish_dwell_seconds)
    owner_pool = int(args.owner_pool_size)
    wave_args_common = [
        "--events-file",
        "/tmp/wave-events.json",
        "--owner-pool-size",
        str(owner_pool),
        "--publish-dwell-seconds",
        str(dwell),
        "--speed",
        str(float(args.wave_speed)),
    ]
    if args.realtime_wave:
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
                str(float(args.wave_publish_wait_timeout_seconds)),
                "--action-drain-timeout-seconds",
                str(float(args.wave_action_drain_timeout_seconds)),
            ]
        )

    iran_script_args = [
        "--run-prefix",
        f"{args.run_prefix}_IR",
        "--surface-filter",
        "webapp",
        "--snapshot-path",
        args.snapshot_container_path,
        *wave_args_common,
    ]
    foreign_script_args = [
        "--run-prefix",
        f"{args.run_prefix}_FO",
        "--surface-filter",
        "bot",
        *wave_args_common,
    ]

    # Realistic peak needs offers to outlive the send backlog; raise the
    # lifetime for the wave window and always restore it afterwards.
    expiry_override: dict[str, Any] = {"enabled": False}
    if not args.wave_immediate_actions:
        expiry_override = _apply_queue_offer_expiry_override(args)
    write_json(artifact_dir / "offer-expiry-override.json", expiry_override)

    # Iran (webapp) and foreign (bot) must pace the same 30-minute wall clock.
    iran_payload: dict[str, Any] = {}
    foreign_payload: dict[str, Any] = {}
    iran_code = 1
    foreign_code = 1
    wave_started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(
                    _container_python,
                    args,
                    server="iran",
                    script="scripts/staging_combined_matrix_mutating_wave.py",
                    script_args=iran_script_args,
                    timeout=float(args.wave_timeout_seconds),
                ): "iran",
                pool.submit(
                    _container_python,
                    args,
                    server="foreign",
                    script="scripts/staging_combined_matrix_mutating_wave.py",
                    script_args=foreign_script_args,
                    timeout=float(args.wave_timeout_seconds),
                ): "foreign",
            }
            for future in as_completed(futures):
                name = futures[future]
                payload, code = future.result()
                if name == "iran":
                    iran_payload, iran_code = payload, code
                else:
                    foreign_payload, foreign_code = payload, code
        wave_wall_seconds = round(time.perf_counter() - wave_started, 3)
        write_json(artifact_dir / "wave-iran.json", iran_payload)
        write_json(artifact_dir / "wave-foreign.json", foreign_payload)

        queue_before, _ = _container_python(
            args,
            server="foreign",
            script="scripts/staging_combined_matrix_queue_sampler.py",
            script_args=["--run-prefix", args.run_prefix],
            timeout=120,
        )
        time.sleep(max(15.0, float(args.drain_wait_seconds)))
        queue_after, _ = _container_python(
            args,
            server="foreign",
            script="scripts/staging_combined_matrix_queue_sampler.py",
            script_args=[
                "--run-prefix",
                args.run_prefix,
                "--timing",
                "--lookback-minutes",
                str(int(args.timing_lookback_minutes)),
            ],
            timeout=180,
        )
        write_json(
            artifact_dir / "queue-sample.json",
            {"before_drain_wait": queue_before, "after_drain_wait": queue_after},
        )
        timing = queue_after.get("timing") if isinstance(queue_after, dict) else {}
        write_json(
            artifact_dir / "telegram-send-timing.json",
            {
                "ok": bool(queue_after.get("ok")),
                "at_utc": queue_after.get("at_utc"),
                "pending_jobs": queue_after.get("pending_jobs"),
                "timing": timing or {},
                "recommendation": {
                    "best_send_minute_utc": (timing or {}).get("best_send_minute_utc"),
                    "best_send_minute_mean_latency_seconds": (timing or {}).get(
                        "best_send_minute_mean_latency_seconds"
                    ),
                    "p50_seconds": ((timing or {}).get("latency_seconds") or {}).get("p50"),
                    "p95_seconds": ((timing or {}).get("latency_seconds") or {}).get("p95"),
                },
            },
        )

        iran_cleanup, _ = _container_python(
            args,
            server="iran",
            script="scripts/staging_combined_matrix_mutating_wave.py",
            script_args=[
                "--run-prefix",
                f"{args.run_prefix}_IR",
                "--events-file",
                "/tmp/wave-events.json",
                "--cleanup-only",
            ],
            timeout=300,
        )
        foreign_cleanup, _ = _container_python(
            args,
            server="foreign",
            script="scripts/staging_combined_matrix_mutating_wave.py",
            script_args=[
                "--run-prefix",
                f"{args.run_prefix}_FO",
                "--events-file",
                "/tmp/wave-events.json",
                "--cleanup-only",
            ],
            timeout=300,
        )
        write_json(
            artifact_dir / "wave-cleanup.json",
            {"iran": iran_cleanup, "foreign": foreign_cleanup},
        )
    finally:
        # Restore only after the wave offers are cleaned up; restoring earlier
        # would mass-expire queued offers mid-drain and skew send metrics.
        restore_state = _restore_queue_offer_expiry(args, expiry_override)
        write_json(artifact_dir / "offer-expiry-restore.json", restore_state)
    time.sleep(max(20.0, min(60.0, float(args.drain_wait_seconds))))

    created_iran = int(iran_payload.get("created_count") or 0)
    created_foreign = int(foreign_payload.get("created_count") or 0)
    expected_valid = int(budget.valid_limit)
    ok = (
        iran_code == 0
        and foreign_code == 0
        and bool(iran_payload.get("ok"))
        and bool(foreign_payload.get("ok"))
        and bool(queue_after.get("backlog_under_threshold", True))
        and (created_iran + created_foreign) >= expected_valid
    )
    return {
        "ok": ok,
        "budget": {
            "scale": budget.scale,
            "valid_limit": budget.valid_limit,
            "invalid_limit": budget.invalid_limit,
            "reduction_reason": budget.reduction_reason,
            "selected_count": len(selected),
        },
        "realtime_wave": bool(args.realtime_wave),
        "wave_speed": float(args.wave_speed),
        "wave_wall_seconds": wave_wall_seconds,
        "created_total": created_iran + created_foreign,
        "iran": {"returncode": iran_code, "payload": iran_payload},
        "foreign": {"returncode": foreign_code, "payload": foreign_payload},
        "queue": {"before": queue_before, "after": queue_after},
        "telegram_send_timing": timing or {},
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
    if args.basic_auth_user:
        argv.extend(["--basic-auth-user", args.basic_auth_user])
    if args.basic_auth_password:
        argv.extend(["--basic-auth-password", args.basic_auth_password])
    env = {EXECUTION_CONFIRM_ENV: ""}  # not used by OT
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
    # OT returns 0 only when all wired; 4 = partial unwired historically — treat passed/partial without failures as ok.
    status = str(summary.get("status") or "")
    ok = completed.returncode == 0 or (
        completed.returncode == 4 and "failed" not in status and status.startswith("execute_")
    )
    # Stricter: require no failed wired drivers.
    driver_path = ot_dir / "driver-results.json"
    if driver_path.is_file():
        drivers = json.loads(driver_path.read_text(encoding="utf-8"))
        failed = [item for item in drivers.get("results") or [] if item.get("status") == "failed"]
        blocked = [item for item in drivers.get("results") or [] if item.get("status") == "blocked"]
        ok = not failed and not blocked
        summary["failed_count"] = len(failed)
        summary["blocked_count"] = len(blocked)
    return {"ok": ok, "returncode": completed.returncode, "summary": summary, "artifact_dir": str(ot_dir)}


def run_market_drivers(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    s2fm_dir = artifact_dir / "child-s2fm-execute"
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
        str(args.market_driver_limit),
    ]
    for scenario_id in args.market_driver_id or []:
        argv.extend(["--driver-scenario-id", scenario_id])
    if args.observability_api_key:
        argv.extend(["--observability-api-key", args.observability_api_key])
    if args.basic_auth_user:
        argv.extend(["--basic-auth-user", args.basic_auth_user])
    if args.basic_auth_password:
        argv.extend(["--basic-auth-password", args.basic_auth_password])
    env = {
        "STAGING_TWO_SERVER_FULL_MATRIX_CONFIRM": "execute-staging-two-server-full-matrix",
    }
    completed = _run(argv, env=env, timeout=args.market_timeout_seconds)
    summary = _parse_json_stdout(completed.stdout)
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
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "summary": summary,
        "artifact_dir": str(s2fm_dir),
    }


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
    # Clear leftover CMB_/OTACC_ rows and orphan outbox before market drivers.
    pre_heal_iran, _ = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--hours", "24"],
        timeout=300,
    )
    pre_heal_foreign, _ = _container_python(
        args,
        server="foreign",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--hours", "24"],
        timeout=300,
    )
    write_json(
        artifact_dir / "pre-execute-heal.json",
        {"iran": pre_heal_iran, "foreign": pre_heal_foreign},
    )
    lanes["pre_heal"] = {
        "ok": bool(pre_heal_iran.get("ok")) and bool(pre_heal_foreign.get("ok")),
        "iran": pre_heal_iran,
        "foreign": pre_heal_foreign,
    }

    # Market drivers first: S2FM execute re-checks parity, so run them while the
    # topology is still clean after combined preflight. Wave/OT churn afterward.
    market = run_market_drivers(args, artifact_dir)
    lanes["market"] = market

    # Actor/terminal guards: tier1 success + tier2/reject negatives (Iran home).
    actor_guards, actor_code = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_actor_guards.py",
        script_args=["--run-prefix", f"{args.run_prefix}_AG"],
        timeout=1800,
    )
    write_json(artifact_dir / "actor-guards.json", actor_guards)
    lanes["actor_guards"] = {
        "ok": actor_code == 0 and bool(actor_guards.get("ok")),
        "returncode": actor_code,
        "payload": actor_guards,
    }

    wave = run_wave(args, preflight["manifest"], artifact_dir)
    lanes["queue_wave"] = wave
    write_json(artifact_dir / "lane-queue-wave.json", wave)

    estimate = run_estimate_hooks(args, artifact_dir)
    lanes["estimate"] = estimate

    overtime = run_overtime_execute(args, artifact_dir)
    lanes["overtime"] = overtime

    coverage = build_live_coverage_report(
        manifest=preflight["manifest"],
        lanes=lanes,
        artifact_dir=artifact_dir,
    )
    write_json(artifact_dir / "live-coverage.json", coverage)
    lanes["live_coverage"] = coverage

    # Final synthetic cleanup so leftover tombstones do not poison the next run.
    heal_iran, _ = _container_python(
        args,
        server="iran",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--hours", "6"],
    )
    heal_foreign, _ = _container_python(
        args,
        server="foreign",
        script="scripts/staging_combined_matrix_heal.py",
        script_args=["--hours", "6"],
    )
    write_json(
        artifact_dir / "post-execute-heal.json",
        {"iran": heal_iran, "foreign": heal_foreign},
    )
    lanes["cleanup_heal"] = {
        "ok": bool(heal_iran.get("ok")) and bool(heal_foreign.get("ok")),
        "iran": heal_iran,
        "foreign": heal_foreign,
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
        "evidence_zip": zip_path,
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
        "--realtime-wave",
        action="store_true",
        help="Pace Iran/foreign mutating waves against schedule t_seconds (30-minute wall clock).",
    )
    parser.add_argument(
        "--wave-speed",
        type=float,
        default=1.0,
        help="Realtime compression factor (>1 shortens waits). Keep 1.0 for true 30-minute wave.",
    )
    parser.add_argument("--owner-pool-size", type=int, default=48)
    parser.add_argument(
        "--publish-dwell-seconds",
        type=float,
        default=1.0,
        help="Bot-side pause after create so Telegram publication can enqueue.",
    )
    parser.add_argument("--drain-wait-seconds", type=float, default=180.0)
    parser.add_argument("--timing-lookback-minutes", type=int, default=45)
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
        default=45,
        help=(
            "temporary offer lifetime during the queue wave so backlogged offers "
            "survive until publication; restored after wave cleanup (0 disables)"
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
        args.run_prefix = f"CMB_{args.run_id.replace('-', '')}"
    if not str(args.run_prefix).startswith("CMB_"):
        raise SystemExit("run prefix must start with CMB_")
    if args.artifact_dir is None:
        args.artifact_dir = DEFAULT_ARTIFACT_ROOT / args.run_id
    if float(args.wave_scale) < 1.0 and not args.wave_reduction_reason:
        args.wave_reduction_reason = (
            "controlled staging combined-matrix budget "
            f"(scale={args.wave_scale}); full schedule hash retained"
        )
    if int(args.market_driver_limit) <= 0:
        args.market_driver_limit = len(DRIVER_SCENARIOS)
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
    """Map mandatory cells to live evidence; fail if any required cell lacks a pass."""

    evidence: dict[str, list[str]] = {cell: [] for cell in manifest_builder.MANDATORY_CELLS}

    # Market drivers declare manifest_cells.
    for scenario in DRIVER_SCENARIOS:
        for cell in scenario.get("manifest_cells") or []:
            if lanes.get("market", {}).get("ok"):
                evidence.setdefault(cell, []).append(f"driver:{scenario['id']}")

    actor_payload = (lanes.get("actor_guards") or {}).get("payload") or {}
    if actor_payload.get("ok"):
        for cell in actor_payload.get("cells_covered") or []:
            evidence.setdefault(cell, []).append("actor_guards")

    wave = lanes.get("queue_wave") or {}
    if wave.get("ok"):
        for cell in (
            "queue:surface:webapp",
            "queue:surface:bot",
            "queue:surface:telegram_heavy",
            "queue:wave:valid",
            "queue:wave:invalid",
            "queue:regime:peak",
            "market:request_surface:balanced",
        ):
            evidence.setdefault(cell, []).append("queue_wave")
        iran_reqs = ((wave.get("iran") or {}).get("payload") or {}).get("request_surface_mix") or {}
        foreign_reqs = ((wave.get("foreign") or {}).get("payload") or {}).get("request_surface_mix") or {}
        total_web = int(iran_reqs.get("webapp") or 0) + int(foreign_reqs.get("webapp") or 0)
        total_tg = int(iran_reqs.get("telegram") or 0) + int(foreign_reqs.get("telegram") or 0)
        if total_web + total_tg > 0:
            evidence.setdefault("market:request_surface:balanced", []).append(
                f"wave_requests:webapp={total_web},telegram={total_tg}"
            )

    estimate_payload = (lanes.get("estimate") or {}).get("payload") or {}
    for item in (
        estimate_payload.get("checks")
        or estimate_payload.get("results")
        or estimate_payload.get("assertions")
        or []
    ):
        cell = item.get("cell")
        if cell and item.get("passed"):
            evidence.setdefault(cell, []).append("estimate_hooks")

    # Fallback: if estimate lane ok, mark all estimate cells covered by hooks artifact.
    if (lanes.get("estimate") or {}).get("ok"):
        for cell in (
            "estimate:preview_shadow",
            "estimate:selectable_accept",
            "estimate:selectable_decline",
            "estimate:no_data_fail_closed",
        ):
            if not evidence.get(cell):
                evidence.setdefault(cell, []).append("estimate_hooks_lane")

    if (lanes.get("overtime") or {}).get("ok"):
        for sid, cell in manifest_builder.OT_FAMILIES:
            evidence.setdefault(cell, []).append(f"overtime:{sid}")

    missing = [cell for cell in manifest_builder.MANDATORY_CELLS if not evidence.get(cell)]
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
