#!/usr/bin/env python3
"""Evaluate production DB/Redis/sync/disk/backup alert thresholds.

This is intentionally a read-only operational probe. It does not require a
metrics exporter and it does not run inside request/job hot paths.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import DEFAULT_ARTIFACT_ROOT, display_path, remote_args, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings
from scripts.run_production_backup import DEFAULT_BACKUP_DIR, HostTarget, target_for_role


REPO_ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
SEVERITY_RANK = {"ok": 0, "warning": 1, "critical": 2}

DISK_PATHS = ("/", "/srv", "/srv/trading-bot", "/srv/trading-bot/backups", "/var/lib/docker")

DISK_PROBE = r"""
import json
import os
import shutil
import sys

items = []
seen = set()
for path in sys.argv[1:]:
    if not os.path.exists(path):
        continue
    try:
        stat = os.stat(path)
        key = stat.st_dev
        usage = shutil.disk_usage(path)
    except OSError:
        continue
    if key in seen:
        continue
    seen.add(key)
    used = usage.total - usage.free
    items.append({
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": used,
        "free_bytes": usage.free,
        "used_percent": round((used / usage.total) * 100, 2) if usage.total else 0,
    })
print(json.dumps({"ok": True, "filesystems": items}, sort_keys=True))
"""

BACKUP_PROBE = r"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

role, backup_dir = sys.argv[1], sys.argv[2]
root = Path(backup_dir)
now = datetime.now(timezone.utc)
payload = {
    "ok": False,
    "role": role,
    "backup_dir": backup_dir,
    "latest_manifest": None,
    "latest_created_at": None,
    "latest_status": None,
    "age_seconds": None,
    "artifact_count": 0,
    "error": None,
}
if not root.exists():
    payload["error"] = "backup_dir_missing"
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(0)

candidates = sorted(root.glob(f"{role}-backup-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
payload["artifact_count"] = len(candidates)
if not candidates:
    payload["error"] = "backup_manifest_missing"
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(0)

latest = candidates[0]
try:
    manifest = json.loads(latest.read_text(encoding="utf-8"))
except Exception as exc:
    payload["latest_manifest"] = str(latest)
    payload["error"] = f"backup_manifest_unreadable:{type(exc).__name__}"
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(0)

created_raw = str(manifest.get("created_at") or "")
created = None
if created_raw:
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        created = None
if created is None:
    created = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)

files = manifest.get("files") or []
required = {"db", "redis", "uploads", "audit"}
present = {str(item.get("kind")) for item in files if isinstance(item, dict)}
missing = sorted(required - present)
empty = [
    str(item.get("kind"))
    for item in files
    if isinstance(item, dict) and int(item.get("bytes") or 0) <= 0
]

payload.update({
    "ok": manifest.get("status") == "ok" and not missing and not empty,
    "latest_manifest": str(latest),
    "latest_created_at": created.isoformat().replace("+00:00", "Z"),
    "latest_status": manifest.get("status"),
    "age_seconds": max(0, int((now - created).total_seconds())),
    "missing_artifacts": missing,
    "empty_artifacts": empty,
})
print(json.dumps(payload, sort_keys=True))
"""

SYNC_HEALTH_PROBE = (
    "import json, os, urllib.request; "
    "req = urllib.request.Request("
    "'http://127.0.0.1:8000/api/sync/health', "
    "headers={'X-Observability-Api-Key': os.environ.get('OBSERVABILITY_API_KEY', '')}"
    "); "
    "print(urllib.request.urlopen(req, timeout=15).read().decode())"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate production alert thresholds.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--roles", choices=("foreign", "iran", "both"), default="both")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-out", default=None)
    parser.add_argument("--fail-on", choices=("never", "warning", "critical"), default="critical")
    return parser.parse_args(argv)


def run_shell(target: HostTarget, settings: dict[str, str], shell: str, *, timeout: int = 60) -> dict[str, Any]:
    if target.remote:
        command = f"cd {shlex.quote(target.project_dir)} && {shell}"
        args = remote_args(settings, command)
        cwd = None
    else:
        args = ["bash", "-lc", f"cd {shlex.quote(target.project_dir)} && {shell}"]
        cwd = REPO_ROOT
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
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr if isinstance(exc.stderr, str) else "") + f"\nTIMEOUT after {timeout}s",
        }


def parse_json_output(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            return payload
    except ValueError:
        pass
    for raw_line in reversed(stdout.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("command did not emit a JSON object")


def compose_exec_shell(target: HostTarget, command: str) -> str:
    return (
        "if docker compose version >/dev/null 2>&1; then compose_cmd='docker compose'; "
        "elif command -v docker-compose >/dev/null 2>&1; then compose_cmd='docker-compose'; "
        "else echo 'No Docker Compose command is available.' >&2; exit 125; fi; "
        f"$compose_cmd -f {shlex.quote(target.compose_file)} exec -T app {command}"
    )


def collect_container_json(
    target: HostTarget,
    settings: dict[str, str],
    command: str,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    result = run_shell(target, settings, compose_exec_shell(target, command), timeout=timeout)
    if not result["ok"]:
        return {
            "ok": False,
            "collector_error": {
                "exit_code": result["exit_code"],
                "stderr_tail": result["stderr"][-1000:],
            },
        }
    try:
        payload = parse_json_output(result["stdout"])
    except ValueError as exc:
        return {"ok": False, "collector_error": {"error": str(exc), "stdout_tail": result["stdout"][-1000:]}}
    return payload


def collect_python_probe(
    target: HostTarget,
    settings: dict[str, str],
    code: str,
    args: list[str],
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    shell = "python3 -c " + shlex.quote(code)
    for arg in args:
        shell += " " + shlex.quote(arg)
    result = run_shell(target, settings, shell, timeout=timeout)
    if not result["ok"]:
        return {
            "ok": False,
            "collector_error": {
                "exit_code": result["exit_code"],
                "stderr_tail": result["stderr"][-1000:],
            },
        }
    try:
        return parse_json_output(result["stdout"])
    except ValueError as exc:
        return {"ok": False, "collector_error": {"error": str(exc), "stdout_tail": result["stdout"][-1000:]}}


def collect_host_report(target: HostTarget, settings: dict[str, str], *, backup_dir: str) -> dict[str, Any]:
    return {
        "role": target.role,
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
        "disk": collect_python_probe(target, settings, DISK_PROBE, list(DISK_PATHS), timeout=30),
        "backup": collect_python_probe(target, settings, BACKUP_PROBE, [target.role, backup_dir], timeout=30),
    }


def add_alert(
    alerts: list[dict[str, Any]],
    *,
    role: str,
    component: str,
    severity: str,
    metric: str,
    value: Any,
    threshold: str,
    summary: str,
) -> None:
    alerts.append(
        {
            "role": role,
            "component": component,
            "severity": severity,
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "summary": summary,
        }
    )


def alert_if_collector_failed(alerts: list[dict[str, Any]], role: str, component: str, payload: dict[str, Any]) -> bool:
    if payload.get("collector_error"):
        add_alert(
            alerts,
            role=role,
            component=component,
            severity="critical",
            metric="collector_error",
            value=payload["collector_error"],
            threshold="collector must return JSON",
            summary=f"{component} alert probe failed on {role}",
        )
        return True
    return False


def evaluate_host_report(host: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(host.get("role") or "unknown")
    alerts: list[dict[str, Any]] = []

    postgres = host.get("postgres") or {}
    if not alert_if_collector_failed(alerts, role, "postgres", postgres):
        budget = postgres.get("connection_budget") or {}
        current_connections = int(budget.get("current_connections") or 0)
        if current_connections > 300:
            add_alert(
                alerts,
                role=role,
                component="postgres",
                severity="critical",
                metric="current_connections",
                value=current_connections,
                threshold="> 300",
                summary="PostgreSQL connections are above the critical threshold",
            )
        elif current_connections > 200:
            add_alert(
                alerts,
                role=role,
                component="postgres",
                severity="warning",
                metric="current_connections",
                value=current_connections,
                threshold="> 200",
                summary="PostgreSQL connections are above the warning threshold",
            )
        idle = postgres.get("idle_in_transaction") or {}
        old_count = int(idle.get("old_count") or 0)
        max_age = float(idle.get("max_age_seconds") or 0)
        if old_count > 0 and max_age > 900:
            add_alert(
                alerts,
                role=role,
                component="postgres",
                severity="critical",
                metric="idle_in_transaction_max_age_seconds",
                value=round(max_age, 2),
                threshold="> 900 seconds",
                summary="PostgreSQL has long idle-in-transaction sessions",
            )
        elif old_count > 0:
            add_alert(
                alerts,
                role=role,
                component="postgres",
                severity="warning",
                metric="idle_in_transaction_old_count",
                value=old_count,
                threshold="> 0 older than 3 minutes",
                summary="PostgreSQL has old idle-in-transaction sessions",
            )

    redis = host.get("redis") or {}
    if not alert_if_collector_failed(alerts, role, "redis", redis):
        memory = redis.get("memory") or {}
        used_memory = int(memory.get("used_memory") or 0)
        if used_memory > 8 * GIB:
            severity = "critical"
            threshold = "> 8GiB"
        elif used_memory > 4 * GIB:
            severity = "warning"
            threshold = "> 4GiB"
        else:
            severity = ""
            threshold = ""
        if severity:
            add_alert(
                alerts,
                role=role,
                component="redis",
                severity=severity,
                metric="used_memory",
                value=used_memory,
                threshold=threshold,
                summary="Redis memory usage is above the initial production threshold",
            )
        persistence = redis.get("persistence") or {}
        for key in ("aof_last_write_status", "aof_last_bgrewrite_status"):
            value = str(persistence.get(key) or "").lower()
            if value and value != "ok":
                add_alert(
                    alerts,
                    role=role,
                    component="redis",
                    severity="critical",
                    metric=key,
                    value=value,
                    threshold="ok",
                    summary="Redis AOF persistence status is not ok",
                )

    sync = host.get("sync") or {}
    if not alert_if_collector_failed(alerts, role, "sync", sync):
        unsynced = int(sync.get("unsynced_change_log_count") or 0)
        oldest = float(sync.get("oldest_unsynced_age_seconds") or 0)
        queues = sync.get("redis_queues") or {}
        retry = int(queues.get("sync:retry") or 0)
        outbound = int(queues.get("sync:outbound") or 0)
        if unsynced > 1000:
            add_alert(alerts, role=role, component="sync", severity="critical", metric="unsynced_change_log_count", value=unsynced, threshold="> 1000", summary="Cross-server sync backlog is critically high")
        elif unsynced > 100:
            add_alert(alerts, role=role, component="sync", severity="warning", metric="unsynced_change_log_count", value=unsynced, threshold="> 100", summary="Cross-server sync backlog is high")
        elif unsynced > 0:
            add_alert(alerts, role=role, component="sync", severity="warning", metric="unsynced_change_log_count", value=unsynced, threshold="> 0 after retry window", summary="Cross-server sync backlog is non-zero")
        if oldest > 3600:
            add_alert(alerts, role=role, component="sync", severity="critical", metric="oldest_unsynced_age_seconds", value=round(oldest, 2), threshold="> 3600 seconds", summary="Cross-server sync lag is critically old")
        elif oldest > 900:
            add_alert(alerts, role=role, component="sync", severity="warning", metric="oldest_unsynced_age_seconds", value=round(oldest, 2), threshold="> 900 seconds", summary="Cross-server sync lag is high")
        if retry > 100:
            add_alert(alerts, role=role, component="sync", severity="critical", metric="sync:retry", value=retry, threshold="> 100", summary="Sync retry queue is critically high")
        elif retry > 0:
            add_alert(alerts, role=role, component="sync", severity="warning", metric="sync:retry", value=retry, threshold="> 0", summary="Sync retry queue is non-empty")
        if outbound > 1000:
            add_alert(alerts, role=role, component="sync", severity="critical", metric="sync:outbound", value=outbound, threshold="> 1000", summary="Sync outbound queue is critically high")

    disk = host.get("disk") or {}
    if not alert_if_collector_failed(alerts, role, "disk", disk):
        for item in disk.get("filesystems") or []:
            used = float(item.get("used_percent") or 0)
            if used > 85:
                severity = "critical"
                threshold = "> 85%"
            elif used > 75:
                severity = "warning"
                threshold = "> 75%"
            else:
                continue
            add_alert(
                alerts,
                role=role,
                component="disk",
                severity=severity,
                metric=f"{item.get('path')}:used_percent",
                value=used,
                threshold=threshold,
                summary="Host disk usage is above the production threshold",
            )

    backup = host.get("backup") or {}
    if not alert_if_collector_failed(alerts, role, "backup", backup):
        age = backup.get("age_seconds")
        if not backup.get("ok"):
            add_alert(
                alerts,
                role=role,
                component="backup",
                severity="critical",
                metric="latest_backup_status",
                value={
                    "status": backup.get("latest_status"),
                    "error": backup.get("error"),
                    "missing_artifacts": backup.get("missing_artifacts"),
                    "empty_artifacts": backup.get("empty_artifacts"),
                },
                threshold="status=ok with db/redis/uploads/audit artifacts",
                summary="Latest production backup is missing or incomplete",
            )
        if age is None:
            add_alert(
                alerts,
                role=role,
                component="backup",
                severity="critical",
                metric="latest_backup_age_seconds",
                value=None,
                threshold="<= 48h",
                summary="No production backup age could be determined",
            )
        else:
            age_int = int(age)
            if age_int > 48 * 3600:
                add_alert(
                    alerts,
                    role=role,
                    component="backup",
                    severity="critical",
                    metric="latest_backup_age_seconds",
                    value=age_int,
                    threshold="<= 48h",
                    summary="Latest production backup is older than 48 hours",
                )
            elif age_int > 24 * 3600:
                add_alert(
                    alerts,
                    role=role,
                    component="backup",
                    severity="warning",
                    metric="latest_backup_age_seconds",
                    value=age_int,
                    threshold="<= 24h",
                    summary="Latest production backup is older than 24 hours",
                )

    return alerts


def overall_status(alerts: list[dict[str, Any]]) -> str:
    rank = 0
    for alert in alerts:
        rank = max(rank, SEVERITY_RANK.get(str(alert.get("severity")), 0))
    if rank >= SEVERITY_RANK["critical"]:
        return "critical"
    if rank >= SEVERITY_RANK["warning"]:
        return "warning"
    return "ok"


def should_fail(status: str, fail_on: str) -> bool:
    if fail_on == "never":
        return False
    return SEVERITY_RANK[status] >= SEVERITY_RANK[fail_on]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Production Alerts Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Status: `{payload['status']}`",
        f"- Artifact dir: `{payload['artifact_dir']}`",
        "",
        "## Alerts",
        "",
    ]
    if not payload["alerts"]:
        lines.append("- No warning or critical alerts.")
    else:
        for alert in payload["alerts"]:
            lines.append(
                f"- `{alert['severity']}` `{alert['role']}` `{alert['component']}` "
                f"`{alert['metric']}`: {alert['summary']} "
                f"(value `{alert['value']}`, threshold `{alert['threshold']}`)"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def selected_roles(value: str) -> list[str]:
    if value == "both":
        return ["foreign", "iran"]
    return [value]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    stamp = args.timestamp or utc_stamp()
    artifact_dir = Path(args.artifact_root) / stamp / "production-alerts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    hosts: dict[str, Any] = {}
    alerts: list[dict[str, Any]] = []
    for role in selected_roles(args.roles):
        target = target_for_role(role, settings)
        report = collect_host_report(target, settings, backup_dir=args.backup_dir)
        hosts[role] = report
        alerts.extend(evaluate_host_report(report))

    alerts.sort(key=lambda item: (-SEVERITY_RANK.get(str(item.get("severity")), 0), item.get("role", ""), item.get("component", "")))
    status = overall_status(alerts)
    payload = {
        "status": status,
        "generated_at": utc_iso(),
        "artifact_dir": display_path(artifact_dir),
        "manifest": settings["DEPLOY_MANIFEST"],
        "hosts": hosts,
        "alerts": alerts,
    }
    write_json(artifact_dir / "results.json", payload)
    write_markdown(artifact_dir / "summary.md", payload)
    if args.report_out:
        write_markdown(REPO_ROOT / args.report_out, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Production alerts report {status}: {display_path(artifact_dir / 'summary.md')}")
    return 2 if should_fail(status, args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
