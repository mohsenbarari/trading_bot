#!/usr/bin/env python3
"""Create an operational production backup on the foreign or Iran host.

The script intentionally runs outside the hot path. It creates a PostgreSQL
dump, Redis data archive, uploads archive, and audit-trail archive, then prints
a compact JSON manifest with file sizes and hashes. Optional DB restore smoke
uses a temporary PostgreSQL container and never touches the production DB.
"""

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import display_path, remote_args, utc_iso, utc_stamp
from scripts.deploy_config import resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = "/srv/trading-bot/backups"


@dataclass(frozen=True)
class HostTarget:
    role: str
    project_dir: str
    compose_file: str
    remote: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create production backup artifacts.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--role", choices={"foreign", "iran", "both"}, default="iran")
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-uploads", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    parser.add_argument("--skip-redis", action="store_true")
    parser.add_argument(
        "--restore-smoke",
        action="store_true",
        help="Restore the DB dump into a temporary postgres container and report table count.",
    )
    parser.add_argument(
        "--pull-to",
        default=None,
        help="Optional local directory to scp Iran backup files into after the remote backup succeeds.",
    )
    return parser.parse_args(argv)


def target_for_role(role: str, settings: dict[str, str]) -> HostTarget:
    if role == "foreign":
        return HostTarget(role="foreign", project_dir=str(REPO_ROOT), compose_file="docker-compose.yml", remote=False)
    if role == "iran":
        return HostTarget(
            role="iran",
            project_dir=settings["IRAN_PROJECT_DIR"],
            compose_file="docker-compose.iran.yml",
            remote=True,
        )
    raise ValueError(f"Unsupported role: {role}")


def shell_bool(value: bool) -> str:
    return "1" if value else "0"


def build_backup_shell(
    target: HostTarget,
    *,
    stamp: str,
    backup_dir: str,
    include_uploads: bool = True,
    include_audit: bool = True,
    include_redis: bool = True,
    restore_smoke: bool = False,
) -> str:
    role = shlex.quote(target.role)
    project_dir = shlex.quote(target.project_dir)
    compose_file = shlex.quote(target.compose_file)
    backup_dir_q = shlex.quote(backup_dir)
    stamp_q = shlex.quote(stamp)
    include_uploads_s = shell_bool(include_uploads)
    include_audit_s = shell_bool(include_audit)
    include_redis_s = shell_bool(include_redis)
    restore_smoke_s = shell_bool(restore_smoke)
    restore_name = shlex.quote(f"trading_bot_restore_drill_{target.role}_{stamp}".replace("-", "_"))
    return f"""
set -euo pipefail
cd {project_dir}
if docker compose version >/dev/null 2>&1; then
  compose_cmd='docker compose'
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd='docker-compose'
else
  echo 'No Docker Compose command is available.' >&2
  exit 125
fi
role={role}
stamp={stamp_q}
backup_dir={backup_dir_q}
compose_file={compose_file}
include_uploads={include_uploads_s}
include_audit={include_audit_s}
include_redis={include_redis_s}
restore_smoke={restore_smoke_s}
mkdir -p "$backup_dir"
files_jsonl="$backup_dir/$role-manifest-files-$stamp.jsonl"
: > "$files_jsonl"

record_file() {{
  kind="$1"
  path="$2"
  bytes="$(wc -c < "$path" | tr -d ' ')"
  sha="$(sha256sum "$path" | awk '{{print $1}}')"
  python3 - "$kind" "$path" "$bytes" "$sha" >> "$files_jsonl" <<'PY'
import json
import sys
print(json.dumps({{
    "kind": sys.argv[1],
    "path": sys.argv[2],
    "bytes": int(sys.argv[3]),
    "sha256": sys.argv[4],
}}, sort_keys=True))
PY
}}

empty_tar() {{
  tmp_empty="$(mktemp -d)"
  tar -C "$tmp_empty" -cf - .
  rm -rf "$tmp_empty"
}}

db_file="$backup_dir/$role-db-$stamp.sql.gz"
docker exec trading_bot_db sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip -c > "$db_file"
record_file db "$db_file"

if [ "$include_redis" = "1" ]; then
  redis_file="$backup_dir/$role-redis-$stamp.tar.gz"
  docker exec trading_bot_redis sh -lc 'redis-cli SAVE >/dev/null'
  docker exec trading_bot_redis sh -lc 'cd /data && tar -cf - .' | gzip -c > "$redis_file"
  record_file redis "$redis_file"
fi

if [ "$include_uploads" = "1" ]; then
  uploads_file="$backup_dir/$role-uploads-$stamp.tar.gz"
  $compose_cmd -f "$compose_file" exec -T app sh -lc 'if [ -d /app/uploads ]; then tar -C /app -cf - uploads; else tmp="$(mktemp -d)"; tar -C "$tmp" -cf - .; rm -rf "$tmp"; fi' | gzip -c > "$uploads_file"
  record_file uploads "$uploads_file"
fi

if [ "$include_audit" = "1" ]; then
  audit_file="$backup_dir/$role-audit-$stamp.tar.gz"
  $compose_cmd -f "$compose_file" exec -T app sh -lc 'if [ -d /app/audit_trail ]; then tar -C /app -cf - audit_trail; else tmp="$(mktemp -d)"; tar -C "$tmp" -cf - .; rm -rf "$tmp"; fi' | gzip -c > "$audit_file"
  record_file audit "$audit_file"
fi

restore_status=skipped
restore_table_count=
restore_error=
if [ "$restore_smoke" = "1" ]; then
  restore_name={restore_name}
  restore_log="$backup_dir/$role-restore-smoke-$stamp.log"
  : > "$restore_log"
  docker rm -f "$restore_name" >/dev/null 2>&1 || true
  restore_status=failed
  if docker run -d --name "$restore_name" -e POSTGRES_USER=restore -e POSTGRES_PASSWORD=restore -e POSTGRES_DB=restore postgres:15-alpine >/dev/null; then
    restore_ready=0
    restore_ready_hits=0
    for _ in $(seq 1 60); do
      if docker exec "$restore_name" pg_isready -U restore -d restore >/dev/null 2>&1; then
        restore_ready_hits=$((restore_ready_hits + 1))
        if [ "$restore_ready_hits" -ge 3 ]; then
          restore_ready=1
          break
        fi
      else
        restore_ready_hits=0
      fi
      sleep 1
    done
    roles_ready=1
    if [ "$restore_ready" != "1" ]; then
      restore_error='temporary postgres container did not become ready'
      docker logs "$restore_name" >> "$restore_log" 2>&1 || true
    else
      owner_roles="$(gzip -dc "$db_file" | sed -n 's/.*OWNER TO \\([^;]*\\);.*/\\1/p' | sort -u)"
      for role_name in $owner_roles; do
        case "$role_name" in
          ''|*[!A-Za-z0-9_]*)
            continue
            ;;
        esac
        if ! docker exec "$restore_name" psql -v ON_ERROR_STOP=1 -U restore -d restore -c "DO \\$\\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role_name') THEN EXECUTE format('CREATE ROLE %I', '$role_name'); END IF; END \\$\\$;" >>"$restore_log" 2>&1; then
          roles_ready=0
          restore_error="failed to create restore owner role: $role_name"
          break
        fi
      done
    fi
    if [ "$restore_ready" = "1" ] && [ "$roles_ready" = "1" ] && gzip -dc "$db_file" | docker exec -i "$restore_name" psql -v ON_ERROR_STOP=1 -U restore -d restore >>"$restore_log" 2>&1; then
      restore_table_count="$(docker exec "$restore_name" psql -U restore -d restore -tAc "select count(*) from information_schema.tables where table_schema='public';" | tr -d '[:space:]')"
      restore_status=passed
    elif [ -z "${{restore_error:-}}" ]; then
      restore_error="$(tail -40 "$restore_log" 2>/dev/null | tr '\\n' ' ' | cut -c1-1000)"
      if [ -z "$restore_error" ]; then
        restore_error='psql restore failed'
      fi
    fi
  else
    restore_error='temporary postgres container did not start'
  fi
  docker rm -f "$restore_name" >/dev/null 2>&1 || true
fi

manifest_file="$backup_dir/$role-backup-$stamp.json"
python3 - "$role" "$stamp" "$backup_dir" "$compose_file" "$restore_status" "${{restore_table_count:-}}" "${{restore_error:-}}" "$files_jsonl" "$manifest_file" <<'PY'
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

role, stamp, backup_dir, compose_file, restore_status, table_count, restore_error, files_jsonl, manifest_file = sys.argv[1:]
files = []
for line in Path(files_jsonl).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line:
        files.append(json.loads(line))
payload = {{
    "status": "ok" if files and restore_status != "failed" else "failed",
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "stamp": stamp,
    "role": role,
    "hostname": socket.gethostname(),
    "backup_dir": backup_dir,
    "compose_file": compose_file,
    "files": files,
    "restore_smoke": {{
        "status": restore_status,
        "table_count": int(table_count) if str(table_count).isdigit() else None,
        "error": restore_error or None,
    }},
    "notes": [
        "sync replication is not a backup; keep this artifact off-host too",
        "uploads/audit/redis archives are captured alongside the PostgreSQL dump when enabled",
    ],
}}
Path(manifest_file).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
payload["manifest_path"] = manifest_file
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
"""


def run_command(args: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def parse_json_from_stdout(stdout: str) -> dict[str, Any]:
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
    raise RuntimeError("backup command did not print a JSON object")


def backup_role(
    target: HostTarget,
    settings: dict[str, str],
    *,
    stamp: str,
    backup_dir: str,
    include_uploads: bool,
    include_audit: bool,
    include_redis: bool,
    restore_smoke: bool,
) -> dict[str, Any]:
    script = build_backup_shell(
        target,
        stamp=stamp,
        backup_dir=backup_dir,
        include_uploads=include_uploads,
        include_audit=include_audit,
        include_redis=include_redis,
        restore_smoke=restore_smoke,
    )
    args = remote_args(settings, script) if target.remote else ["bash", "-lc", script]
    started = time.perf_counter()
    result = run_command(args, timeout=3600 if restore_smoke else 1800)
    elapsed = round(time.perf_counter() - started, 3)
    if result.returncode != 0:
        raise RuntimeError(
            f"{target.role} backup failed with exit code {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    payload = parse_json_from_stdout(result.stdout)
    payload["duration_seconds"] = elapsed
    payload["command_role"] = target.role
    return payload


def pull_iran_files(settings: dict[str, str], payload: dict[str, Any], destination: Path) -> list[dict[str, str]]:
    destination.mkdir(parents=True, exist_ok=True)
    pulled: list[dict[str, str]] = []
    target = f"{settings.get('IRAN_SSH_USER', 'root')}@{settings['IRAN_HOST']}"
    for item in payload.get("files") or []:
        remote_path = item.get("path")
        if not remote_path:
            continue
        local_path = destination / Path(remote_path).name
        args = [
            "scp",
            "-P",
            settings.get("IRAN_SSH_PORT", "22"),
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{target}:{remote_path}",
            str(local_path),
        ]
        result = run_command(args, timeout=1800)
        if result.returncode != 0:
            raise RuntimeError(f"failed to pull {remote_path}: {result.stderr.strip()}")
        pulled.append({"remote_path": remote_path, "local_path": display_path(local_path)})
    return pulled


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    stamp = args.timestamp or utc_stamp()
    roles = ("foreign", "iran") if args.role == "both" else (args.role,)
    results: list[dict[str, Any]] = []
    for role in roles:
        payload = backup_role(
            target_for_role(role, settings),
            settings,
            stamp=stamp,
            backup_dir=args.backup_dir,
            include_uploads=not args.skip_uploads,
            include_audit=not args.skip_audit,
            include_redis=not args.skip_redis,
            restore_smoke=args.restore_smoke,
        )
        if args.pull_to and role == "iran":
            payload["pulled_files"] = pull_iran_files(settings, payload, Path(args.pull_to))
        results.append(payload)

    output: dict[str, Any] = {
        "status": "ok" if all(item.get("status") == "ok" for item in results) else "failed",
        "created_at": utc_iso(),
        "stamp": stamp,
        "roles": list(roles),
        "results": results,
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Production backup {output['status']} for roles: {', '.join(roles)}")
        for item in results:
            print(f"- {item['role']}: {item.get('manifest_path')}")
    return 0 if output["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
