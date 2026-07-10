#!/usr/bin/env python3
"""Run a guarded Stage L7.1 DB pool matrix on the Iran production host."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings
from scripts.report_production_realistic_load import parse_duration_seconds
from scripts.run_worker_pool_matrix import run_remote_script, sh_quote


REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_DROPPED_ITERATION_RATIO = 0.02


@dataclass(frozen=True)
class PoolCandidate:
    pool_size: int
    max_overflow: int

    @property
    def label(self) -> str:
        return f"p{self.pool_size}-o{self.max_overflow}"

    @property
    def total_per_worker(self) -> int:
        return self.pool_size + self.max_overflow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage L7.1 DB pool candidates with the realistic load harness.")
    parser.add_argument("--manifest", default=os.environ.get("MANIFEST", "./deploy/production/online.env"))
    parser.add_argument("--candidates", default="8:4,12:8,16:8")
    parser.add_argument("--api-workers", type=int, default=int(os.environ.get("IRAN_API_WORKERS", "4")))
    parser.add_argument("--workers", default=None, help="Optional comma-separated API_WORKERS candidates. Defaults to --api-workers.")
    parser.add_argument("--target-rps", type=int, default=int(os.environ.get("TARGET_RPS", "500")))
    parser.add_argument("--duration", default=os.environ.get("DURATION", "2m"))
    parser.add_argument("--load-profile", default=os.environ.get("LOAD_PROFILE", "target"))
    parser.add_argument("--include-media", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-mutations", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--sampler-interval-seconds", type=float, default=float(os.environ.get("LOAD_SAMPLER_INTERVAL_SECONDS", "10")))
    parser.add_argument("--post-apply-settle-seconds", type=float, default=float(os.environ.get("LOAD_POST_APPLY_SETTLE_SECONDS", "10")))
    parser.add_argument("--artifact-root", default=os.environ.get("LOAD_ARTIFACT_ROOT", str(DEFAULT_ARTIFACT_ROOT)))
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--candidate-timeout-padding-seconds", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def parse_candidates(raw: str) -> list[PoolCandidate]:
    candidates: list[PoolCandidate] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"invalid pool candidate {item!r}; expected pool:overflow")
        pool_raw, overflow_raw = item.split(":", 1)
        candidate = PoolCandidate(int(pool_raw), int(overflow_raw))
        if candidate.pool_size <= 0 or candidate.max_overflow < 0:
            raise ValueError("pool size must be positive and max overflow must be non-negative")
        if candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("at least one pool candidate is required")
    return candidates


def parse_worker_candidates(raw: str | None, fallback: int) -> list[int]:
    if not raw:
        return [fallback]
    candidates: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item)
        if value <= 0:
            raise ValueError("worker candidates must be positive integers")
        if value not in candidates:
            candidates.append(value)
    if not candidates:
        raise ValueError("at least one worker candidate is required")
    return candidates


def remote_compose_prelude(project_dir: str) -> str:
    return f"""
set -euo pipefail
cd {sh_quote(project_dir)}
if docker compose version >/dev/null 2>&1; then
  compose_cmd="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd="docker-compose"
else
  echo "No Docker Compose command is available on the Iran host." >&2
  exit 125
fi
"""


def apply_pool_script(project_dir: str, stamp: str, candidate: PoolCandidate, api_workers: int) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
env_file=".env"
backup_file=".env.stage-l7-pool-matrix-{stamp}.bak"
if [ ! -f "$backup_file" ]; then
  cp "$env_file" "$backup_file"
fi
python3 - "$env_file" <<'PY'
import sys
from pathlib import Path

def update_env_content(content, updates):
    lines = content.splitlines()
    seen = set()
    rendered = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            rendered.append(f"{{key}}={{updates[key]}}")
            seen.add(key)
        else:
            rendered.append(line)
    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{{key}}={{value}}")
    return "\\n".join(rendered) + "\\n"

path = Path(sys.argv[1])
path.write_text(
    update_env_content(
        path.read_text(encoding="utf-8"),
        {{
            "API_WORKERS": "{api_workers}",
            "DB_POOL_SIZE": "{candidate.pool_size}",
            "DB_MAX_OVERFLOW": "{candidate.max_overflow}",
        }},
    ),
    encoding="utf-8",
)
PY
$compose_cmd -f docker-compose.iran.yml rm -sf app >/dev/null 2>&1 || docker rm -f trading_bot_app >/dev/null 2>&1 || true
$compose_cmd -f docker-compose.iran.yml up -d --no-deps app
for i in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
    docker exec trading_bot_app sh -c 'printf "DB_POOL_SIZE=%s\\nDB_MAX_OVERFLOW=%s\\nAPI_WORKERS=%s\\n" "$DB_POOL_SIZE" "$DB_MAX_OVERFLOW" "$API_WORKERS"'
    exit 0
  fi
  sleep 2
done
echo "app did not become ready for {candidate.label}" >&2
exit 124
"""


def restore_pool_script(project_dir: str, stamp: str) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
env_file=".env"
backup_file=".env.stage-l7-pool-matrix-{stamp}.bak"
if [ -f "$backup_file" ]; then
  cp "$backup_file" "$env_file"
fi
$compose_cmd -f docker-compose.iran.yml rm -sf app >/dev/null 2>&1 || docker rm -f trading_bot_app >/dev/null 2>&1 || true
$compose_cmd -f docker-compose.iran.yml up -d --no-deps app
for i in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
    docker exec trading_bot_app sh -c 'printf "DB_POOL_SIZE=%s\\nDB_MAX_OVERFLOW=%s\\nAPI_WORKERS=%s\\n" "$DB_POOL_SIZE" "$DB_MAX_OVERFLOW" "$API_WORKERS"'
    exit 0
  fi
  sleep 2
done
echo "app did not become ready after pool matrix restore" >&2
exit 124
"""


def collect_app_logs_script(project_dir: str, tail_lines: int = 5000) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
$compose_cmd -f docker-compose.iran.yml logs --no-color --tail {tail_lines} app || docker logs --tail {tail_lines} trading_bot_app
"""


def realistic_load_args(args: argparse.Namespace, candidate_stamp: str) -> list[str]:
    command = [
        sys.executable,
        "./scripts/report_production_realistic_load.py",
        "--manifest",
        args.manifest,
        "--timestamp",
        candidate_stamp,
        "--target-rps",
        str(args.target_rps),
        "--duration",
        args.duration,
        "--load-profile",
        args.load_profile,
        "--sampler-interval-seconds",
        str(args.sampler_interval_seconds),
        "--json",
    ]
    command.append("--include-media" if args.include_media else "--no-include-media")
    command.append("--include-mutations" if args.include_mutations else "--no-include-mutations")
    return command


def run_local_command(command: list[str], *, timeout: int, env: dict[str, str]) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env={**os.environ, **env},
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def metric_value(metrics: dict[str, Any], key: str, field: str, default: Any = None) -> Any:
    value = metrics.get(key) or {}
    return value.get(field, default)


def endpoint_breakdown(metrics: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    prefix = "stage_l_endpoint_"
    duration_suffix = "_duration"
    failed_suffix = "_failed"
    rows: list[dict[str, Any]] = []
    for name, duration in metrics.items():
        if not name.startswith(prefix) or not name.endswith(duration_suffix):
            continue
        endpoint = name[len(prefix):-len(duration_suffix)]
        failed = metrics.get(f"{prefix}{endpoint}{failed_suffix}") or {}
        rows.append(
            {
                "endpoint": endpoint,
                "avg_ms": duration.get("avg"),
                "p90_ms": duration.get("p(90)"),
                "p95_ms": duration.get("p(95)"),
                "p99_ms": duration.get("p(99)"),
                "max_ms": duration.get("max"),
                "failure_rate": failed.get("value"),
                "failed_requests": failed.get("passes"),
                "successful_requests": failed.get("fails"),
            }
        )
    rows.sort(
        key=lambda item: (
            float(item.get("p95_ms") or 0),
            float(item.get("p99_ms") or 0),
            float(item.get("avg_ms") or 0),
        ),
        reverse=True,
    )
    return rows[:limit]


def dropped_iteration_ratio(*, http_reqs: float, dropped_iterations: int) -> float:
    attempted = float(http_reqs or 0) + float(dropped_iterations or 0)
    if attempted <= 0:
        return 1.0 if dropped_iterations else 0.0
    return float(dropped_iterations or 0) / attempted


def summarize_candidate(artifact_root: str, candidate_stamp: str) -> dict[str, Any]:
    run_dir = Path(artifact_root) / candidate_stamp / "load-realistic"
    results_path = run_dir / "results.json"
    k6_path = run_dir / "k6-summary.json"
    sampler_path = run_dir / "sampler" / "results.json"
    summary: dict[str, Any] = {
        "artifact_dir": display_path(run_dir),
        "results_path": display_path(results_path),
        "exists": results_path.exists(),
    }
    if results_path.exists():
        results = load_json(results_path)
        summary["status"] = results.get("status")
        summary["error"] = results.get("error")
    if k6_path.exists():
        k6 = load_json(k6_path)
        metrics = k6.get("metrics") or {}
        duration = metrics.get("http_req_duration") or {}
        failed = metrics.get("http_req_failed") or {}
        checks = metrics.get("checks") or {}
        dropped = metrics.get("dropped_iterations") or {}
        summary["k6"] = {
            "http_reqs": metric_value(metrics, "http_reqs", "count", 0),
            "rps": metric_value(metrics, "http_reqs", "rate", 0),
            "failure_rate": failed.get("value"),
            "failed_requests": failed.get("passes"),
            "check_rate": checks.get("value"),
            "dropped_iterations": dropped.get("count", 0),
            "dropped_iteration_ratio": dropped_iteration_ratio(
                http_reqs=metric_value(metrics, "http_reqs", "count", 0),
                dropped_iterations=int(dropped.get("count", 0) or 0),
            ),
            "p95_ms": duration.get("p(95)"),
            "p99_ms": duration.get("p(99)"),
            "avg_ms": duration.get("avg"),
        }
        summary["endpoint_breakdown"] = endpoint_breakdown(metrics)
    if sampler_path.exists():
        sampler = load_json(sampler_path)
        summary["sampler"] = sampler.get("summary")
        summary["sampler_status"] = sampler.get("status")
    return summary


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Stage L7.1 DB Pool Matrix",
        "",
        f"- Started at: `{report['started_at']}`",
        f"- Iran host: `{report['iran_host']}`",
        f"- Target: `{report['target_rps']} RPS / {report['duration']}`",
        f"- API workers: `{report['api_workers']}`",
        f"- Restored original env: `{report.get('restored')}`",
        "",
        "| Candidate | Status | RPS | Failure | P95 | P99 | Dropped | Dropped ratio | Max PG conn | Nginx 5xx delta | Artifact |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["candidates"]:
        summary = item.get("summary") or {}
        k6 = summary.get("k6") or {}
        sampler = summary.get("sampler") or {}
        lines.append(
            f"| `{item['candidate']}` | `{summary.get('status', item.get('status', 'unknown'))}` | "
            f"`{round(float(k6.get('rps') or 0), 2)}` | "
            f"`{round(float(k6.get('failure_rate') or 0) * 100, 3)}%` | "
            f"`{round(float(k6.get('p95_ms') or 0), 2)}ms` | "
            f"`{round(float(k6.get('p99_ms') or 0), 2)}ms` | "
            f"`{k6.get('dropped_iterations', 'n/a')}` | "
            f"`{round(float(k6.get('dropped_iteration_ratio') or 0) * 100, 3)}%` | "
            f"`{sampler.get('max_postgres_connections', 'n/a')}` | "
            f"`{sampler.get('max_nginx_5xx_delta_from_first_sample', 'n/a')}` | "
            f"`{summary.get('artifact_dir', '')}` |"
        )
    if any((item.get("summary") or {}).get("endpoint_breakdown") for item in report["candidates"]):
        lines.extend(["", "## Endpoint Latency Breakdown"])
        for item in report["candidates"]:
            rows = (item.get("summary") or {}).get("endpoint_breakdown") or []
            if not rows:
                continue
            lines.extend(
                [
                    "",
                    f"### Candidate `{item['candidate']}`",
                    "",
                    "| Endpoint | Failure | P95 | P99 | Avg | Max |",
                    "| --- | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for row in rows:
                lines.append(
                    f"| `{row.get('endpoint')}` | "
                    f"`{round(float(row.get('failure_rate') or 0) * 100, 3)}%` | "
                    f"`{round(float(row.get('p95_ms') or 0), 2)}ms` | "
                    f"`{round(float(row.get('p99_ms') or 0), 2)}ms` | "
                    f"`{round(float(row.get('avg_ms') or 0), 2)}ms` | "
                    f"`{round(float(row.get('max_ms') or 0), 2)}ms` |"
                )
    if report.get("recommendation"):
        lines.extend(["", f"Recommendation: {report['recommendation']}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recommend(report: dict[str, Any]) -> str:
    candidates = []
    for item in report["candidates"]:
        summary = item.get("summary") or {}
        k6 = summary.get("k6") or {}
        sampler = summary.get("sampler") or {}
        if not k6:
            continue
        candidates.append(
            {
                "label": item["candidate"],
                "status": summary.get("status"),
                "rps": float(k6.get("rps") or 0),
                "failure_rate": float(k6.get("failure_rate") if k6.get("failure_rate") is not None else 1),
                "p95": float(k6.get("p95_ms") or 10**9),
                "p99": float(k6.get("p99_ms") or 10**9),
                "dropped": int(k6.get("dropped_iterations") or 0),
                "dropped_ratio": float(
                    k6.get("dropped_iteration_ratio")
                    if k6.get("dropped_iteration_ratio") is not None
                    else dropped_iteration_ratio(
                        http_reqs=float(k6.get("http_reqs") or 0),
                        dropped_iterations=int(k6.get("dropped_iterations") or 0),
                    )
                ),
                "pg": int(sampler.get("max_postgres_connections") or 0),
                "nginx_5xx_delta": int(sampler.get("max_nginx_5xx_delta_from_first_sample") or 0),
            }
        )
    if not candidates:
        return "No candidate produced k6 metrics; keep the previous production pool profile."
    viable = [
        item for item in candidates
        if item["status"] == "passed"
        and item["rps"] >= float(report.get("target_rps") or 0) * 0.98
        and item["failure_rate"] < 0.02
        and item["p95"] < 1000
        and item["p99"] < 2500
        and item["dropped_ratio"] <= MAX_DROPPED_ITERATION_RATIO
        and item["nginx_5xx_delta"] == 0
    ]
    pool = viable or candidates
    best = min(pool, key=lambda item: (item["p95"], item["p99"], -item["rps"], item["pg"]))
    if viable:
        return (
            f"Prefer {best['label']} for the next full L7 rerun; it had the best latency among candidates "
            f"with failures under budget, bounded dropped iterations, and no 5xx delta."
        )
    return (
        f"No candidate passed the official L7 gates; {best['label']} had the best latency shape "
        "among failed candidates, but Stage L7 remains blocked until failures/5xx and dropped iterations are resolved."
    )


def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    candidates = parse_candidates(args.candidates)
    worker_candidates = parse_worker_candidates(args.workers, args.api_workers)
    stamp = args.timestamp or utc_stamp()
    matrix_dir = Path(args.artifact_root) / stamp / "load-pool-matrix"
    logs_dir = matrix_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    duration_seconds = parse_duration_seconds(args.duration)
    candidate_timeout = int(duration_seconds + args.candidate_timeout_padding_seconds)
    report: dict[str, Any] = {
        "started_at": utc_iso(),
        "timestamp": stamp,
        "iran_host": settings["IRAN_HOST"],
        "iran_project_dir": settings["IRAN_PROJECT_DIR"],
        "api_workers": args.api_workers,
        "worker_candidates": worker_candidates,
        "target_rps": args.target_rps,
        "duration": args.duration,
        "post_apply_settle_seconds": args.post_apply_settle_seconds,
        "artifact_dir": display_path(matrix_dir),
        "candidates": [],
    }
    restored = False
    try:
        for worker_count in worker_candidates:
            for candidate in candidates:
                candidate_label = f"w{worker_count}-{candidate.label}" if len(worker_candidates) > 1 else candidate.label
                candidate_stamp = f"{stamp}-l7pool-{candidate_label}"
                print(
                    f"[stage-l7.1] applying API_WORKERS={worker_count} "
                    f"DB_POOL_SIZE={candidate.pool_size} DB_MAX_OVERFLOW={candidate.max_overflow}",
                    flush=True,
                )
                apply_result = run_remote_script(
                    settings,
                    apply_pool_script(settings["IRAN_PROJECT_DIR"], stamp, candidate, worker_count),
                    timeout=240,
                )
                (logs_dir / f"{candidate_label}-apply.stdout.log").write_text(apply_result.stdout, encoding="utf-8")
                (logs_dir / f"{candidate_label}-apply.stderr.log").write_text(apply_result.stderr, encoding="utf-8")
                item: dict[str, Any] = {
                    "candidate": f"workers={worker_count} pool={candidate.pool_size}:{candidate.max_overflow}",
                    "candidate_stamp": candidate_stamp,
                    "api_workers": worker_count,
                    "pool_size": candidate.pool_size,
                    "max_overflow": candidate.max_overflow,
                    "theoretical_api_connections": worker_count * candidate.total_per_worker,
                    "apply": apply_result.__dict__,
                }
                if apply_result.returncode != 0:
                    item["status"] = "apply_failed"
                    report["candidates"].append(item)
                    write_json(matrix_dir / "results.json", report)
                    continue

                if args.post_apply_settle_seconds > 0:
                    print(
                        f"[stage-l7.1] settling app for {args.post_apply_settle_seconds:g}s before load",
                        flush=True,
                    )
                    time.sleep(args.post_apply_settle_seconds)

                print(f"[stage-l7.1] running {candidate_label} at {args.target_rps} RPS for {args.duration}", flush=True)
                command = realistic_load_args(args, candidate_stamp)
                run_result = run_local_command(command, timeout=candidate_timeout, env={})
                (logs_dir / f"{candidate_label}-realistic.stdout.log").write_text(run_result["stdout"], encoding="utf-8")
                (logs_dir / f"{candidate_label}-realistic.stderr.log").write_text(run_result["stderr"], encoding="utf-8")
                item["run"] = {key: value for key, value in run_result.items() if key not in {"stdout", "stderr"}}
                app_logs = run_remote_script(
                    settings,
                    collect_app_logs_script(settings["IRAN_PROJECT_DIR"]),
                    timeout=120,
                )
                (logs_dir / f"{candidate_label}-app.stdout.log").write_text(app_logs.stdout, encoding="utf-8")
                (logs_dir / f"{candidate_label}-app.stderr.log").write_text(app_logs.stderr, encoding="utf-8")
                item["app_logs"] = {key: value for key, value in app_logs.__dict__.items() if key not in {"stdout", "stderr"}}
                item["summary"] = summarize_candidate(args.artifact_root, candidate_stamp)
                report["candidates"].append(item)
                write_json(matrix_dir / "results.json", report)
    finally:
        print("[stage-l7.1] restoring original Iran DB pool env and app service", flush=True)
        restore = run_remote_script(settings, restore_pool_script(settings["IRAN_PROJECT_DIR"], stamp), timeout=240)
        report["restore"] = restore.__dict__
        restored = restore.returncode == 0

    report["restored"] = restored
    report["finished_at"] = utc_iso()
    report["recommendation"] = recommend(report)
    report["ok"] = restored and all((item.get("apply") or {}).get("returncode") == 0 for item in report["candidates"])
    write_json(matrix_dir / "results.json", report)
    write_summary(matrix_dir / "summary.md", report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_matrix(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"STAGE_L_POOL_MATRIX_DONE status=failed error={type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        f"STAGE_L_POOL_MATRIX_DONE status={'passed' if report.get('ok') else 'failed'} "
        f"artifact={report.get('artifact_dir')} restored={report.get('restored')}",
        file=sys.stderr,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Stage L7.1 pool matrix: {report['artifact_dir']}")
        print(report["recommendation"])
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
