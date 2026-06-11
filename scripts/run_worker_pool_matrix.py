#!/usr/bin/env python3
"""Run the Stage P5 API worker matrix on the Iran production host.

The script changes only API_WORKERS in the Iran runtime .env, recreates the app
service for each candidate, runs the redaction-safe authenticated/chat HTTP
probe inside the app container, records Docker stats samples, then restores the
original .env and app service before exiting.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, utc_stamp
from scripts.deploy_config import resolve_deploy_settings


BENCHMARK_BEGIN = "__P5_BENCHMARK_JSON_BEGIN__"
BENCHMARK_END = "__P5_BENCHMARK_JSON_END__"
STATS_BEGIN = "__P5_DOCKER_STATS_BEGIN__"
STATS_END = "__P5_DOCKER_STATS_END__"


@dataclass(frozen=True)
class RemoteResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run API worker matrix benchmark on the Iran host.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--workers", default="8,12,16", help="Comma-separated worker candidates.")
    parser.add_argument("--requests", type=int, default=240)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default for automation.")
    return parser.parse_args(argv)


def parse_worker_candidates(raw: str) -> list[int]:
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


def update_env_content(content: str, updates: dict[str, str]) -> str:
    lines = content.splitlines()
    seen: set[str] = set()
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            rendered.append(line)
    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={value}")
    return "\n".join(rendered) + "\n"


def extract_marked_section(stdout: str, begin: str, end: str) -> str:
    pattern = re.compile(re.escape(begin) + r"\n(?P<body>.*?)\n" + re.escape(end), re.DOTALL)
    match = pattern.search(stdout)
    if not match:
        raise ValueError(f"Missing marked section {begin}..{end}")
    return match.group("body").strip()


def parse_docker_stats_lines(raw: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            samples.append(json.loads(line))
        except json.JSONDecodeError:
            samples.append({"parse_error": True, "raw": line[:120]})
    return samples


def parse_percent(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_memory_bytes(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if "/" in text:
        text = text.split("/", 1)[0].strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B|B)", text, flags=re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    return int(number * factors.get(unit, 1))


def summarize_docker_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    cpu_values = [value for value in (parse_percent(item.get("CPUPerc")) for item in samples) if value is not None]
    memory_values = [value for value in (parse_memory_bytes(item.get("MemUsage")) for item in samples) if value is not None]
    return {
        "sample_count": len(samples),
        "parse_error_count": sum(1 for item in samples if item.get("parse_error")),
        "peak_cpu_percent": round(max(cpu_values), 3) if cpu_values else None,
        "mean_cpu_percent": round(sum(cpu_values) / len(cpu_values), 3) if cpu_values else None,
        "peak_memory_bytes": max(memory_values) if memory_values else None,
    }


def recommend_worker_count(candidate_reports: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [
        item for item in candidate_reports
        if item.get("ok") and item.get("benchmark", {}).get("ok")
    ]
    if not successful:
        baseline = candidate_reports[0]["workers"] if candidate_reports else None
        return {
            "recommended_workers": baseline,
            "decision": "keep_baseline",
            "reason": "No candidate completed the benchmark safely.",
        }

    baseline = successful[0]
    baseline_summary = baseline["benchmark"]["workload"]["summary"]
    baseline_rps = float(baseline["benchmark"]["workload"]["requests_per_second"])
    baseline_p95 = float(baseline_summary["p95_ms"])
    baseline_p99 = float(baseline_summary["p99_ms"])
    accepted = [baseline]
    rejected: list[dict[str, Any]] = []

    for item in successful[1:]:
        summary = item["benchmark"]["workload"]["summary"]
        rps = float(item["benchmark"]["workload"]["requests_per_second"])
        p95 = float(summary["p95_ms"])
        p99 = float(summary["p99_ms"])
        db_safe = bool(item["benchmark"]["runtime"].get("estimated_within_limit")) and bool(
            item["benchmark"]["runtime"].get("peak_connections_within_limit")
        )
        latency_better = p95 <= baseline_p95 * 0.95 or p99 <= baseline_p99 * 0.95
        throughput_better = rps >= baseline_rps * 1.10 and p95 <= baseline_p95 * 1.05 and p99 <= baseline_p99 * 1.05
        if db_safe and (latency_better or throughput_better):
            accepted.append(item)
        else:
            rejected.append(
                {
                    "workers": item["workers"],
                    "db_safe": db_safe,
                    "latency_better": latency_better,
                    "throughput_better": throughput_better,
                    "p95_ms": p95,
                    "p99_ms": p99,
                    "rps": rps,
                }
            )

    best = min(
        accepted,
        key=lambda item: (
            float(item["benchmark"]["workload"]["summary"]["p95_ms"]),
            -float(item["benchmark"]["workload"]["requests_per_second"]),
        ),
    )
    if best["workers"] == baseline["workers"]:
        return {
            "recommended_workers": baseline["workers"],
            "decision": "keep_baseline",
            "reason": "Higher worker counts did not clear the latency/throughput gates without tradeoffs.",
            "rejected_candidates": rejected,
        }
    return {
        "recommended_workers": best["workers"],
        "decision": "increase_workers",
        "reason": "A higher worker count cleared the p95/p99 or throughput gate while staying inside the DB pressure budget.",
        "rejected_candidates": rejected,
    }


def ssh_base(settings: dict[str, str]) -> list[str]:
    return [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        settings.get("IRAN_SSH_PORT", "22"),
        f"{settings.get('IRAN_SSH_USER', 'root')}@{settings['IRAN_HOST']}",
        "bash",
        "-s",
    ]


def run_remote_script(settings: dict[str, str], script: str, *, timeout: int) -> RemoteResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            ssh_base(settings),
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return RemoteResult(
            returncode=124,
            stdout=stdout,
            stderr=f"{stderr}\nTIMEOUT after {timeout}s".strip(),
            duration_seconds=round(time.perf_counter() - started, 3),
        )
    return RemoteResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=round(time.perf_counter() - started, 3),
    )


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


def sh_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def restart_and_wait_script(project_dir: str, workers: int, stamp: str) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
env_file=".env"
backup_file=".env.p5-worker-matrix-{stamp}.bak"
if [ ! -f "$backup_file" ]; then
  cp "$env_file" "$backup_file"
fi
python3 - "$env_file" <<'PY'
import sys
from pathlib import Path
from scripts.run_worker_pool_matrix import update_env_content
path = Path(sys.argv[1])
path.write_text(update_env_content(path.read_text(encoding="utf-8"), {{"API_WORKERS": "{workers}"}}), encoding="utf-8")
PY
$compose_cmd -f docker-compose.iran.yml rm -sf app >/dev/null 2>&1 || docker rm -f trading_bot_app >/dev/null 2>&1 || true
$compose_cmd -f docker-compose.iran.yml up -d --no-deps app
for i in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
    echo "app_ready workers={workers}"
    exit 0
  fi
  sleep 2
done
echo "app did not become ready for workers={workers}" >&2
exit 124
"""


def run_candidate_script(project_dir: str, requests: int, concurrency: int, benchmark_timeout_seconds: int) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
stats_file="$(mktemp)"
timeout 8s docker stats --no-stream --format '{{{{json .}}}}' trading_bot_app >> "$stats_file" 2>/dev/null || true
set +e
echo "{BENCHMARK_BEGIN}"
timeout {benchmark_timeout_seconds}s $compose_cmd -f docker-compose.iran.yml exec -T app python scripts/report_worker_http_benchmark.py --json --requests {requests} --concurrency {concurrency} < /dev/null
benchmark_code="$?"
echo "{BENCHMARK_END}"
timeout 8s docker stats --no-stream --format '{{{{json .}}}}' trading_bot_app >> "$stats_file" 2>/dev/null || true
echo "{STATS_BEGIN}"
cat "$stats_file"
echo "{STATS_END}"
rm -f "$stats_file"
exit "$benchmark_code"
"""


def restore_script(project_dir: str, stamp: str) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
env_file=".env"
backup_file=".env.p5-worker-matrix-{stamp}.bak"
if [ -f "$backup_file" ]; then
  cp "$backup_file" "$env_file"
fi
$compose_cmd -f docker-compose.iran.yml rm -sf app >/dev/null 2>&1 || docker rm -f trading_bot_app >/dev/null 2>&1 || true
$compose_cmd -f docker-compose.iran.yml up -d --no-deps app
for i in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
    echo "app_restored"
    exit 0
  fi
  sleep 2
done
echo "app did not become ready after restore" >&2
exit 124
"""


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Stage P5 Worker Matrix Summary",
        "",
        f"- Started at: `{report['started_at']}`",
        f"- Iran host: `{report['iran_host']}`",
        f"- Candidates: `{', '.join(str(item) for item in report['workers'])}`",
        f"- Recommendation: `{report['recommendation']['recommended_workers']}`",
        f"- Decision: `{report['recommendation']['decision']}`",
        f"- Reason: {report['recommendation']['reason']}",
        "",
        "| Workers | Status | RPS | P95 | P99 | Error rate | Peak DB conn | Peak CPU | Peak RSS |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["candidates"]:
        benchmark = item.get("benchmark") or {}
        summary = benchmark.get("workload", {}).get("summary", {})
        runtime = benchmark.get("runtime", {})
        docker_stats = item.get("docker_stats_summary", {})
        peak_db = benchmark.get("connections", {}).get("samples", {}).get("peak_total")
        lines.append(
            f"| `{item['workers']}` | `{'passed' if item.get('ok') else 'failed'}` | "
            f"`{benchmark.get('workload', {}).get('requests_per_second', 'n/a')}` | "
            f"`{summary.get('p95_ms', 'n/a')}ms` | `{summary.get('p99_ms', 'n/a')}ms` | "
            f"`{summary.get('error_rate', 'n/a')}` | "
            f"`{peak_db}/{runtime.get('safe_connection_limit', 'n/a')}` | "
            f"`{docker_stats.get('peak_cpu_percent', 'n/a')}%` | "
            f"`{docker_stats.get('peak_memory_bytes', 'n/a')}` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    workers = parse_worker_candidates(args.workers)
    stamp = args.timestamp or utc_stamp()
    run_dir = Path(args.artifact_root) / stamp / "workers"
    run_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "started_at": stamp,
        "iran_host": settings["IRAN_HOST"],
        "iran_project_dir": settings["IRAN_PROJECT_DIR"],
        "workers": workers,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "artifact_dir": display_path(run_dir),
        "candidates": [],
    }
    restored = False
    try:
        for worker_count in workers:
            print(f"[worker-matrix] restarting Iran app with API_WORKERS={worker_count}", flush=True)
            restart = run_remote_script(
                settings,
                restart_and_wait_script(settings["IRAN_PROJECT_DIR"], worker_count, stamp),
                timeout=240,
            )
            candidate: dict[str, Any] = {
                "workers": worker_count,
                "restart": restart.__dict__,
                "ok": False,
            }
            if restart.returncode != 0:
                candidate["error"] = "restart_failed"
                report["candidates"].append(candidate)
                write_json(run_dir / "results.json", report)
                continue

            print(f"[worker-matrix] running authenticated/chat benchmark for API_WORKERS={worker_count}", flush=True)
            benchmark_result = run_remote_script(
                settings,
                run_candidate_script(
                    settings["IRAN_PROJECT_DIR"],
                    args.requests,
                    args.concurrency,
                    max(60, args.timeout - 120),
                ),
                timeout=args.timeout,
            )
            candidate["benchmark_command"] = {
                "returncode": benchmark_result.returncode,
                "duration_seconds": benchmark_result.duration_seconds,
                "stderr_tail": benchmark_result.stderr[-2000:],
            }
            (run_dir / f"workers-{worker_count}.stdout.log").write_text(benchmark_result.stdout, encoding="utf-8")
            (run_dir / f"workers-{worker_count}.stderr.log").write_text(benchmark_result.stderr, encoding="utf-8")
            try:
                benchmark_json = extract_marked_section(benchmark_result.stdout, BENCHMARK_BEGIN, BENCHMARK_END)
                stats_raw = extract_marked_section(benchmark_result.stdout, STATS_BEGIN, STATS_END)
                benchmark = json.loads(benchmark_json)
                docker_stats = parse_docker_stats_lines(stats_raw)
                candidate["benchmark"] = benchmark
                candidate["docker_stats_summary"] = summarize_docker_stats(docker_stats)
                candidate["completed"] = True
                candidate["ok"] = bool(benchmark.get("ok"))
                candidate["benchmark_returncode"] = benchmark_result.returncode
            except Exception as exc:
                candidate["error"] = f"parse_failed:{type(exc).__name__}"
            report["candidates"].append(candidate)
            write_json(run_dir / "results.json", report)
    finally:
        print("[worker-matrix] restoring original Iran API_WORKERS env and app service", flush=True)
        restore = run_remote_script(settings, restore_script(settings["IRAN_PROJECT_DIR"], stamp), timeout=240)
        report["restore"] = restore.__dict__
        restored = restore.returncode == 0

    report["restored"] = restored
    report["recommendation"] = recommend_worker_count(report["candidates"])
    baseline_ok = bool(report["candidates"] and report["candidates"][0].get("ok"))
    all_candidates_completed = all(item.get("completed") for item in report["candidates"])
    report["ok"] = restored and baseline_ok and all_candidates_completed
    write_json(run_dir / "results.json", report)
    write_summary(run_dir / "summary.md", report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_matrix(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Stage P5 worker matrix: {display_path(Path(report['artifact_dir']))}")
        print(f"Recommendation: {report['recommendation']['recommended_workers']} ({report['recommendation']['decision']})")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
