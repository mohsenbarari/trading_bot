#!/usr/bin/env python3
"""Orchestrate Stage L realistic mixed k6 load-test harness."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, quote_remote, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings
from scripts.report_production_load_fixtures import (
    build_scp_opts,
    build_ssh_opts,
    command_display,
    parse_last_json,
    redact_payload,
    require_sshpass_if_needed,
    scp_base_command,
    ssh_base_command,
)


K6_SCRIPT = REPO_ROOT / "scripts" / "load" / "k6_realistic_mix.js"


SCENARIO_CONTRACT = [
    {
        "persona": "market_watcher",
        "weight": 25,
        "endpoints": [
            "GET /api/trading-settings",
            "GET /api/trading-settings/market-state",
            "GET /api/commodities",
            "GET /api/offers",
            "GET /api/trades/my",
            "GET /api/notifications/unread-count",
        ],
    },
    {
        "persona": "offer_maker",
        "weight": 10,
        "endpoints": ["GET /api/offers", "GET /api/offers/my", "POST /api/offers"],
    },
    {
        "persona": "trade_taker",
        "weight": 10,
        "endpoints": ["GET /api/offers", "GET /api/trades/my", "GET /api/trades/{trade_id}", "POST /api/trades"],
    },
    {
        "persona": "chat_texter",
        "weight": 20,
        "endpoints": [
            "GET /api/chat/conversations",
            "GET /api/chat/messages/{user_id}",
            "GET /api/chat/rooms/{chat_id}/messages",
            "POST /api/chat/send",
            "POST /api/chat/rooms/{chat_id}/send",
            "GET /api/chat/poll",
        ],
    },
    {
        "persona": "chat_media_sender",
        "weight": 8,
        "endpoints": [
            "POST /api/chat/upload-batches",
            "POST /api/chat/upload-sessions",
            "PATCH /api/chat/upload-sessions/{session_id}/chunk",
            "POST /api/chat/upload-sessions/{session_id}/finalize",
            "POST /api/chat/upload-batches/{batch_id}/commit",
        ],
    },
    {
        "persona": "profile_browser",
        "weight": 15,
        "endpoints": [
            "GET /api/auth/me",
            "GET /api/users-public/search",
            "GET /api/users-public/{user_id}",
            "GET /api/users-public/{user_id}/project-users",
            "GET /api/customers/owner-relations",
            "GET /api/accountants/owner-relations",
        ],
    },
    {
        "persona": "notification_user",
        "weight": 7,
        "endpoints": [
            "GET /api/notifications",
            "GET /api/notifications/unread",
            "GET /api/notifications/unread-count",
            "PATCH /api/notifications/{notification_id}/read",
            "POST /api/notifications/mark-all-read",
        ],
    },
    {
        "persona": "admin_light_read",
        "weight": 5,
        "endpoints": [
            "GET /api/users",
            "GET /api/admin-messages/market/current",
            "GET /api/admin-messages/market/history",
            "GET /api/admin-messages/broadcasts/history",
            "GET /api/trading-settings/market-overrides",
        ],
    },
]

THRESHOLD_CONTRACT = {
    "http_req_failed": ["rate<0.02"],
    "http_req_duration": ["p(95)<1000", "p(99)<2500"],
    "checks": ["rate>0.98"],
    "stage_l_request_failed": ["rate<0.02"],
    "http_req_duration{persona:market_watcher}": ["p(95)<800"],
    "http_req_duration{persona:chat_texter}": ["p(95)<1200"],
    "http_req_duration{persona:profile_browser}": ["p(95)<1200"],
}

REMAINING_STAGES = [
    "L5 - Smoke Run",
    "L6 - Warmup Ramp",
    "L7 - Official Target Run",
    "L8 - Spike Run",
    "L9 - Soak Run",
    "L10 - Analysis and Bottleneck Classification",
    "L11 - Release Capacity Decision",
]


class RealisticLoadError(RuntimeError):
    pass


@dataclass
class CommandResult:
    name: str
    args: list[str]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


@dataclass
class SamplerProcess:
    process: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path
    artifact_dir: Path
    command: list[str]


DURATION_UNITS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_duration_seconds(value: str) -> float:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("duration is required")
    unit = raw[-1]
    if unit in DURATION_UNITS:
        number = raw[:-1]
        if not number:
            raise ValueError(f"invalid duration: {value}")
        return float(number) * DURATION_UNITS[unit]
    return float(raw)


def target_url(settings: dict[str, str], explicit: str | None) -> str:
    return (explicit or settings.get("IRAN_SERVER_URL") or settings.get("IRAN_FRONTEND_URL") or "").rstrip("/")


class Runner:
    def __init__(self, *, logs_dir: Path):
        self.logs_dir = logs_dir
        self.commands: list[dict[str, Any]] = []

    def run(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int,
        check: bool = True,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.perf_counter()
        stdout_path = self.logs_dir / f"{name}.stdout.log"
        stderr_path = self.logs_dir / f"{name}.stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            completed = subprocess.run(
                args,
                cwd=str(REPO_ROOT),
                text=True,
                input=input_text,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout,
                check=False,
                env={**os.environ, **(env or {})},
            )
        duration = round(time.perf_counter() - started, 3)
        result = CommandResult(
            name=name,
            args=args,
            exit_code=completed.returncode,
            duration_seconds=duration,
            stdout=stdout_path.read_text(encoding="utf-8"),
            stderr=stderr_path.read_text(encoding="utf-8"),
        )
        self._record_logs(result, stdout_path=stdout_path, stderr_path=stderr_path)
        if check and result.exit_code != 0:
            raise RealisticLoadError(f"{name} failed with exit code {result.exit_code}")
        return result

    def _record_logs(self, result: CommandResult, *, stdout_path: Path, stderr_path: Path) -> None:
        self.commands.append(
            {
                "name": result.name,
                "command": command_display(result.args),
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "stdout_path": display_path(stdout_path),
                "stderr_path": display_path(stderr_path),
            }
        )


def build_contract(args: argparse.Namespace, settings: dict[str, str] | None = None) -> dict[str, Any]:
    base_url = target_url(settings or {}, args.base_url)
    return {
        "base_url": base_url,
        "target_rps": args.target_rps,
        "duration": args.duration,
        "load_profile": args.load_profile,
        "include_media": args.include_media,
        "include_mutations": args.include_mutations,
        "pre_allocated_vus": args.pre_allocated_vus,
        "max_vus": args.max_vus,
        "load_runner_nofile": args.load_runner_nofile,
        "load_runner_shards": args.load_runner_shards,
        "scenario_contract": SCENARIO_CONTRACT,
        "thresholds": THRESHOLD_CONTRACT,
        "k6_script": display_path(K6_SCRIPT),
    }


def load_runner_env(args: argparse.Namespace) -> dict[str, str] | None:
    if not args.load_runner_password:
        return None
    return {"SSHPASS": args.load_runner_password, "LOAD_RUNNER_PASSWORD": args.load_runner_password}


def remote_paths(args: argparse.Namespace, prefix: str) -> dict[str, str]:
    remote_root = args.load_runner_remote_dir.rstrip("/")
    remote_artifact_dir = f"{remote_root}/artifacts/{prefix}realistic"
    return {
        "root": remote_root,
        "script_dir": f"{remote_root}/scripts",
        "script": f"{remote_root}/scripts/k6_realistic_mix.js",
        "auth_pool": f"{remote_root}/auth/{prefix}auth-pool.json",
        "artifact_dir": remote_artifact_dir,
        "summary": f"{remote_artifact_dir}/k6-summary.json",
    }


def ssh_args(args: argparse.Namespace, command: str) -> list[str]:
    ssh_cmd = ssh_base_command(password=args.load_runner_password)
    ssh_opts = build_ssh_opts(
        host_port=args.load_runner_port,
        jump_host=args.load_runner_jump_host,
        jump_port=args.load_runner_jump_port,
    )
    return [*ssh_cmd, *ssh_opts, args.load_runner_host, command]


def scp_args(args: argparse.Namespace, source: str, destination: str) -> list[str]:
    scp_cmd = scp_base_command(password=args.load_runner_password)
    scp_opts = build_scp_opts(
        host_port=args.load_runner_port,
        jump_host=args.load_runner_jump_host,
        jump_port=args.load_runner_jump_port,
    )
    return [*scp_cmd, *scp_opts, source, destination]


def fixture_command(args: argparse.Namespace, prefix: str, action: str) -> list[str]:
    return [
        "python3",
        "./scripts/report_production_load_fixtures.py",
        "--manifest",
        args.manifest,
        "--action",
        action,
        "--prefix",
        prefix,
        "--load-runner-host",
        args.load_runner_host,
        "--load-runner-port",
        args.load_runner_port,
        "--load-runner-jump-host",
        args.load_runner_jump_host,
        "--load-runner-jump-port",
        args.load_runner_jump_port,
        "--load-runner-remote-dir",
        args.load_runner_remote_dir,
        "--token-minutes",
        str(args.token_minutes),
        "--json",
    ]


def split_rps(total_rps: int, shard_count: int) -> list[int]:
    shard_count = max(1, int(shard_count))
    base = total_rps // shard_count
    remainder = total_rps % shard_count
    return [base + (1 if index < remainder else 0) for index in range(shard_count)]


def shard_vus_plan(
    *,
    total_pre_allocated_vus: int,
    total_max_vus: int,
    rps_by_shard: list[int],
) -> list[dict[str, int]]:
    shard_count = max(1, len(rps_by_shard))
    if shard_count == 1:
        return [{"pre_allocated_vus": total_pre_allocated_vus, "max_vus": total_max_vus}]
    pre_allocated_split = split_rps(total_pre_allocated_vus, shard_count)
    max_vus_split = split_rps(total_max_vus, shard_count)
    plan: list[dict[str, int]] = []
    for index, shard_rps in enumerate(rps_by_shard):
        pre_allocated = max(20, int(shard_rps), int(pre_allocated_split[index]))
        max_vus = max(100, pre_allocated, int(max_vus_split[index]))
        plan.append({"pre_allocated_vus": pre_allocated, "max_vus": max_vus})
    return plan


def k6_exports(
    args: argparse.Namespace,
    contract: dict[str, Any],
    paths: dict[str, str],
    *,
    target_rps: int,
    shard_index: int,
    pre_allocated_vus: int,
    max_vus: int,
) -> str:
    env_parts = {
        "BASE_URL": contract["base_url"],
        "API_PREFIX": "/api",
        "AUTH_POOL_PATH": paths["auth_pool"],
        "TARGET_RPS": str(target_rps),
        "DURATION": args.duration,
        "LOAD_PROFILE": args.load_profile,
        "INCLUDE_MEDIA": "1" if args.include_media else "0",
        "INCLUDE_MUTATIONS": "1" if args.include_mutations else "0",
        "PRE_ALLOCATED_VUS": str(pre_allocated_vus),
        "MAX_VUS": str(max_vus),
        "K6_SHARD_INDEX": str(shard_index),
        "K6_SHARD_COUNT": str(args.load_runner_shards),
    }
    return " ".join(f"{key}={shlex.quote(value)}" for key, value in env_parts.items())


def run_k6_command(args: argparse.Namespace, contract: dict[str, Any], paths: dict[str, str]) -> str:
    shard_count = max(1, int(args.load_runner_shards or 1))
    rps_by_shard = split_rps(args.target_rps, shard_count)
    vus_plan = shard_vus_plan(
        total_pre_allocated_vus=args.pre_allocated_vus,
        total_max_vus=args.max_vus,
        rps_by_shard=rps_by_shard,
    )
    nofile = max(1024, int(args.load_runner_nofile or 1024))
    prelude = (
        f"mkdir -p {quote_remote(paths['artifact_dir'])} {quote_remote(paths['script_dir'])} && "
        f"cd {quote_remote(paths['root'])} && "
        f"ulimit -n {nofile}"
    )
    if shard_count == 1:
        exports = k6_exports(
            args,
            contract,
            paths,
            target_rps=args.target_rps,
            shard_index=1,
            pre_allocated_vus=vus_plan[0]["pre_allocated_vus"],
            max_vus=vus_plan[0]["max_vus"],
        )
        return (
            f"{prelude} && "
            f"{exports} k6 run --summary-export {quote_remote(paths['summary'])} "
            f"{quote_remote(paths['script'])}"
        )

    parts = [prelude, "pids=''"]
    for shard_index, shard_rps in enumerate(rps_by_shard, start=1):
        shard_vus = vus_plan[shard_index - 1]
        shard_summary = f"{paths['artifact_dir']}/k6-summary-shard-{shard_index}.json"
        shard_stdout = f"{paths['artifact_dir']}/k6-stdout-shard-{shard_index}.log"
        shard_stderr = f"{paths['artifact_dir']}/k6-stderr-shard-{shard_index}.log"
        shard_status = f"{paths['artifact_dir']}/k6-status-shard-{shard_index}.txt"
        exports = k6_exports(
            args,
            contract,
            paths,
            target_rps=shard_rps,
            shard_index=shard_index,
            pre_allocated_vus=shard_vus["pre_allocated_vus"],
            max_vus=shard_vus["max_vus"],
        )
        parts.append(
            f"(echo 'starting shard {shard_index}/{shard_count} at {shard_rps} RPS "
            f"with {shard_vus['pre_allocated_vus']}/{shard_vus['max_vus']} VUs'; "
            f"{exports} k6 run --summary-export {quote_remote(shard_summary)} {quote_remote(paths['script'])} "
            f"> {quote_remote(shard_stdout)} 2> {quote_remote(shard_stderr)}; "
            f"rc=\"$?\"; printf '%s\\n' \"$rc\" > {quote_remote(shard_status)}; exit \"$rc\") "
            f'& pids="$pids $!"'
        )
    parts.append("status=0")
    parts.append('for pid in $pids; do wait "$pid" || status=1; done')
    parts.append(f"cat {quote_remote(paths['artifact_dir'])}/k6-status-shard-*.txt")
    parts.append('exit "$status"')
    return "; ".join(parts)


def weighted_avg(values: list[tuple[float, float]]) -> float | None:
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values) / total_weight


def trend_count(metric_name: str, metrics: dict[str, Any]) -> float:
    if metric_name == "http_req_duration" or metric_name.startswith("http_req_"):
        return float((metrics.get("http_reqs") or {}).get("count") or 0)
    if metric_name.startswith("stage_l_endpoint_") and metric_name.endswith("_duration"):
        endpoint = metric_name[len("stage_l_endpoint_"):-len("_duration")]
        failed = metrics.get(f"stage_l_endpoint_{endpoint}_failed") or {}
        return float((failed.get("passes") or 0) + (failed.get("fails") or 0))
    return 1.0


def merge_metric(metric_name: str, metric_values: list[dict[str, Any]], shard_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    first = metric_values[0]
    if "count" in first or "rate" in first:
        return {
            "count": sum(float(value.get("count") or 0) for value in metric_values),
            "rate": sum(float(value.get("rate") or 0) for value in metric_values),
        }
    if "value" in first and ("min" in first or "max" in first):
        return {
            "value": sum(float(value.get("value") or 0) for value in metric_values),
            "min": sum(float(value.get("min") or 0) for value in metric_values),
            "max": sum(float(value.get("max") or 0) for value in metric_values),
        }
    if "passes" in first or "fails" in first or "value" in first:
        passes = sum(float(value.get("passes") or 0) for value in metric_values)
        fails = sum(float(value.get("fails") or 0) for value in metric_values)
        total = passes + fails
        merged: dict[str, Any] = {"passes": passes, "fails": fails, "value": passes / total if total else 0}
        thresholds = first.get("thresholds")
        if thresholds:
            merged["thresholds"] = thresholds
        return merged
    if {"avg", "min", "max"}.intersection(first):
        weights = [trend_count(metric_name, metrics) for metrics in shard_metrics]
        avg_values = [
            (float(value["avg"]), weights[index])
            for index, value in enumerate(metric_values)
            if value.get("avg") is not None
        ]
        merged = {
            "avg": weighted_avg(avg_values),
            "min": min((value.get("min") for value in metric_values if value.get("min") is not None), default=None),
            "max": max((value.get("max") for value in metric_values if value.get("max") is not None), default=None),
        }
        for percentile in ("med", "p(90)", "p(95)", "p(99)"):
            percentile_values = [value.get(percentile) for value in metric_values if value.get(percentile) is not None]
            if percentile_values:
                merged[percentile] = max(percentile_values)
        thresholds = first.get("thresholds")
        if thresholds:
            merged["thresholds"] = thresholds
        return merged
    return first


def merge_k6_summaries(summary_paths: list[Path], output_path: Path) -> dict[str, Any]:
    summaries = [json.loads(path.read_text(encoding="utf-8")) for path in summary_paths]
    shard_metrics = [summary.get("metrics") or {} for summary in summaries]
    metric_names = sorted({name for metrics in shard_metrics for name in metrics})
    merged_metrics: dict[str, Any] = {}
    for name in metric_names:
        values = [metrics[name] for metrics in shard_metrics if name in metrics]
        if values:
            merged_metrics[name] = merge_metric(name, values, shard_metrics)
    merged = {
        "root_group": summaries[0].get("root_group"),
        "metrics": merged_metrics,
        "stage_l_shards": [display_path(path) for path in summary_paths],
    }
    write_json(output_path, merged)
    return merged


def sampler_command(args: argparse.Namespace, sampler_dir: Path, duration_seconds: float) -> list[str]:
    return [
        "python3",
        "./scripts/report_production_load_sampler.py",
        "--manifest",
        args.manifest,
        "--artifact-dir",
        str(sampler_dir),
        "--duration-seconds",
        str(max(0.0, duration_seconds)),
        "--interval-seconds",
        str(max(1.0, args.sampler_interval_seconds)),
        "--sample-count",
        "0",
        "--roles",
        "both",
        "--json",
    ]


def start_sampler(args: argparse.Namespace, *, run_dir: Path, logs_dir: Path) -> SamplerProcess:
    k6_duration = parse_duration_seconds(args.duration)
    duration_seconds = k6_duration + max(0.0, args.sampler_recovery_seconds)
    sampler_dir = run_dir / "sampler"
    stdout_path = logs_dir / "load_sampler.stdout.log"
    stderr_path = logs_dir / "load_sampler.stderr.log"
    command = sampler_command(args, sampler_dir, duration_seconds)
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            text=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    stdout_handle.close()
    stderr_handle.close()
    return SamplerProcess(
        process=process,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        artifact_dir=sampler_dir,
        command=command,
    )


def finish_sampler(sampler: SamplerProcess, *, timeout: float) -> dict[str, Any]:
    status = "finished"
    try:
        exit_code = sampler.process.wait(timeout=max(1.0, timeout))
    except subprocess.TimeoutExpired:
        sampler.process.terminate()
        try:
            exit_code = sampler.process.wait(timeout=120)
            status = "terminated"
        except subprocess.TimeoutExpired:
            sampler.process.kill()
            exit_code = sampler.process.wait(timeout=10)
            status = "killed"
    results_path = sampler.artifact_dir / "results.json"
    return {
        "status": status,
        "exit_code": exit_code,
        "command": command_display(sampler.command),
        "artifact_dir": display_path(sampler.artifact_dir),
        "results_path": display_path(results_path) if results_path.exists() else None,
        "stdout_path": display_path(sampler.stdout_path),
        "stderr_path": display_path(sampler.stderr_path),
    }


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    contract = payload.get("contract") or {}
    k6_result = payload.get("k6") or {}
    fixture = payload.get("fixture") or {}
    sampler = payload.get("sampler") or {}
    lines = [
        "# Stage L Production Realistic Load Harness",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Dry run: `{payload.get('dry_run')}`",
        f"- Prefix: `{payload.get('prefix')}`",
        f"- Target RPS: `{contract.get('target_rps')}`",
        f"- Duration: `{contract.get('duration')}`",
        f"- Profile: `{contract.get('load_profile')}`",
        f"- Include media: `{contract.get('include_media')}`",
        f"- Include mutations: `{contract.get('include_mutations')}`",
        f"- Artifact dir: `{payload.get('artifact_dir')}`",
        "",
        "## Fixture",
        "",
        f"- Prepare status: `{(fixture.get('prepare') or {}).get('status', 'n/a')}`",
        f"- Cleanup status: `{(fixture.get('cleanup') or {}).get('status', 'n/a')}`",
        "",
        "## k6",
        "",
        f"- Exit code: `{k6_result.get('exit_code', 'n/a')}`",
        f"- Summary: `{k6_result.get('summary_path', 'n/a')}`",
        "",
        "## Sampler",
        "",
        f"- Status: `{sampler.get('status', 'n/a')}`",
        f"- Exit code: `{sampler.get('exit_code', 'n/a')}`",
        f"- Results: `{sampler.get('results_path', 'n/a')}`",
        "",
        "## Scenario Weights",
        "",
    ]
    for scenario in contract.get("scenario_contract", []):
        lines.append(f"- `{scenario['persona']}`: `{scenario['weight']}%`")
    lines.extend(["", "## Remaining Stages", ""])
    lines.extend(f"- {stage}" for stage in REMAINING_STAGES)
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_report(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    prefix = args.prefix or f"loadtest_{stamp}_"
    run_dir = Path(args.artifact_root) / stamp / "load-realistic"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    contract = build_contract(args, settings)
    runner = Runner(logs_dir=logs_dir)
    sampler: SamplerProcess | None = None
    payload: dict[str, Any] = {
        "status": "running",
        "stage": "L3-L4",
        "dry_run": args.dry_run or args.list_scenarios,
        "prefix": prefix,
        "started_at": utc_iso(),
        "artifact_dir": display_path(run_dir),
        "contract": contract,
    }

    try:
        if args.dry_run or args.list_scenarios:
            payload["status"] = "passed"
            payload["dry_run_contract_validated"] = True
            return payload

        if not args.load_runner_host:
            raise RealisticLoadError("LOAD_RUNNER_HOST is required for non-dry-run Stage L3 execution")
        if not K6_SCRIPT.exists():
            raise RealisticLoadError(f"k6 script not found: {K6_SCRIPT}")
        require_sshpass_if_needed(args.load_runner_password)

        payload["fixture"] = {}
        prepare = runner.run(
            "fixture_prepare",
            fixture_command(args, prefix, "prepare"),
            timeout=args.fixture_timeout,
            env=load_runner_env(args),
        )
        payload["fixture"]["prepare"] = redact_payload(parse_last_json(prepare.stdout))
        if not args.skip_sampler:
            sampler = start_sampler(args, run_dir=run_dir, logs_dir=logs_dir)
            payload["sampler"] = {
                "status": "running",
                "command": command_display(sampler.command),
                "artifact_dir": display_path(sampler.artifact_dir),
                "stdout_path": display_path(sampler.stdout_path),
                "stderr_path": display_path(sampler.stderr_path),
            }

        paths = remote_paths(args, prefix)
        env = load_runner_env(args)
        runner.run(
            "load_runner_prepare_dirs",
            ssh_args(
                args,
                f"mkdir -p {quote_remote(paths['script_dir'])} {quote_remote(paths['artifact_dir'])}",
            ),
            timeout=45,
            env=env,
        )
        runner.run(
            "load_runner_upload_k6_script",
            scp_args(args, str(K6_SCRIPT), f"{args.load_runner_host}:{paths['script']}"),
            timeout=45,
            env=env,
        )
        k6_result = runner.run(
            "k6_realistic_mix",
            ssh_args(args, run_k6_command(args, contract, paths)),
            timeout=args.k6_timeout,
            check=False,
            env=env,
        )
        payload["k6"] = {
            "exit_code": k6_result.exit_code,
            "remote_summary_path": paths["summary"],
            "stdout_path": display_path(logs_dir / "k6_realistic_mix.stdout.log"),
            "stderr_path": display_path(logs_dir / "k6_realistic_mix.stderr.log"),
            "shard_count": args.load_runner_shards,
        }
        if args.load_runner_shards > 1:
            shard_entries = []
            shard_summary_paths: list[Path] = []
            for shard_index in range(1, args.load_runner_shards + 1):
                remote_summary = f"{paths['artifact_dir']}/k6-summary-shard-{shard_index}.json"
                local_summary = run_dir / f"k6-summary-shard-{shard_index}.json"
                runner.run(
                    f"download_k6_summary_shard_{shard_index}",
                    scp_args(args, f"{args.load_runner_host}:{remote_summary}", str(local_summary)),
                    timeout=45,
                    check=False,
                    env=env,
                )
                shard_entry = {
                    "shard_index": shard_index,
                    "remote_summary_path": remote_summary,
                    "summary_path": display_path(local_summary) if local_summary.exists() else None,
                }
                if local_summary.exists():
                    shard_summary_paths.append(local_summary)
                for stream_name in ("stdout", "stderr"):
                    remote_stream = f"{paths['artifact_dir']}/k6-{stream_name}-shard-{shard_index}.log"
                    local_stream = logs_dir / f"k6_realistic_mix_shard_{shard_index}.{stream_name}.log"
                    runner.run(
                        f"download_k6_{stream_name}_shard_{shard_index}",
                        scp_args(args, f"{args.load_runner_host}:{remote_stream}", str(local_stream)),
                        timeout=45,
                        check=False,
                        env=env,
                    )
                    if local_stream.exists():
                        shard_entry[f"{stream_name}_path"] = display_path(local_stream)
                shard_entries.append(shard_entry)
            payload["k6"]["shards"] = shard_entries
            if len(shard_summary_paths) == args.load_runner_shards:
                aggregate_path = run_dir / "k6-summary.json"
                merge_k6_summaries(shard_summary_paths, aggregate_path)
                payload["k6"]["summary_path"] = display_path(aggregate_path)
            elif k6_result.exit_code == 0:
                raise RealisticLoadError("not all k6 shard summaries were downloaded")
        else:
            runner.run(
                "download_k6_summary",
                scp_args(args, f"{args.load_runner_host}:{paths['summary']}", str(run_dir / "k6-summary.json")),
                timeout=45,
                check=False,
                env=env,
            )
            if (run_dir / "k6-summary.json").exists():
                payload["k6"]["summary_path"] = display_path(run_dir / "k6-summary.json")
        if k6_result.exit_code != 0:
            raise RealisticLoadError(f"k6 realistic mix failed with exit code {k6_result.exit_code}")
        payload["status"] = "passed"
    except Exception as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        raise
    finally:
        if not args.dry_run and not args.list_scenarios and not args.keep_fixtures:
            try:
                cleanup = runner.run(
                    "fixture_cleanup",
                    fixture_command(args, prefix, "cleanup"),
                    timeout=args.fixture_timeout,
                    check=False,
                    env=load_runner_env(args),
                )
                payload.setdefault("fixture", {})["cleanup"] = redact_payload(parse_last_json(cleanup.stdout))
                if cleanup.exit_code != 0 and payload.get("status") == "passed":
                    payload["status"] = "failed"
                    payload["error"] = f"fixture cleanup failed with exit code {cleanup.exit_code}"
            except Exception as cleanup_exc:
                payload.setdefault("fixture", {})["cleanup_error"] = str(cleanup_exc)
        if sampler is not None:
            payload["sampler"] = finish_sampler(
                sampler,
                timeout=max(30.0, args.sampler_interval_seconds + args.sampler_timeout_padding_seconds),
            )
            if payload["sampler"].get("exit_code") not in (0, None) and payload.get("status") == "passed":
                payload["status"] = "failed"
                payload["error"] = f"load sampler failed with exit code {payload['sampler'].get('exit_code')}"
        payload["finished_at"] = utc_iso()
        payload["commands"] = runner.commands
        write_json(run_dir / "metadata.json", {"contract": contract, "prefix": prefix, "stage": "L3-L4"})
        write_json(run_dir / "results.json", redact_payload(payload))
        write_summary(run_dir / "summary.md", redact_payload(payload))
    return redact_payload(payload)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage L3 realistic mixed k6 load harness.")
    parser.add_argument("--manifest", default=os.environ.get("MANIFEST", "./deploy/production/online.env"))
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=os.environ.get("LOAD_ARTIFACT_ROOT", str(DEFAULT_ARTIFACT_ROOT)))
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL"))
    parser.add_argument("--target-rps", type=int, default=int(os.environ.get("TARGET_RPS", "50")))
    parser.add_argument("--duration", default=os.environ.get("DURATION", "2m"))
    parser.add_argument("--load-profile", default=os.environ.get("LOAD_PROFILE", "smoke"))
    parser.add_argument("--include-media", action=argparse.BooleanOptionalAction, default=bool_env("INCLUDE_MEDIA", False))
    parser.add_argument("--include-mutations", action=argparse.BooleanOptionalAction, default=bool_env("INCLUDE_MUTATIONS", False))
    parser.add_argument("--pre-allocated-vus", type=int, default=int(os.environ.get("PRE_ALLOCATED_VUS", "0") or "0"))
    parser.add_argument("--max-vus", type=int, default=int(os.environ.get("MAX_VUS", "0") or "0"))
    parser.add_argument("--load-runner-nofile", type=int, default=int(os.environ.get("LOAD_RUNNER_NOFILE", "65535")))
    parser.add_argument("--load-runner-shards", type=int, default=int(os.environ.get("LOAD_RUNNER_SHARDS", "1")))
    parser.add_argument("--load-runner-host", default=os.environ.get("LOAD_RUNNER_HOST", ""))
    parser.add_argument("--load-runner-port", default=os.environ.get("LOAD_RUNNER_SSH_PORT", "22"))
    parser.add_argument("--load-runner-jump-host", default=os.environ.get("LOAD_RUNNER_JUMP_HOST", ""))
    parser.add_argument("--load-runner-jump-port", default=os.environ.get("LOAD_RUNNER_JUMP_SSH_PORT", "22"))
    parser.add_argument("--load-runner-password", default=os.environ.get("LOAD_RUNNER_PASSWORD", ""))
    parser.add_argument("--load-runner-remote-dir", default=os.environ.get("LOAD_RUNNER_REMOTE_DIR", "/srv/trading-bot-loadtest"))
    parser.add_argument("--token-minutes", type=int, default=240)
    parser.add_argument("--fixture-timeout", type=int, default=420)
    parser.add_argument("--k6-timeout", type=int, default=900)
    parser.add_argument("--sampler-interval-seconds", type=float, default=float(os.environ.get("LOAD_SAMPLER_INTERVAL_SECONDS", "10")))
    parser.add_argument("--sampler-recovery-seconds", type=float, default=float(os.environ.get("LOAD_SAMPLER_RECOVERY_SECONDS", "30")))
    parser.add_argument("--sampler-timeout-padding-seconds", type=float, default=float(os.environ.get("LOAD_SAMPLER_TIMEOUT_PADDING_SECONDS", "60")))
    parser.add_argument("--skip-sampler", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--keep-fixtures", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.pre_allocated_vus <= 0:
        args.pre_allocated_vus = max(20, (args.target_rps + 1) // 2)
    if args.max_vus <= 0:
        args.max_vus = max(100, args.target_rps * 4)
    if args.load_runner_shards <= 0:
        args.load_runner_shards = 1
    try:
        payload = run_report(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"STAGE_L_REALISTIC_DONE status=failed error={type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        f"STAGE_L_REALISTIC_DONE status={payload.get('status')} artifact={payload.get('artifact_dir')}",
        file=sys.stderr,
    )
    if args.json or args.dry_run or args.list_scenarios:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
