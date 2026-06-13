#!/usr/bin/env python3
"""Sample production runtime state during Stage L realistic load runs."""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings
from scripts.report_production_alerts import SYNC_HEALTH_PROBE, collect_container_json, collect_python_probe, run_shell
from scripts.run_production_backup import HostTarget, target_for_role


REPO_ROOT = Path(__file__).resolve().parents[1]
STOP_REQUESTED = False

HOST_PROBE = r"""
import json
import os
import shutil
from pathlib import Path

def meminfo():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, raw = line.split(':', 1)
        parts = raw.strip().split()
        if parts:
            values[key] = int(parts[0]) * 1024
    total = values.get('MemTotal', 0)
    available = values.get('MemAvailable', 0)
    return {
        'total_bytes': total,
        'available_bytes': available,
        'used_percent': round(((total - available) / total) * 100, 2) if total else 0,
    }

def disk(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    used = usage.total - usage.free
    return {
        'path': path,
        'total_bytes': usage.total,
        'free_bytes': usage.free,
        'used_percent': round((used / usage.total) * 100, 2) if usage.total else 0,
    }

def netdev():
    rows = {}
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        if ':' not in line:
            continue
        name, raw = line.split(':', 1)
        parts = raw.split()
        if len(parts) >= 16:
            rows[name.strip()] = {
                'rx_bytes': int(parts[0]),
                'rx_packets': int(parts[1]),
                'tx_bytes': int(parts[8]),
                'tx_packets': int(parts[9]),
            }
    return rows

print(json.dumps({
    'ok': True,
    'loadavg': os.getloadavg(),
    'cpu_count': os.cpu_count(),
    'memory': meminfo(),
    'disk': [item for item in (disk('/'), disk('/srv'), disk('/var/lib/docker')) if item],
    'network': netdev(),
}, sort_keys=True))
"""

NGINX_PROBE = r"""
import json
from collections import Counter
from pathlib import Path

paths = [Path('/var/log/nginx/access.log'), Path('/var/log/nginx/error.log')]
payload = {'ok': True, 'access': {'seen': False, 'status_families': {}, 'status_codes': {}, 'sampled_lines': 0}, 'error': {'seen': False, 'line_count': 0}}

access = paths[0]
if access.exists():
    payload['access']['seen'] = True
    try:
        lines = access.read_text(errors='ignore').splitlines()[-5000:]
    except OSError:
        lines = []
    families = Counter()
    codes = Counter()
    for line in lines:
        parts = line.split()
        if len(parts) > 8 and parts[8].isdigit():
            code = parts[8]
            codes[code] += 1
            families[f'{code[0]}xx'] += 1
    payload['access']['sampled_lines'] = len(lines)
    payload['access']['status_families'] = dict(sorted(families.items()))
    payload['access']['status_codes'] = dict(sorted(codes.items()))

error = paths[1]
if error.exists():
    payload['error']['seen'] = True
    try:
        payload['error']['line_count'] = len(error.read_text(errors='ignore').splitlines()[-1000:])
    except OSError:
        payload['error']['line_count'] = 0

print(json.dumps(payload, sort_keys=True))
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample runtime state during Stage L load tests.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--roles", choices=("foreign", "iran", "both"), default="both")
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def request_stop(signum: int, frame: Any) -> None:
    del signum, frame
    global STOP_REQUESTED
    STOP_REQUESTED = True


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def roles_for(args: argparse.Namespace) -> list[str]:
    if args.roles == "both":
        return ["foreign", "iran"]
    return [args.roles]


def collect_docker_stats(target: HostTarget, settings: dict[str, str]) -> dict[str, Any]:
    result = run_shell(target, settings, "docker stats --no-stream --format '{{json .}}'", timeout=30)
    if not result["ok"]:
        return {"ok": False, "collector_error": {"exit_code": result["exit_code"], "stderr_tail": result["stderr"][-1000:]}}
    rows = []
    for line in result["stdout"].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"parse_error": True})
    return {"ok": True, "containers": rows, "container_count": len(rows)}


def collect_target_sample(target: HostTarget, settings: dict[str, str]) -> dict[str, Any]:
    return {
        "role": target.role,
        "captured_at": utc_iso(),
        "host": collect_python_probe(target, settings, HOST_PROBE, [], timeout=30),
        "docker": collect_docker_stats(target, settings),
        "postgres": collect_container_json(
            target,
            settings,
            "python scripts/report_postgres_runtime.py --json --no-fail --skip-settings-check",
            timeout=90,
        ),
        "redis": collect_container_json(
            target,
            settings,
            "python scripts/report_redis_runtime.py --json --no-fail",
            timeout=90,
        ),
        "sync": collect_container_json(
            target,
            settings,
            "python -c " + shlex.quote(SYNC_HEALTH_PROBE),
            timeout=90,
        ),
        "nginx": collect_python_probe(target, settings, NGINX_PROBE, [], timeout=30),
    }


def collect_sample(settings: dict[str, str], roles: list[str]) -> dict[str, Any]:
    return {
        "captured_at": utc_iso(),
        "hosts": [collect_target_sample(target_for_role(role, settings), settings) for role in roles],
    }


def safe_collect_sample(settings: dict[str, str], roles: list[str]) -> dict[str, Any]:
    try:
        return collect_sample(settings, roles)
    except Exception as exc:
        return {
            "captured_at": utc_iso(),
            "collector_error": {
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            },
            "hosts": [],
        }


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    host_count = sum(len(sample.get("hosts") or []) for sample in samples)
    collector_errors = 0
    max_connections = 0
    max_redis_memory = 0
    max_sync_backlog = 0
    max_nginx_5xx = 0
    baseline_nginx_5xx_by_role: dict[str, int] = {}
    max_nginx_5xx_delta = 0
    for sample in samples:
        if sample.get("collector_error"):
            collector_errors += 1
        for host in sample.get("hosts") or []:
            for key in ("host", "docker", "postgres", "redis", "sync", "nginx"):
                if (host.get(key) or {}).get("collector_error"):
                    collector_errors += 1
            budget = ((host.get("postgres") or {}).get("connection_budget") or {})
            max_connections = max(max_connections, int(budget.get("current_connections") or 0))
            memory = ((host.get("redis") or {}).get("memory") or {})
            max_redis_memory = max(max_redis_memory, int(memory.get("used_memory") or 0))
            sync = host.get("sync") or {}
            max_sync_backlog = max(max_sync_backlog, int(sync.get("unsynced_change_log_count") or 0))
            families = (((host.get("nginx") or {}).get("access") or {}).get("status_families") or {})
            nginx_5xx = int(families.get("5xx") or 0)
            max_nginx_5xx = max(max_nginx_5xx, nginx_5xx)
            role = str(host.get("role") or "unknown")
            baseline = baseline_nginx_5xx_by_role.setdefault(role, nginx_5xx)
            max_nginx_5xx_delta = max(max_nginx_5xx_delta, max(0, nginx_5xx - baseline))
    return {
        "sample_count": len(samples),
        "host_sample_count": host_count,
        "collector_errors": collector_errors,
        "max_postgres_connections": max_connections,
        "max_redis_used_memory": max_redis_memory,
        "max_sync_backlog": max_sync_backlog,
        "max_nginx_5xx_in_log_window": max_nginx_5xx,
        "max_nginx_5xx_delta_from_first_sample": max_nginx_5xx_delta,
        "nginx_5xx_count_in_log_windows": max_nginx_5xx,
        "ok": collector_errors == 0,
    }


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    summary = payload.get("summary") or {}
    lines = [
        "# Stage L4 Production Load Sampler",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Dry run: `{payload.get('dry_run')}`",
        f"- Samples: `{summary.get('sample_count')}`",
        f"- Host samples: `{summary.get('host_sample_count')}`",
        f"- Collector errors: `{summary.get('collector_errors')}`",
        f"- Max PostgreSQL connections: `{summary.get('max_postgres_connections')}`",
        f"- Max Redis used memory: `{summary.get('max_redis_used_memory')}`",
        f"- Max sync backlog: `{summary.get('max_sync_backlog')}`",
        f"- Max Nginx 5xx in a sampled log window: `{summary.get('max_nginx_5xx_in_log_window')}`",
        f"- Max Nginx 5xx delta from first sample: `{summary.get('max_nginx_5xx_delta_from_first_sample')}`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_sampler(args: argparse.Namespace) -> dict[str, Any]:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    stamp = args.timestamp or utc_stamp()
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else Path(args.artifact_root) / stamp / "load-sampler"
    samples_dir = artifact_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "status": "running",
        "stage": "L4",
        "dry_run": args.dry_run,
        "started_at": utc_iso(),
        "artifact_dir": display_path(artifact_dir),
        "roles": roles_for(args),
        "interval_seconds": args.interval_seconds,
        "duration_seconds": args.duration_seconds,
        "requested_sample_count": args.sample_count,
    }

    if args.dry_run:
        payload["status"] = "passed"
        payload["contract"] = {
            "collectors": ["host", "docker", "postgres", "redis", "sync", "nginx"],
            "raw_logs": False,
            "redacted": True,
        }
        payload["summary"] = summarize([])
        payload["finished_at"] = utc_iso()
        write_json(artifact_dir / "results.json", payload)
        write_summary(artifact_dir / "summary.md", payload)
        return payload

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    roles = roles_for(args)
    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    index = 0
    while True:
        index += 1
        sample = safe_collect_sample(settings, roles)
        samples.append(sample)
        write_json(samples_dir / f"sample-{index:04d}.json", sample)
        reached_count = args.sample_count > 0 and index >= args.sample_count
        reached_duration = args.duration_seconds > 0 and (time.monotonic() - started) >= args.duration_seconds
        if reached_count or reached_duration or STOP_REQUESTED:
            break
        sleep_until = time.monotonic() + max(1.0, args.interval_seconds)
        while time.monotonic() < sleep_until and not STOP_REQUESTED:
            time.sleep(min(1.0, sleep_until - time.monotonic()))

    if STOP_REQUESTED and (not samples or not samples[-1].get("final")):
        index += 1
        sample = safe_collect_sample(settings, roles)
        sample["final"] = True
        samples.append(sample)
        write_json(samples_dir / f"sample-{index:04d}.json", sample)

    payload["samples"] = [display_path(samples_dir / f"sample-{item:04d}.json") for item in range(1, len(samples) + 1)]
    payload["summary"] = summarize(samples)
    payload["status"] = "passed" if payload["summary"].get("ok") else "failed"
    payload["finished_at"] = utc_iso()
    write_json(artifact_dir / "results.json", payload)
    write_summary(artifact_dir / "summary.md", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_sampler(args)
    if args.json or args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
