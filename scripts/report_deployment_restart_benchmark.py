#!/usr/bin/env python3
"""Run the Stage P10 production deployment/restart/backup benchmark."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import (
    DEFAULT_ARTIFACT_ROOT,
    compose_probe_script,
    display_path,
    quote_remote,
    remote_args,
    utc_iso,
    utc_stamp,
)
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_EVENT_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+(?P<message>.+)$")
PHASE_MARKERS = {
    "local_validation": ("Checking local prerequisites", "Local checks passed"),
    "foreign_deploy": ("Deploying the foreign server locally", "Synchronizing host mappings"),
    "build": ("Building frontend locally", "Local release build complete"),
    "sync": ("Syncing production payload to the Iran host", "Production payload sync complete"),
    "image_ship": ("Uploading Docker image bundle to the Iran host", "Docker image bundle upload complete"),
    "image_load": ("Loading transferred Docker images on the Iran host", "Docker images loaded on the Iran host"),
    "iran_deploy": ("Deploying Docker services on the Iran host", "Iran deploy step complete"),
    "shared_data": ("Skipping Iran shared-table seed/reset", "Skipping Iran shared-table seed/reset"),
    "final_health": ("Running post-deploy health checks", "Health checks passed"),
}


@dataclass
class CommandResult:
    name: str
    args: list[str]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


class DeploymentBenchmarkError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run production deployment/restart/backup probes.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--release-timeout", type=int, default=1800)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-release", action="store_true", help="Skip the full production release rehearsal.")
    parser.add_argument("--skip-postgres-restart", action="store_true", help="Skip the controlled PostgreSQL restart probe.")
    return parser.parse_args(argv)


def command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def parse_json_output(stdout: str) -> dict[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise DeploymentBenchmarkError("Command did not print a JSON object on stdout")


def parse_log_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_release_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous: datetime | None = None
    for raw_line in text.splitlines():
        match = LOG_EVENT_RE.match(raw_line.strip())
        if not match:
            continue
        current = parse_log_timestamp(match.group("ts"))
        events.append(
            {
                "timestamp": match.group("ts"),
                "message": match.group("message"),
                "delta_since_previous_seconds": round((current - previous).total_seconds(), 3) if previous else None,
            }
        )
        previous = current
    return events


def summarize_release_phases(events: list[dict[str, Any]]) -> dict[str, Any]:
    phase_summary: dict[str, Any] = {}
    for phase, (start_marker, end_marker) in PHASE_MARKERS.items():
        start_event = next((event for event in events if start_marker in event["message"]), None)
        if not start_event:
            phase_summary[phase] = {"status": "not_seen"}
            continue
        if start_marker == end_marker:
            phase_summary[phase] = {"status": "seen", "start": start_event["timestamp"], "duration_seconds": 0.0}
            continue
        start_index = events.index(start_event)
        end_event = next((event for event in events[start_index:] if end_marker in event["message"]), None)
        if not end_event:
            phase_summary[phase] = {"status": "incomplete", "start": start_event["timestamp"]}
            continue
        duration = parse_log_timestamp(end_event["timestamp"]) - parse_log_timestamp(start_event["timestamp"])
        phase_summary[phase] = {
            "status": "measured",
            "start": start_event["timestamp"],
            "end": end_event["timestamp"],
            "duration_seconds": round(duration.total_seconds(), 3),
        }
    return phase_summary


def sync_health_is_clean(payload: dict[str, Any]) -> bool:
    queues = payload.get("redis_queues") or {}
    return (
        payload.get("status") == "ok"
        and int(payload.get("unsynced_change_log_count") or 0) == 0
        and int(queues.get("sync:outbound") or 0) == 0
        and int(queues.get("sync:retry") or 0) == 0
    )


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Runner:
    def __init__(self, *, settings: dict[str, str], logs_dir: Path):
        self.settings = settings
        self.logs_dir = logs_dir
        self.results: list[dict[str, Any]] = []

    def run(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 60,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.perf_counter()
        completed = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env={**os.environ, **(env or {})},
        )
        result = CommandResult(
            name=name,
            args=args,
            exit_code=completed.returncode,
            duration_seconds=round(time.perf_counter() - started, 3),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        self._write_logs(result)
        if check and result.exit_code != 0:
            raise DeploymentBenchmarkError(f"{name} failed with exit code {result.exit_code}")
        return result

    def run_json(self, name: str, args: list[str], *, timeout: int = 60, check: bool = True) -> dict[str, Any]:
        result = self.run(name, args, timeout=timeout, check=check)
        payload = parse_json_output(result.stdout)
        self.results[-1]["json"] = payload
        return payload

    def _write_logs(self, result: CommandResult) -> None:
        stdout_path = self.logs_dir / f"{result.name}.stdout.log"
        stderr_path = self.logs_dir / f"{result.name}.stderr.log"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        self.results.append(
            {
                "name": result.name,
                "command": command_display(result.args),
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "stdout_path": display_path(stdout_path),
                "stderr_path": display_path(stderr_path),
            }
        )

    def compose_args(self, role: str, body: str) -> list[str]:
        if role == "foreign":
            command = f"cd {shlex.quote(str(REPO_ROOT))} && " + compose_probe_script("docker-compose.yml", body)
            return ["bash", "-lc", command]
        command = f"cd {quote_remote(self.settings['IRAN_PROJECT_DIR'])} && " + compose_probe_script("docker-compose.iran.yml", body)
        return remote_args(self.settings, command)

    def remote_json(self, name: str, script: str, *, timeout: int = 120, check: bool = True) -> dict[str, Any]:
        return self.run_json(name, remote_args(self.settings, script), timeout=timeout, check=check)

    def app_json(self, role: str, name: str, *script_args: str, timeout: int = 60) -> dict[str, Any]:
        body = "exec -T app " + " ".join(shlex.quote(part) for part in ("python", *script_args))
        return self.run_json(f"{role}_{name}", self.compose_args(role, body), timeout=timeout)


def remote_compose_prelude(project_dir: str) -> str:
    return f"""
set -euo pipefail
cd {shlex.quote(project_dir)}
if docker compose version >/dev/null 2>&1; then
  compose_cmd='docker compose'
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd='docker-compose'
else
  echo 'No Docker Compose command is available.' >&2
  exit 125
fi
"""


def backup_script(project_dir: str, stamp: str) -> str:
    backup_path = f"/srv/trading-bot/backups/p10-deployment-{stamp}.sql"
    return f"""
{remote_compose_prelude(project_dir)}
mkdir -p /srv/trading-bot/backups
backup_path={shlex.quote(backup_path)}
docker exec trading_bot_db sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$backup_path"
bytes="$(wc -c < "$backup_path" | tr -d ' ')"
sha="$(sha256sum "$backup_path" | awk '{{print $1}}')"
python3 - "$backup_path" "$bytes" "$sha" <<'PY'
import json, sys
print(json.dumps(
    {{
        "status": "ok",
        "path": sys.argv[1],
        "bytes": int(sys.argv[2]),
        "sha256": sys.argv[3],
    }},
    sort_keys=True,
))
PY
"""


def restart_service_script(project_dir: str, service: str, *, app_health: bool = True) -> str:
    health_block = """
for i in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
    ready_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
    duration_ms=$((ready_ms - start_ms))
    printf '{"status":"ok","service":"%s","duration_ms":%s,"health":"api_config"}\\n' "$service" "$duration_ms"
    exit 0
  fi
  sleep 2
done
echo "API did not become ready after $service restart" >&2
exit 124
""" if app_health else """
for i in $(seq 1 45); do
  state="$(docker inspect -f '{{.State.Running}}' "trading_bot_${service}" 2>/dev/null || true)"
  if [ "$state" = "true" ]; then
    ready_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
    duration_ms=$((ready_ms - start_ms))
    printf '{"status":"ok","service":"%s","duration_ms":%s,"health":"container_running"}\\n' "$service" "$duration_ms"
    exit 0
  fi
  sleep 2
done
echo "$service did not become running after restart" >&2
exit 124
"""
    return f"""
{remote_compose_prelude(project_dir)}
service={shlex.quote(service)}
start_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
$compose_cmd -f docker-compose.iran.yml restart "$service"
{health_block}
"""


def restart_redis_script(project_dir: str) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
start_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
$compose_cmd -f docker-compose.iran.yml restart redis
for i in $(seq 1 60); do
  if docker exec trading_bot_redis redis-cli ping 2>/dev/null | grep -q PONG; then
    if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
      ready_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
      duration_ms=$((ready_ms - start_ms))
      printf '{{"status":"ok","service":"redis","duration_ms":%s,"health":"redis_ping_and_api_config"}}\\n' "$duration_ms"
      exit 0
    fi
  fi
  sleep 2
done
echo "Redis/API did not become ready after Redis restart" >&2
exit 124
"""


def restart_postgres_script(project_dir: str) -> str:
    return f"""
{remote_compose_prelude(project_dir)}
start_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
docker restart trading_bot_db >/dev/null
for i in $(seq 1 90); do
  if docker exec trading_bot_db sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    if curl -fsS --max-time 3 http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
      ready_ms="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
      duration_ms=$((ready_ms - start_ms))
      printf '{{"status":"ok","service":"postgres","duration_ms":%s,"health":"pg_isready_and_api_config"}}\\n' "$duration_ms"
      exit 0
    fi
  fi
  sleep 2
done
echo "PostgreSQL/API did not become ready after DB restart; attempting app/sync_worker recovery" >&2
$compose_cmd -f docker-compose.iran.yml restart app sync_worker >/dev/null 2>&1 || true
exit 124
"""


def rollback_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    backup = payload.get("checks", {}).get("backup") or {}
    return {
        "backup_path": backup.get("path"),
        "rollback_commands": [
            "git revert <P10 commit> && make production-online-sync",
            "make production-online-deploy && make production-online-health",
            "If a DB restore is required, stop app/sync_worker first, restore the recorded pg_dump backup into PostgreSQL, then restart app/sync_worker and verify sync-health.",
        ],
        "notes": [
            "P10 does not run destructive shared-table reset.",
            "The recorded pg_dump backup is created before the controlled PostgreSQL restart probe.",
        ],
    }


def evaluate_gates(checks: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    release = checks.get("release") or {}
    if release and release.get("status") != "passed":
        failures.append("production release rehearsal failed")
    backup = checks.get("backup") or {}
    if backup.get("status") != "ok" or int(backup.get("bytes") or 0) <= 0:
        failures.append("pg_dump backup was not created before restart probes")
    for name, restart in (checks.get("restarts") or {}).items():
        if restart.get("status") != "ok":
            failures.append(f"{name} restart did not recover cleanly")
    for role, health in (checks.get("sync_health") or {}).items():
        if not sync_health_is_clean(health):
            failures.append(f"{role} sync-health is not clean")
    return {"status": "failed" if failures else "passed", "failures": failures}


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    checks = payload.get("checks") or {}
    release = checks.get("release") or {}
    phases = release.get("phases") or {}
    restarts = checks.get("restarts") or {}
    gates = payload.get("gates") or {}
    lines = [
        "# Stage P10 Deployment Restart Benchmark",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Started at: `{payload.get('started_at')}`",
        f"- Finished at: `{payload.get('finished_at')}`",
        "",
        "## Release Phases",
        "",
        "| Phase | Status | Duration |",
        "| --- | --- | ---: |",
    ]
    for phase, item in phases.items():
        lines.append(f"| `{phase}` | `{item.get('status')}` | `{item.get('duration_seconds', '')}s` |")
    backup = checks.get("backup") or {}
    lines.extend([
        "",
        "## Backup",
        "",
        f"- Status: `{backup.get('status')}`",
        f"- Path: `{backup.get('path')}`",
        f"- Size: `{backup.get('bytes')}` bytes",
        "",
        "## Restart Probes",
        "",
        "| Service | Status | Recovery duration | Health check |",
        "| --- | --- | ---: | --- |",
    ])
    for service, item in restarts.items():
        duration = item.get("duration_ms")
        duration_text = f"{round(float(duration) / 1000, 3)}s" if duration is not None else ""
        lines.append(f"| `{service}` | `{item.get('status')}` | `{duration_text}` | `{item.get('health')}` |")
    lines.extend(["", "## Sync Health", ""])
    for role, health in sorted((checks.get("sync_health") or {}).items()):
        queues = health.get("redis_queues") or {}
        lines.append(
            f"- `{role}`: status `{health.get('status')}`, unsynced `{health.get('unsynced_change_log_count')}`, "
            f"outbound `{queues.get('sync:outbound')}`, retry `{queues.get('sync:retry')}`"
        )
    lines.extend(["", "## Rollback", ""])
    for command in payload.get("rollback", {}).get("rollback_commands", []):
        lines.append(f"- `{command}`")
    lines.extend(["", "## Gates", ""])
    if gates.get("failures"):
        for failure in gates["failures"]:
            lines.append(f"- Failure: {failure}")
    else:
        lines.append("- Failures: `0`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    stamp = args.timestamp or utc_stamp()
    artifact_root = Path(args.artifact_root)
    run_dir = artifact_root / stamp
    scenario_dir = run_dir / "deployment-p10"
    logs_dir = scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    settings = resolve_deploy_settings(manifest_path=args.manifest)
    runner = Runner(settings=settings, logs_dir=logs_dir)
    checks: dict[str, Any] = {"restarts": {}, "sync_health": {}}
    started_at = utc_iso()
    status = "passed"
    error: str | None = None

    try:
        if args.skip_release:
            checks["release"] = {"status": "skipped", "phases": {}}
        else:
            release_result = runner.run(
                "production_release_rehearsal",
                ["bash", "./scripts/production_deploy_online.sh", "--manifest", args.manifest or "./deploy/production/online.env"],
                timeout=args.release_timeout,
                env={"IRAN_CONNECTIVITY_MODE": "yes", "IRAN_SHARED_DATA_MODE": "skip"},
            )
            events = parse_release_events(release_result.stdout + "\n" + release_result.stderr)
            checks["release"] = {
                "status": "passed",
                "duration_seconds": release_result.duration_seconds,
                "events": events,
                "phases": summarize_release_phases(events),
            }
        checks["backup"] = runner.remote_json(
            "iran_pg_dump_backup",
            backup_script(settings["IRAN_PROJECT_DIR"], stamp),
            timeout=300,
        )
        checks["restarts"]["app"] = runner.remote_json(
            "iran_restart_app",
            restart_service_script(settings["IRAN_PROJECT_DIR"], "app", app_health=True),
            timeout=240,
        )
        checks["restarts"]["sync_worker"] = runner.remote_json(
            "iran_restart_sync_worker",
            restart_service_script(settings["IRAN_PROJECT_DIR"], "sync_worker", app_health=False),
            timeout=180,
        )
        checks["restarts"]["redis"] = runner.remote_json("iran_restart_redis", restart_redis_script(settings["IRAN_PROJECT_DIR"]), timeout=240)
        if args.skip_postgres_restart:
            checks["restarts"]["postgres"] = {"status": "skipped"}
        else:
            checks["restarts"]["postgres"] = runner.remote_json(
                "iran_restart_postgres",
                restart_postgres_script(settings["IRAN_PROJECT_DIR"]),
                timeout=300,
            )
        checks["sync_health"]["iran"] = runner.app_json("iran", "sync_health", "scripts/sync_probe_worker.py", "health", timeout=60)
        checks["sync_health"]["foreign"] = runner.app_json("foreign", "sync_health", "scripts/sync_probe_worker.py", "health", timeout=60)
    except Exception as exc:
        status = "failed"
        error = str(exc)
        try:
            checks["sync_health"]["iran"] = runner.app_json("iran", "sync_health_after_failure", "scripts/sync_probe_worker.py", "health", timeout=60)
        except Exception:
            pass
        try:
            checks["sync_health"]["foreign"] = runner.app_json("foreign", "sync_health_after_failure", "scripts/sync_probe_worker.py", "health", timeout=60)
        except Exception:
            pass

    gates = evaluate_gates(checks)
    if gates["status"] != "passed":
        status = "failed"
        error = error or "deployment restart gates failed"
    payload = {
        "status": status,
        "error": error,
        "started_at": started_at,
        "finished_at": utc_iso(),
        "artifact_dir": display_path(scenario_dir),
        "checks": checks,
        "gates": gates,
        "commands": runner.results,
    }
    payload["rollback"] = rollback_manifest(payload)
    write_json(scenario_dir / "results.json", payload)
    write_summary(scenario_dir / "summary.md", payload)
    if status != "passed":
        raise DeploymentBenchmarkError(error or "Stage P10 deployment restart benchmark failed")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_benchmark(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
