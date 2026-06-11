#!/usr/bin/env python3
"""Capture a redacted production baseline before optimization work.

The script is intentionally read-only. It gathers host/runtime settings, Docker
status, selected PostgreSQL/Redis settings, and sync-health snapshots from the
foreign host and the Iran host.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "production-benchmark"
SENSITIVE_KEY_PARTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "API_KEY",
    "WEBHOOK",
    "DSN",
    "SALT",
)
SAFE_MANIFEST_KEYS = (
    "DEPLOY_MANIFEST",
    "FOREIGN_PUBLIC_IP",
    "FOREIGN_PUBLIC_DOMAIN",
    "FOREIGN_SERVER_URL",
    "FOREIGN_SERVER_DOMAIN",
    "FOREIGN_FRONTEND_URL",
    "IRAN_HOST",
    "IRAN_SSH_USER",
    "IRAN_SSH_PORT",
    "IRAN_PROJECT_DIR",
    "IRAN_PUBLIC_IP",
    "IRAN_PUBLIC_DOMAIN",
    "IRAN_APP_DOMAIN",
    "IRAN_SERVER_URL",
    "IRAN_SERVER_DOMAIN",
    "IRAN_FRONTEND_URL",
    "IRAN_HEALTHCHECK_URL",
    "FOREIGN_API_WORKERS",
    "IRAN_API_WORKERS",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "IRAN_DB_POOL_SIZE",
    "IRAN_DB_MAX_OVERFLOW",
    "DB_POOL_RECYCLE_SECONDS",
    "DB_POOL_PRE_PING",
)
RUNTIME_ENV_KEYS = (
    "API_WORKERS",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_RECYCLE_SECONDS",
    "DB_POOL_PRE_PING",
    "BACKGROUND_LEADER_LOCK_TTL_SECONDS",
    "BACKGROUND_LEADER_LOCK_REFRESH_SECONDS",
    "BACKGROUND_LEADER_RETRY_SECONDS",
    "TRADING_BOT_SERVICE",
    "TRADING_BOT_METRICS_BACKEND",
    "FRONTEND_URL",
    "SERVER_URL",
    "SERVER_DOMAIN",
)
POSTGRES_SETTINGS = (
    "max_connections",
    "shared_buffers",
    "effective_cache_size",
    "work_mem",
    "maintenance_work_mem",
    "max_worker_processes",
    "max_parallel_workers",
    "max_parallel_workers_per_gather",
    "wal_buffers",
    "checkpoint_timeout",
    "max_wal_size",
    "min_wal_size",
    "random_page_cost",
    "effective_io_concurrency",
)
REMAINING_STAGES = (
    "P1 - Full-product benchmark harness",
    "P2 - PostgreSQL production tuning",
    "P3 - Redis durability and queue safety",
    "P4 - Nginx, static assets, and media delivery",
    "P5 - Worker and pool recalibration",
    "P6 - Cross-server sync and recovery hardening",
    "P7 - Trading core, market, and bot workloads",
    "P8 - Frontend UX performance beyond Messenger",
    "P9 - Observability, audit, and alert readiness",
    "P10 - Deployment, restart, backup, and rollback benchmark",
    "P11 - Final release gate",
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_sensitive_key(key: str) -> bool:
    normalized = key.upper()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_mapping(values: dict[str, str], *, allow_keys: tuple[str, ...] = SAFE_MANIFEST_KEYS) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key in allow_keys:
        if key not in values:
            continue
        redacted[key] = "[REDACTED]" if is_sensitive_key(key) else values.get(key, "")
    return redacted


def quote_remote(value: str) -> str:
    return shlex.quote(value)


def command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def run_command(
    *,
    name: str,
    args: list[str],
    logs_dir: Path,
    cwd: Path | None = REPO_ROOT,
    timeout: int = 30,
) -> dict[str, Any]:
    started = time.perf_counter()
    stdout_path = logs_dir / f"{name}.stdout.log"
    stderr_path = logs_dir / f"{name}.stderr.log"
    timed_out = False
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr = f"{stderr}\nTIMEOUT after {timeout}s".strip()
    elapsed = round(time.perf_counter() - started, 3)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "name": name,
        "command": command_display(args),
        "exit_code": returncode,
        "duration_seconds": elapsed,
        "timed_out": timed_out,
        "stdout_path": display_path(stdout_path),
        "stderr_path": display_path(stderr_path),
    }


def remote_args(settings: dict[str, str], command: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        settings.get("IRAN_SSH_PORT", "22"),
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


def runtime_env_probe() -> str:
    keys = ",".join(repr(key) for key in RUNTIME_ENV_KEYS)
    return (
        "exec -T app python -c "
        + shlex.quote(
            "import json, os; "
            f"keys=[{keys}]; "
            "print(json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True))"
        )
    )


def sync_health_probe() -> str:
    return (
        "exec -T app python -c "
        + shlex.quote(
            "import os, urllib.request; "
            "req=urllib.request.Request('http://127.0.0.1:8000/api/sync/health', "
            "headers={'X-Observability-Api-Key': os.environ.get('OBSERVABILITY_API_KEY', '')}); "
            "print(urllib.request.urlopen(req, timeout=15).read().decode())"
        )
    )


def postgres_settings_probe() -> str:
    names = ",".join(f"'{name}'" for name in POSTGRES_SETTINGS)
    sql = (
        "select name || '=' || setting || coalesce(' ' || nullif(unit, ''), '') "
        f"from pg_settings where name in ({names}) order by name;"
    )
    return "exec -T db sh -lc " + shlex.quote(f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc {shlex.quote(sql)}')


def redis_info_probe() -> str:
    return "exec -T redis redis-cli INFO server memory persistence stats"


def local_compose_args(compose_file: str, body: str) -> list[str]:
    return ["bash", "-lc", compose_probe_script(compose_file, body)]


def remote_compose_args(settings: dict[str, str], body: str) -> list[str]:
    project_dir = quote_remote(settings["IRAN_PROJECT_DIR"])
    command = f"cd {project_dir} && " + compose_probe_script("docker-compose.iran.yml", body)
    return remote_args(settings, command)


def extract_unsynced_values(payload: Any) -> list[int]:
    values: list[int] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if "unsynced" in lowered and isinstance(value, int):
                values.append(value)
            else:
                values.extend(extract_unsynced_values(value))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(extract_unsynced_values(item))
    return values


def parse_sync_health(stdout_path: Path) -> dict[str, Any]:
    text = stdout_path.read_text(encoding="utf-8").strip()
    if not text:
        return {"parsed": False, "clean": False, "reason": "empty output"}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"parsed": False, "clean": False, "reason": f"invalid json: {exc.msg}"}
    values = extract_unsynced_values(payload)
    return {
        "parsed": True,
        "clean": bool(values) and all(value == 0 for value in values),
        "unsynced_values": values,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(
    *,
    path: Path,
    artifact_dir: Path,
    metadata: dict[str, Any],
    command_results: list[dict[str, Any]],
    sync_summary: dict[str, Any],
) -> None:
    failed = [item for item in command_results if item["exit_code"] != 0]
    lines = [
        "# Production Baseline Snapshot",
        "",
        f"- Captured at: `{metadata['captured_at']}`",
        f"- Git SHA: `{metadata.get('git_sha', 'unknown')}`",
        f"- Artifact dir: `{display_path(artifact_dir)}`",
        f"- Commands: `{len(command_results)}` total, `{len(failed)}` failed",
        f"- Sync health clean: `{sync_summary.get('clean', False)}`",
        "",
        "## Sync Health",
        "",
        f"- Foreign: `{sync_summary.get('foreign', {}).get('clean', False)}`",
        f"- Iran: `{sync_summary.get('iran', {}).get('clean', False)}`",
        "",
        "## Failed Commands",
        "",
    ]
    if failed:
        for item in failed:
            lines.append(f"- `{item['name']}` exit `{item['exit_code']}`; see `{item['stderr_path']}`")
    else:
        lines.append("- None")
    lines.extend(["", "## Remaining Roadmap Stages", ""])
    for stage in REMAINING_STAGES:
        lines.append(f"- {stage}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a production optimization baseline snapshot.")
    parser.add_argument("--manifest", default=None, help="Production deployment manifest path.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT), help="Directory for benchmark artifacts.")
    parser.add_argument("--timestamp", default=None, help="Stable timestamp for reproducible tests.")
    parser.add_argument("--no-ssh", action="store_true", help="Skip Iran SSH probes.")
    parser.add_argument("--no-docker", action="store_true", help="Skip Docker/Compose probes.")
    parser.add_argument("--allow-dirty-sync", action="store_true", help="Exit 0 even when sync-health is not clean.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = args.timestamp or utc_stamp()
    artifact_dir = Path(args.artifact_root) / stamp / "baseline"
    logs_dir = artifact_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    metadata: dict[str, Any] = {
        "captured_at": utc_iso(),
        "stage": "P0",
        "repo_root": str(REPO_ROOT),
        "manifest": redact_mapping(settings),
    }

    commands: list[tuple[str, list[str], int]] = [
        ("git_sha", ["git", "rev-parse", "HEAD"], 15),
        ("git_status", ["git", "status", "--short"], 15),
        ("foreign_date_utc", ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], 15),
        ("foreign_hostname", ["hostname"], 15),
        ("foreign_uname", ["uname", "-a"], 15),
        ("foreign_nproc", ["nproc"], 15),
        ("foreign_memory", ["free", "-h"], 15),
        ("foreign_disk_root", ["df", "-h", "/"], 15),
        ("foreign_lsblk", ["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,ROTA,MODEL"], 15),
    ]
    if not args.no_docker:
        commands.extend(
            [
                ("foreign_docker_ps", ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"], 30),
                ("foreign_compose_ps", local_compose_args("docker-compose.yml", "ps"), 30),
                ("foreign_app_runtime_env", local_compose_args("docker-compose.yml", runtime_env_probe()), 30),
                ("foreign_postgres_settings", local_compose_args("docker-compose.yml", postgres_settings_probe()), 30),
                ("foreign_redis_info", local_compose_args("docker-compose.yml", redis_info_probe()), 30),
                ("foreign_sync_health", local_compose_args("docker-compose.yml", sync_health_probe()), 30),
            ]
        )
    if not args.no_ssh:
        remote_basics = (
            ("iran_date_utc", "date -u +%Y-%m-%dT%H:%M:%SZ", 15),
            ("iran_hostname", "hostname", 15),
            ("iran_uname", "uname -a", 15),
            ("iran_nproc", "nproc", 15),
            ("iran_memory", "free -h", 15),
            ("iran_disk_root", "df -h /", 15),
            ("iran_lsblk", "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,ROTA,MODEL", 15),
            ("iran_docker_ps", "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'", 30),
        )
        commands.extend((name, remote_args(settings, command), timeout) for name, command, timeout in remote_basics)
        if not args.no_docker:
            commands.extend(
                [
                    ("iran_compose_ps", remote_compose_args(settings, "ps"), 30),
                    ("iran_app_runtime_env", remote_compose_args(settings, runtime_env_probe()), 30),
                    ("iran_postgres_settings", remote_compose_args(settings, postgres_settings_probe()), 30),
                    ("iran_redis_info", remote_compose_args(settings, redis_info_probe()), 30),
                    ("iran_sync_health", remote_compose_args(settings, sync_health_probe()), 30),
                ]
            )

    results = [run_command(name=name, args=cmd, logs_dir=logs_dir, timeout=timeout) for name, cmd, timeout in commands]
    git_sha_result = next((item for item in results if item["name"] == "git_sha" and item["exit_code"] == 0), None)
    if git_sha_result:
        metadata["git_sha"] = (REPO_ROOT / git_sha_result["stdout_path"]).read_text(encoding="utf-8").strip()

    sync_summary: dict[str, Any] = {"clean": False}
    by_name = {item["name"]: item for item in results}
    for key, command_name in (("foreign", "foreign_sync_health"), ("iran", "iran_sync_health")):
        item = by_name.get(command_name)
        if not item:
            sync_summary[key] = {"parsed": False, "clean": False, "reason": "not captured"}
            continue
        sync_summary[key] = parse_sync_health(REPO_ROOT / item["stdout_path"]) if item["exit_code"] == 0 else {
            "parsed": False,
            "clean": False,
            "reason": f"command failed with exit {item['exit_code']}",
        }
    sync_summary["clean"] = bool(sync_summary.get("foreign", {}).get("clean") and sync_summary.get("iran", {}).get("clean"))

    write_json(artifact_dir / "metadata.json", metadata)
    write_json(artifact_dir / "commands.json", results)
    write_json(artifact_dir / "sync-health-summary.json", sync_summary)
    write_summary(
        path=artifact_dir / "summary.md",
        artifact_dir=artifact_dir,
        metadata=metadata,
        command_results=results,
        sync_summary=sync_summary,
    )

    print(json.dumps({
        "artifact_dir": display_path(artifact_dir),
        "sync_health_clean": sync_summary["clean"],
        "failed_commands": [item["name"] for item in results if item["exit_code"] != 0],
    }, ensure_ascii=False, sort_keys=True))
    if sync_summary["clean"] or args.allow_dirty_sync:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
