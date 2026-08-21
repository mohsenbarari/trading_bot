#!/usr/bin/env python3
"""Capture a redacted production baseline before optimization work.

The script is intentionally read-only. It gathers host/runtime settings, Docker
status, selected PostgreSQL/Redis settings, and sync-health snapshots from the
foreign host and the Iran host.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import stat
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
PROCESS_TERMINATION_GRACE_SECONDS = 5.0
PROCESS_KILL_TIMEOUT_SECONDS = 5.0
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
    "POSTGRES_MAX_CONNECTIONS",
    "POSTGRES_SHARED_BUFFERS",
    "POSTGRES_EFFECTIVE_CACHE_SIZE",
    "POSTGRES_WORK_MEM",
    "POSTGRES_MAINTENANCE_WORK_MEM",
    "POSTGRES_RANDOM_PAGE_COST",
    "POSTGRES_EFFECTIVE_IO_CONCURRENCY",
    "POSTGRES_CHECKPOINT_TIMEOUT",
    "POSTGRES_MAX_WAL_SIZE",
    "POSTGRES_MIN_WAL_SIZE",
    "POSTGRES_WAL_BUFFERS",
    "IRAN_POSTGRES_MAX_CONNECTIONS",
    "IRAN_POSTGRES_SHARED_BUFFERS",
    "IRAN_POSTGRES_EFFECTIVE_CACHE_SIZE",
    "IRAN_POSTGRES_WORK_MEM",
    "IRAN_POSTGRES_MAINTENANCE_WORK_MEM",
    "IRAN_POSTGRES_RANDOM_PAGE_COST",
    "IRAN_POSTGRES_EFFECTIVE_IO_CONCURRENCY",
    "IRAN_POSTGRES_CHECKPOINT_TIMEOUT",
    "IRAN_POSTGRES_MAX_WAL_SIZE",
    "IRAN_POSTGRES_MIN_WAL_SIZE",
    "IRAN_POSTGRES_WAL_BUFFERS",
    "REDIS_APPENDONLY",
    "REDIS_APPENDFSYNC",
    "REDIS_MAXMEMORY",
    "REDIS_MAXMEMORY_POLICY",
)
RUNTIME_ENV_KEYS = (
    "API_WORKERS",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_RECYCLE_SECONDS",
    "DB_POOL_PRE_PING",
    "POSTGRES_MAX_CONNECTIONS",
    "POSTGRES_SHARED_BUFFERS",
    "POSTGRES_EFFECTIVE_CACHE_SIZE",
    "POSTGRES_WORK_MEM",
    "POSTGRES_MAINTENANCE_WORK_MEM",
    "POSTGRES_RANDOM_PAGE_COST",
    "POSTGRES_EFFECTIVE_IO_CONCURRENCY",
    "POSTGRES_CHECKPOINT_TIMEOUT",
    "POSTGRES_MAX_WAL_SIZE",
    "POSTGRES_MIN_WAL_SIZE",
    "POSTGRES_WAL_BUFFERS",
    "REDIS_APPENDONLY",
    "REDIS_APPENDFSYNC",
    "REDIS_MAXMEMORY",
    "REDIS_MAXMEMORY_POLICY",
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


def _process_group_has_live_members(process_group_id: int) -> bool:
    """Return true only while a non-zombie member remains in the process group."""
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat_fields = entry.joinpath("stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
                state = stat_fields[0]
                process_group = int(stat_fields[2])
            except (OSError, IndexError, ValueError):
                continue
            if process_group == int(process_group_id) and state != "Z":
                return True
        return False
    try:
        os.killpg(int(process_group_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(process_group_id: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _process_group_has_live_members(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def _close_process_pipes(process: subprocess.Popen[str]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _stop_process_group(
    process: subprocess.Popen[str],
    *,
    process_group_id: int,
    grace_seconds: float | None = None,
    kill_seconds: float | None = None,
) -> tuple[str, str]:
    grace_seconds = float(
        PROCESS_TERMINATION_GRACE_SECONDS
        if grace_seconds is None
        else grace_seconds
    )
    kill_seconds = float(
        PROCESS_KILL_TIMEOUT_SECONDS if kill_seconds is None else kill_seconds
    )
    term_deadline = time.monotonic() + max(grace_seconds, 0.0)
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    communicate_timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        communicate_timed_out = True
        stdout = stderr = ""
    group_stopped = _wait_for_process_group_exit(
        process_group_id,
        max(0.0, term_deadline - time.monotonic()),
    )
    if communicate_timed_out or process.poll() is None or not group_stopped:
        kill_deadline = time.monotonic() + max(kill_seconds, 0.0)
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kill_communicate_timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=kill_seconds)
        except subprocess.TimeoutExpired:
            kill_communicate_timed_out = True
            _close_process_pipes(process)
        group_stopped = _wait_for_process_group_exit(
            process_group_id,
            max(0.0, kill_deadline - time.monotonic()),
        )
        if kill_communicate_timed_out or not group_stopped:
            raise RuntimeError(
                "baseline command process group did not stop within bounded cleanup"
            ) from None
    if process.poll() is None:
        raise RuntimeError("baseline command process leader did not terminate")
    return stdout or "", stderr or ""


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
    process = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    process_group_id = process.pid
    try:
        stdout, stderr = process.communicate(timeout=float(timeout))
        returncode = int(process.returncode or 0)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = 124
        stdout, stderr = _stop_process_group(
            process,
            process_group_id=process_group_id,
        )
        stderr = f"{stderr}\nTIMEOUT after {timeout}s".strip()
    except BaseException:
        _stop_process_group(process, process_group_id=process_group_id)
        raise
    if not timed_out and _process_group_has_live_members(process_group_id):
        stdout, stderr = _stop_process_group(
            process,
            process_group_id=process_group_id,
        )
        returncode = 125
        stderr = (
            f"{stderr}\nCONTAINMENT ERROR: command leader exited while live "
            "process-group members remained"
        ).strip()
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


def _iran_transport_contract(
    settings: dict[str, str],
    *,
    require_explicit_identity: bool = False,
) -> tuple[str, str, str | None]:
    host = str(settings.get("IRAN_HOST") or "")
    user = str(settings.get("IRAN_SSH_USER") or "root")
    port = str(settings.get("IRAN_SSH_PORT") or "37067")
    method = settings.get("IRAN_SSH_AUTH_METHOD", "key").lower()
    if method != "key":
        raise ValueError("production recoverability transport requires key authentication")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
        raise ValueError("invalid Iran SSH port")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", user):
        raise ValueError("invalid Iran SSH user")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host)
        or ".." in host
        or "%" in host
    ):
        raise ValueError("invalid Iran SSH host")
    identity_value = settings.get("IRAN_SSH_PRIVATE_KEY_PATH") or ""
    if require_explicit_identity and not identity_value:
        raise ValueError("production recoverability requires an explicit Iran SSH identity file")
    if not identity_value:
        return f"{user}@{host}", port, None
    supplied = Path(identity_value)
    resolved = supplied.resolve()
    if (
        not supplied.is_absolute()
        or supplied != resolved
        or supplied.is_symlink()
        or not supplied.is_file()
        or stat.S_IMODE(supplied.stat().st_mode) not in {0o400, 0o600}
        or supplied.stat().st_uid not in {0, os.geteuid()}
    ):
        raise ValueError("invalid Iran SSH identity file")
    return f"{user}@{host}", port, str(supplied)


def validate_production_iran_transport(settings: dict[str, str]) -> None:
    _iran_transport_contract(settings, require_explicit_identity=True)


def remote_transport_args(settings: dict[str, str], *, scp: bool = False) -> list[str]:
    target, port, identity = _iran_transport_contract(settings)
    args = [
        "scp" if scp else "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-P" if scp else "-p",
        port,
    ]
    if identity:
        args.extend(("-i", identity))
    if not scp:
        args.append(target)
    return args


def remote_args(settings: dict[str, str], command: str) -> list[str]:
    return [*remote_transport_args(settings), command]


def remote_scp_args(settings: dict[str, str], source: str, destination: str) -> list[str]:
    return [*remote_transport_args(settings, scp=True), source, destination]


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
    if not args.no_ssh:
        validate_production_iran_transport(settings)
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
