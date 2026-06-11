#!/usr/bin/env python3
"""Run the staged full-product production benchmark harness.

This is an orchestration layer. It keeps artifacts, surface mapping, command
results, and pass/fail summaries consistent while delegating deep probes to the
existing focused scripts.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import (
    DEFAULT_ARTIFACT_ROOT,
    REMAINING_STAGES,
    display_path,
    compose_probe_script,
    quote_remote,
    remote_args,
    run_command,
    utc_iso,
    utc_stamp,
)
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    surface_id: str
    label: str
    profile: str
    command: list[str]
    timeout_seconds: int
    bottleneck_class: str
    modes: tuple[str, ...] = ("quick", "targeted", "full")
    long_running: bool = False
    mutating: bool = False
    required: bool = True


def _health_url(settings: dict[str, str], *, role: str) -> str:
    if role == "iran":
        return settings.get("IRAN_HEALTHCHECK_URL") or f"{settings.get('IRAN_SERVER_URL', '').rstrip('/')}/api/config"
    return f"{settings.get('FOREIGN_SERVER_URL', 'http://127.0.0.1:8000').rstrip('/')}/api/config"


def _python_script(script: str, *args: str) -> list[str]:
    return [sys.executable, script, *args]


def _iran_compose_args(settings: dict[str, str], body: str) -> list[str]:
    command = f"cd {quote_remote(settings['IRAN_PROJECT_DIR'])} && " + compose_probe_script("docker-compose.iran.yml", body)
    return remote_args(settings, command)


def _iran_python_script(settings: dict[str, str], script: str, *args: str) -> list[str]:
    quoted = " ".join(shlex_quote(item) for item in ("python", script, *args))
    return _iran_compose_args(settings, f"exec -T app {quoted}")


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def build_tasks(
    *,
    settings: dict[str, str],
    manifest: str | None,
    stamp: str,
    artifact_root: Path,
    target: str,
) -> list[BenchmarkTask]:
    manifest_args = ["--manifest", manifest] if manifest else []
    tasks = [
        BenchmarkTask(
            task_id="baseline_capture",
            surface_id="B00/B08",
            label="Stage P0 host/runtime/sync baseline capture",
            profile="baseline",
            command=[
                sys.executable,
                "scripts/capture_production_baseline.py",
                *manifest_args,
                "--timestamp",
                stamp,
                "--artifact-root",
                str(artifact_root),
            ],
            timeout_seconds=180,
            bottleneck_class="runtime_inventory",
            modes=("quick", "targeted", "full"),
        ),
        BenchmarkTask(
            task_id="iran_api_config",
            surface_id="B01",
            label="Iran public API config health latency",
            profile="api",
            command=["curl", "-fsS", "--max-time", "15", _health_url(settings, role="iran")],
            timeout_seconds=30,
            bottleneck_class="api_health",
            modes=("quick", "targeted", "full"),
        ),
        BenchmarkTask(
            task_id="foreign_peer_api_config",
            surface_id="B01",
            label="Foreign peer public API config health latency",
            profile="api",
            command=["curl", "-fsS", "--max-time", "15", _health_url(settings, role="foreign")],
            timeout_seconds=30,
            bottleneck_class="peer_api_health",
            modes=("quick", "targeted", "full"),
            required=False,
        ),
        BenchmarkTask(
            task_id="sync_health_iran",
            surface_id="B08",
            label="Iran sync-health endpoint over SSH",
            profile="sync",
            command=["make", "sync-health-iran"],
            timeout_seconds=60,
            bottleneck_class="cross_server_sync",
            modes=("quick", "targeted", "full"),
        ),
        BenchmarkTask(
            task_id="sync_health_foreign_peer",
            surface_id="B08",
            label="Foreign peer sync-health endpoint",
            profile="sync",
            command=["make", "sync-health"],
            timeout_seconds=45,
            bottleneck_class="cross_server_sync",
            modes=("quick", "targeted", "full"),
            required=False,
        ),
        BenchmarkTask(
            task_id="cross_server_sync_benchmark",
            surface_id="B08",
            label="Stage P6 cross-server sync propagation, partial-failure, and recovery benchmark",
            profile="sync",
            command=[
                sys.executable,
                "scripts/report_cross_server_sync_benchmark.py",
                *manifest_args,
                "--timestamp",
                stamp,
                "--artifact-root",
                str(artifact_root),
            ],
            timeout_seconds=900,
            bottleneck_class="cross_server_sync_recovery",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="observability_overhead",
            surface_id="B10",
            label="Structured logging overhead inside Iran app container",
            profile="observability",
            command=_iran_python_script(settings, "scripts/measure_logging_overhead.py", "--iterations", "5000", "--budget-us", "1000"),
            timeout_seconds=45,
            bottleneck_class="observability_overhead",
            modes=("quick", "targeted", "full"),
        ),
        BenchmarkTask(
            task_id="metrics_targets",
            surface_id="B10",
            label="Metrics target contract render inside Iran app container",
            profile="observability",
            command=_iran_python_script(settings, "scripts/render_metrics_targets.py", "--server-mode", "iran", "--compose-file", "docker-compose.iran.yml"),
            timeout_seconds=30,
            bottleneck_class="metrics_contract",
            modes=("quick", "targeted", "full"),
        ),
        BenchmarkTask(
            task_id="static_delivery_headers",
            surface_id="B01/B06",
            label="Public Nginx static/cache/protected-media delivery probe",
            profile="static",
            command=[
                sys.executable,
                "scripts/report_static_delivery.py",
                *manifest_args,
                "--json",
            ],
            timeout_seconds=45,
            bottleneck_class="nginx_static_delivery",
            modes=("quick", "targeted", "full"),
        ),
        BenchmarkTask(
            task_id="redis_runtime_durability",
            surface_id="B08",
            label="Redis durability settings and queue state inside Iran app container",
            profile="redis",
            command=_iran_python_script(settings, "scripts/report_redis_runtime.py", "--json"),
            timeout_seconds=45,
            bottleneck_class="redis_durability",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="production_healthcheck",
            surface_id="B11",
            label="Production deploy healthcheck wrapper",
            profile="deployment",
            command=["make", "production-online-health"],
            timeout_seconds=180,
            bottleneck_class="deployment_health",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="messenger_report",
            surface_id="B05",
            label="Messenger benchmark artifact integration report",
            profile="messenger",
            command=_python_script("scripts/build_messenger_benchmark_report.py"),
            timeout_seconds=90,
            bottleneck_class="messenger_artifact_summary",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="postgres_runtime_tuning",
            surface_id="B02",
            label="PostgreSQL runtime tuning and connection budget inside Iran app container",
            profile="db",
            command=_iran_python_script(settings, "scripts/report_postgres_runtime.py", "--expected-profile", "iran-p2", "--json"),
            timeout_seconds=45,
            bottleneck_class="database_runtime_tuning",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="messenger_query_plans",
            surface_id="B05/B06",
            label="Messenger DB query plan benchmark inside Iran app container",
            profile="db",
            command=_iran_python_script(settings, "scripts/report_messenger_query_plans.py", "--json"),
            timeout_seconds=120,
            bottleneck_class="database_query_plan",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="market_trade_query_plans",
            surface_id="B04/B06",
            label="Market/trade DB query plan benchmark inside Iran app container",
            profile="db",
            command=_iran_python_script(settings, "scripts/report_market_trade_query_plans.py", "--json"),
            timeout_seconds=120,
            bottleneck_class="database_query_plan",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="trading_core_benchmark",
            surface_id="B04/B06/B09",
            label="Stage P7 trading core, market, bot, notification, and race benchmark",
            profile="trading",
            command=[
                sys.executable,
                "scripts/report_trading_core_benchmark.py",
                *manifest_args,
                "--timestamp",
                stamp,
                "--artifact-root",
                str(artifact_root),
                "--json",
            ],
            timeout_seconds=900,
            bottleneck_class="trading_core_market_bot",
            modes=("targeted", "full"),
            mutating=True,
        ),
        BenchmarkTask(
            task_id="worker_pool_matrix",
            surface_id="B01/B02/B04/B05",
            label="Stage P5 authenticated/chat worker-count matrix on Iran",
            profile="workers",
            command=[
                sys.executable,
                "scripts/run_worker_pool_matrix.py",
                *manifest_args,
                "--workers",
                "8,12,16",
                "--requests",
                "180",
                "--concurrency",
                "18",
                "--timestamp",
                stamp,
                "--artifact-root",
                str(artifact_root),
                "--json",
            ],
            timeout_seconds=1200,
            bottleneck_class="api_worker_db_pressure",
            modes=("targeted", "full"),
        ),
        BenchmarkTask(
            task_id="observability_gate",
            surface_id="B10",
            label="Focused local observability regression gate",
            profile="observability",
            command=["make", "observability-gate"],
            timeout_seconds=180,
            bottleneck_class="observability_regression",
            modes=("targeted", "full"),
            required=False,
        ),
        BenchmarkTask(
            task_id="frontend_ux_benchmark",
            surface_id="B03/B04/B10",
            label="Stage P8 non-messenger frontend UX, bounded list, route, heap, DOM, and network benchmark",
            profile="frontend",
            command=[
                sys.executable,
                "scripts/report_frontend_ux_benchmark.py",
                *manifest_args,
                "--timestamp",
                stamp,
                "--artifact-root",
                str(artifact_root),
                "--json",
            ],
            timeout_seconds=600,
            bottleneck_class="frontend_ux_runtime",
            modes=("targeted", "full"),
            mutating=True,
        ),
        BenchmarkTask(
            task_id="frontend_e2e_chromium",
            surface_id="B03/B08",
            label="Frontend Chromium Playwright smoke",
            profile="frontend",
            command=["make", "frontend-test-e2e"],
            timeout_seconds=3600,
            bottleneck_class="frontend_ux_runtime",
            modes=("targeted", "full"),
            long_running=True,
            mutating=True,
        ),
        BenchmarkTask(
            task_id="messenger_full",
            surface_id="B05/B06/B07/B08",
            label="Official Messenger full benchmark",
            profile="messenger",
            command=["make", "messenger-benchmark-all"],
            timeout_seconds=7200,
            bottleneck_class="messenger_full_stack",
            modes=("full",),
            long_running=True,
            mutating=True,
        ),
    ]
    if target != "iran":
        return tasks
    return tasks


def task_selected(
    task: BenchmarkTask,
    *,
    mode: str,
    profile: str | None,
    include_long: bool,
    skip_long: bool,
    include_mutating: bool,
) -> bool:
    if mode not in task.modes:
        return False
    targeted_safe_mutating_profile = (
        mode == "targeted"
        and (
            (profile == "trading" and task.profile == "trading")
            or (profile == "frontend" and task.task_id == "frontend_ux_benchmark")
        )
    )
    if task.mutating and not include_mutating and not targeted_safe_mutating_profile:
        return False
    if mode == "targeted" and profile and task.profile != profile:
        return False
    if mode == "targeted" and not profile and task.profile not in {"baseline", "api", "sync"}:
        return False
    if task.long_running and skip_long:
        return False
    if task.long_running and mode != "full" and not include_long:
        return False
    return True


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_result(task: BenchmarkTask, result: dict[str, Any]) -> dict[str, Any]:
    status = "passed" if result["exit_code"] == 0 else ("failed" if task.required else "warning")
    return {
        **result,
        "task_id": task.task_id,
        "surface_id": task.surface_id,
        "label": task.label,
        "profile": task.profile,
        "bottleneck_class": task.bottleneck_class,
        "required": task.required,
        "long_running": task.long_running,
        "mutating": task.mutating,
        "status": status,
    }


def build_surface_summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_surface: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_surface.setdefault(str(item["surface_id"]), []).append(item)
    summary: list[dict[str, Any]] = []
    for surface_id, items in sorted(by_surface.items()):
        failed = [item for item in items if item["status"] == "failed"]
        summary.append(
            {
                "surface_id": surface_id,
                "task_count": len(items),
                "failed_count": len(failed),
                "status": "failed" if failed else "passed",
                "bottleneck_classes": sorted({str(item["bottleneck_class"]) for item in items}),
            }
        )
    return summary


def write_summary(path: Path, *, metadata: dict[str, Any], results: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> None:
    failed = [item for item in results if item["status"] == "failed"]
    lines = [
        "# Production Benchmark Summary",
        "",
        f"- Mode: `{metadata['mode']}`",
        f"- Profile: `{metadata.get('profile') or 'n/a'}`",
        f"- Started at: `{metadata['started_at']}`",
        f"- Git SHA: `{metadata.get('git_sha', 'unknown')}`",
        f"- Artifact dir: `{metadata['artifact_dir']}`",
        f"- Tasks run: `{len(results)}`",
        f"- Tasks skipped: `{len(skipped)}`",
        f"- Failed required tasks: `{len(failed)}`",
        "",
        "## Task Results",
        "",
        "| Task | Surface | Profile | Status | Duration | Bottleneck class |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            f"| `{item['task_id']}` | `{item['surface_id']}` | `{item['profile']}` | "
            f"`{item['status']}` | `{item['duration_seconds']}s` | `{item['bottleneck_class']}` |"
        )
    lines.extend(["", "## Skipped Tasks", ""])
    if skipped:
        for item in skipped:
            lines.append(f"- `{item['task_id']}` ({item['surface_id']}): {item['reason']}")
    else:
        lines.append("- None")
    lines.extend(["", "## Remaining Roadmap Stages", ""])
    for stage in REMAINING_STAGES:
        if stage.startswith("P1 "):
            continue
        lines.append(f"- {stage}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_run_artifacts(
    *,
    run_dir: Path,
    metadata: dict[str, Any],
    results: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> None:
    payload = {
        "metadata": metadata,
        "surface_summary": build_surface_summary([item for item in results if item.get("status") != "planned"]),
        "results": results,
        "skipped": skipped,
    }
    write_json(run_dir / "metadata.json", metadata)
    write_json(run_dir / "results.json", payload)
    write_summary(run_dir / "summary.md", metadata=metadata, results=results, skipped=skipped)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the production benchmark orchestration harness.")
    parser.add_argument("--mode", choices=("quick", "targeted", "full"), default="quick")
    parser.add_argument("--profile", help="Targeted profile: baseline, api, sync, observability, deployment, messenger, db, redis, static, workers, trading, frontend.")
    parser.add_argument("--target", choices=("iran",), default="iran", help="Production benchmark target host.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--include-long", action="store_true", help="Allow long-running tasks in targeted mode.")
    parser.add_argument("--include-mutating", action="store_true", help="Allow data-mutating benchmark suites. Do not use on production.")
    parser.add_argument("--skip-long", action="store_true", help="Skip long-running tasks even in full mode.")
    parser.add_argument("--allow-failures", action="store_true", help="Exit 0 even when required tasks fail.")
    parser.add_argument("--dry-run", action="store_true", help="Write planned tasks without executing them.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stamp = args.timestamp or utc_stamp()
    artifact_root = Path(args.artifact_root)
    run_dir = artifact_root / stamp
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    tasks = build_tasks(settings=settings, manifest=args.manifest, stamp=stamp, artifact_root=artifact_root, target=args.target)
    selected = [
        task for task in tasks
        if task_selected(
            task,
            mode=args.mode,
            profile=args.profile,
            include_long=args.include_long,
            skip_long=args.skip_long,
            include_mutating=args.include_mutating,
        )
    ]
    selected_ids = {task.task_id for task in selected}
    skipped = [
        {
            "task_id": task.task_id,
            "surface_id": task.surface_id,
            "profile": task.profile,
            "long_running": task.long_running,
            "mutating": task.mutating,
            "reason": "not selected by mode/profile/long-running flags",
        }
        for task in tasks
        if task.task_id not in selected_ids
    ]

    git_sha = ""
    git_result = run_command(name="benchmark_git_sha", args=["git", "rev-parse", "HEAD"], logs_dir=logs_dir, timeout=15)
    if git_result["exit_code"] == 0:
        git_sha = (REPO_ROOT / git_result["stdout_path"]).read_text(encoding="utf-8").strip()

    metadata = {
        "started_at": utc_iso(),
        "mode": args.mode,
        "profile": args.profile,
        "target": args.target,
        "artifact_dir": display_path(run_dir),
        "git_sha": git_sha,
        "include_long": bool(args.include_long),
        "include_mutating": bool(args.include_mutating),
        "skip_long": bool(args.skip_long),
        "dry_run": bool(args.dry_run),
        "task_count": len(selected),
    }

    results: list[dict[str, Any]] = []
    if args.dry_run:
        for task in selected:
            results.append(
                {
                    "task_id": task.task_id,
                    "surface_id": task.surface_id,
                    "label": task.label,
                    "profile": task.profile,
                    "bottleneck_class": task.bottleneck_class,
                    "required": task.required,
                    "long_running": task.long_running,
                    "mutating": task.mutating,
                    "status": "planned",
                    "command": " ".join(task.command),
                    "exit_code": None,
                    "duration_seconds": 0,
                }
            )
    else:
        for task in selected:
            print(f"[production-benchmark] starting {task.task_id}: {task.label}", flush=True)
            write_json(run_dir / "current-task.json", {
                "task_id": task.task_id,
                "surface_id": task.surface_id,
                "label": task.label,
                "started_at": utc_iso(),
            })
            result = run_command(name=task.task_id, args=task.command, logs_dir=logs_dir, timeout=task.timeout_seconds)
            normalized = normalize_result(task, result)
            results.append(normalized)
            print(
                f"[production-benchmark] finished {task.task_id}: "
                f"{normalized['status']} ({normalized['duration_seconds']}s)",
                flush=True,
            )
            metadata["finished_at"] = utc_iso()
            metadata["failed_required_count"] = sum(1 for item in results if item.get("status") == "failed")
            write_run_artifacts(run_dir=run_dir, metadata=metadata, results=results, skipped=skipped)

    metadata["finished_at"] = utc_iso()
    metadata["failed_required_count"] = sum(1 for item in results if item.get("status") == "failed")
    write_run_artifacts(run_dir=run_dir, metadata=metadata, results=results, skipped=skipped)

    print(json.dumps({
        "artifact_dir": display_path(run_dir),
        "failed_required_count": metadata["failed_required_count"],
        "mode": args.mode,
        "profile": args.profile,
        "task_count": len(selected),
    }, ensure_ascii=False, sort_keys=True))
    if metadata["failed_required_count"] and not args.allow_failures:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
