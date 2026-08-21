#!/usr/bin/env python3
"""Create an operational production backup on the foreign or Iran host.

The script intentionally runs outside the hot path. It creates a PostgreSQL
dump, Redis data archive, uploads archive, and audit-trail archive, then prints
a compact JSON manifest with file sizes and hashes. Optional DB restore smoke
uses a temporary PostgreSQL container and never touches the production DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_production_baseline import (
    display_path,
    remote_args,
    remote_scp_args,
    utc_iso,
    utc_stamp,
)
from scripts.deploy_config import parse_env_file, resolve_deploy_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = "/srv/trading-bot/backups"
DEFAULT_IRAN_PULL_DIR = Path(
    "/root/secure-envs/trading-bot/production-backups/iran"
)
DEFAULT_BACKUP_RECEIPT_DIR = Path(
    "/root/secure-envs/trading-bot/production-backups/evidence"
)
PRODUCTION_FOREIGN_DOMAIN = "coin.362514.ir"
PRODUCTION_IRAN_DOMAIN = "coin.gold-trade.ir"
PRODUCTION_IRAN_PROJECT_DIR = "/srv/trading-bot/current"
PRODUCTION_BACKUP_MANIFEST_KEYS = (
    "LOCAL_PROJECT_DIR",
    "FOREIGN_PUBLIC_DOMAIN",
    "IRAN_HOST",
    "IRAN_SSH_USER",
    "IRAN_SSH_PORT",
    "IRAN_SSH_AUTH_METHOD",
    "IRAN_SSH_PRIVATE_KEY_PATH",
    "IRAN_PROJECT_DIR",
    "IRAN_APP_DOMAIN",
    "IRAN_PUBLIC_DOMAIN",
)
NORMAL_RETURN_PROCESS_TERMINATION_GRACE_SECONDS = 0.25
NORMAL_RETURN_PROCESS_KILL_TIMEOUT_SECONDS = 2.0
REGISTRATION_STAGE1_RESTORE_TABLES = (
    "invitation_identity_reservations",
    "telegram_registration_command_receipts",
    "telegram_registration_intents",
    "user_counter_event_receipts",
)


@dataclass(frozen=True)
class HostTarget:
    role: str
    project_dir: str
    compose_file: str
    remote: bool


def production_backup_manifest_values(manifest: Path | None) -> dict[str, str]:
    """Load an explicit production target without deploy default/env fallback."""

    if manifest is None or not manifest.is_file():
        raise RuntimeError("an explicit production manifest is required")
    values = parse_env_file(manifest)
    if any(not str(values.get(key) or "").strip() for key in PRODUCTION_BACKUP_MANIFEST_KEYS):
        raise RuntimeError("the production backup target identity is incomplete")
    key_path = Path(str(values.get("IRAN_SSH_PRIVATE_KEY_PATH") or "")).expanduser()
    if (
        Path(str(values["LOCAL_PROJECT_DIR"])).expanduser().resolve(strict=False)
        != REPO_ROOT
        or str(values["FOREIGN_PUBLIC_DOMAIN"]).strip().lower()
        != PRODUCTION_FOREIGN_DOMAIN
        or str(values["IRAN_PROJECT_DIR"]).strip() != PRODUCTION_IRAN_PROJECT_DIR
        or str(values["IRAN_APP_DOMAIN"]).strip().lower() != PRODUCTION_IRAN_DOMAIN
        or str(values["IRAN_PUBLIC_DOMAIN"]).strip().lower() != PRODUCTION_IRAN_DOMAIN
        or any(
            "staging" in str(values.get(key) or "").strip().lower()
            for key in ("IRAN_PROJECT_DIR", "IRAN_APP_DOMAIN", "IRAN_PUBLIC_DOMAIN", "FOREIGN_PUBLIC_DOMAIN")
        )
        or str(values.get("IRAN_SSH_AUTH_METHOD") or "").strip().lower()
        != "key"
        or bool(str(values.get("IRAN_SSH_PASSWORD") or "").strip())
        or not key_path.is_absolute()
        or key_path.is_symlink()
        or not key_path.is_file()
        or key_path.resolve(strict=False) != key_path
        or key_path.stat().st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(key_path.stat().st_mode) not in {0o400, 0o600}
    ):
        raise RuntimeError("the production backup target identity is not exact")
    return values


def backup_target_binding_sha256(role: str, values: dict[str, str]) -> str:
    """Hash the intended manifest target without writing host data to reports."""

    if role == "foreign":
        identity = {
            "environment": "production",
            "role": role,
            "endpoint": str(values.get("FOREIGN_PUBLIC_DOMAIN") or "").strip().lower(),
            "project_dir": str(
                Path(str(values.get("LOCAL_PROJECT_DIR") or ""))
                .expanduser()
                .resolve(strict=False)
            ),
            "compose_project": "trading_bot",
        }
    elif role == "iran":
        identity = {
            "environment": "production",
            "role": role,
            "endpoint": str(values.get("IRAN_HOST") or "").strip().lower(),
            "ssh_user": str(values.get("IRAN_SSH_USER") or "").strip(),
            "ssh_port": str(values.get("IRAN_SSH_PORT") or "").strip(),
            "project_dir": str(values.get("IRAN_PROJECT_DIR") or "").strip(),
            "public_domain": str(values.get("IRAN_APP_DOMAIN") or "").strip().lower(),
            "compose_project": "current",
        }
    else:
        raise ValueError(f"Unsupported role: {role}")
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def database_identity_sha256(role: str, database_name: str, system_identifier: str) -> str:
    material = f"{role}\0{database_name}\0{system_identifier}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


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
    parser.add_argument(
        "--receipt",
        default=None,
        help="Write the combined evidence atomically under the approved secure receipt root.",
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
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", stamp):
        raise ValueError("backup timestamp contains unsafe characters")
    if Path(backup_dir) != Path(DEFAULT_BACKUP_DIR):
        raise ValueError("production backup directory must use the approved root")
    role = shlex.quote(target.role)
    project_dir = shlex.quote(target.project_dir)
    compose_file = shlex.quote(target.compose_file)
    backup_dir_q = shlex.quote(backup_dir)
    stamp_q = shlex.quote(stamp)
    include_uploads_s = shell_bool(include_uploads)
    include_audit_s = shell_bool(include_audit)
    include_redis_s = shell_bool(include_redis)
    restore_smoke_s = shell_bool(restore_smoke)
    run_nonce = secrets.token_hex(12)
    run_nonce_q = shlex.quote(run_nonce)
    restore_name = shlex.quote(
        f"trading_bot_restore_drill_{target.role}_{stamp}_{run_nonce}".replace("-", "_")
    )
    restore_volume = shlex.quote(
        f"trading_bot_restore_drill_{target.role}_{stamp}_{run_nonce}_data".replace(
            "-", "_"
        )
    )
    required_restore_tables = shlex.quote(" ".join(REGISTRATION_STAGE1_RESTORE_TABLES))
    return f"""
set -euo pipefail
umask 077
cd {project_dir}
if docker compose version >/dev/null 2>&1; then
  compose_cmd='docker compose'
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd='docker-compose'
else
  echo 'No Docker Compose command is available.' >&2
  exit 125
fi
if ! command -v timeout >/dev/null 2>&1; then
  echo 'The bounded cleanup command is unavailable.' >&2
  exit 125
fi
role={role}
stamp={stamp_q}
backup_dir={backup_dir_q}
compose_file={compose_file}
compose_project="$([ "$role" = foreign ] && printf trading_bot || printf current)"
include_uploads={include_uploads_s}
include_audit={include_audit_s}
include_redis={include_redis_s}
restore_smoke={restore_smoke_s}
required_restore_tables={required_restore_tables}
run_nonce={run_nonce_q}
case "$backup_dir" in
  /*) ;;
  *) echo 'Backup directory must be absolute.' >&2; exit 126 ;;
esac
backup_dir_canonical="$(realpath -m -- "$backup_dir")"
[ "$backup_dir_canonical" = "$backup_dir" ] || {{
  echo 'Backup directory must be the canonical approved production root.' >&2
  exit 126
}}
project_dir_canonical="$(realpath -m -- {project_dir})"
case "$backup_dir" in
  /tmp|/tmp/*|/var/tmp|/var/tmp/*|"$project_dir_canonical"|"$project_dir_canonical"/*)
    echo 'Backup directory must be private and outside temporary/project paths.' >&2
    exit 126
    ;;
esac
if [ -L "$backup_dir" ]; then
  echo 'Backup directory cannot be a symlink.' >&2
  exit 126
fi
mkdir -p -- "$backup_dir"
chmod 0700 "$backup_dir"
if [ "$(stat -c %u "$backup_dir")" != "$(id -u)" ] || [ "$(stat -c %a "$backup_dir")" != "700" ]; then
  echo 'Backup directory ownership or permissions are unsafe.' >&2
  exit 126
fi
lock_dir="$backup_dir/.production-backup.lock"
if ! mkdir -m 0700 -- "$lock_dir" 2>/dev/null; then
  echo 'Another production backup is active or requires lock review.' >&2
  exit 126
fi
run_id="$role-$stamp-$run_nonce"
run_dir="$backup_dir/$run_id"
if ! mkdir -m 0700 -- "$run_dir"; then
  rmdir "$lock_dir" 2>/dev/null || true
  echo 'Could not allocate a unique backup run directory.' >&2
  exit 126
fi
restore_name={restore_name}
restore_volume={restore_volume}
restore_cleanup_status=skipped
restore_cleanup_container_absent=false
restore_cleanup_named_volume_absent=false
restore_cleanup_owned_volume_count=0
restore_cleanup_owned_volumes_absent=false
restore_cleanup_volume_names_sha256=
restore_cleanup_proof_sha256=
restore_cleanup_error=
restore_owned_volumes_file="$run_dir/$role-restore-owned-volumes-$stamp.txt"
( set -o noclobber; : > "$restore_owned_volumes_file" )
chmod 0600 "$restore_owned_volumes_file"

docker_cleanup_bounded() {{
  timeout --signal=TERM --kill-after=5s 30s docker "$@"
}}

cleanup_restore_resources() {{
  restore_cleanup_status=failed
  restore_cleanup_container_absent=false
  restore_cleanup_named_volume_absent=false
  restore_cleanup_owned_volumes_absent=false
  restore_cleanup_error=

  if ! container_names="$(docker_cleanup_bounded container ls -a --format '{{{{.Names}}}}' 2>/dev/null)"; then
    restore_cleanup_error='could not enumerate temporary restore containers'
    return 1
  fi
  if printf '%s\n' "$container_names" | grep -Fxq -- "$restore_name"; then
    if ! owned_label="$(docker_cleanup_bounded inspect -f '{{{{index .Config.Labels "trading-bot.production-backup-run"}}}}' "$restore_name" 2>/dev/null)"; then
      restore_cleanup_error='could not inspect temporary restore container ownership'
      return 1
    fi
    if [ "$owned_label" != "$run_id" ]; then
      restore_cleanup_error='temporary restore container ownership mismatch'
      return 1
    fi
    if ! docker_cleanup_bounded rm -fv "$restore_name" >/dev/null 2>&1; then
      restore_cleanup_error='temporary restore container cleanup failed'
      return 1
    fi
  fi

  if ! all_volume_names="$(docker_cleanup_bounded volume ls --format '{{{{.Name}}}}' 2>/dev/null)"; then
    restore_cleanup_error='could not enumerate temporary restore volume name'
    return 1
  fi
  if printf '%s\n' "$all_volume_names" | grep -Fxq -- "$restore_volume"; then
    if ! intended_volume_label="$(docker_cleanup_bounded volume inspect -f '{{{{index .Labels "trading-bot.production-backup-run"}}}}' "$restore_volume" 2>/dev/null)"; then
      restore_cleanup_error='could not inspect intended restore volume ownership'
      return 1
    fi
    if [ "$intended_volume_label" != "$run_id" ]; then
      restore_cleanup_error='intended restore volume ownership mismatch'
      return 1
    fi
  fi

  if ! owned_volumes="$(docker_cleanup_bounded volume ls --filter "label=trading-bot.production-backup-run=$run_id" --format '{{{{.Name}}}}' 2>/dev/null)"; then
    restore_cleanup_error='could not enumerate temporary restore volumes'
    return 1
  fi
  printf '%s\n' "$owned_volumes" | sed '/^$/d' | LC_ALL=C sort -u > "$restore_owned_volumes_file"
  restore_cleanup_owned_volume_count="$(wc -l < "$restore_owned_volumes_file" | tr -d ' ')"
  restore_cleanup_volume_names_sha256="$(sha256sum "$restore_owned_volumes_file" | awk '{{print $1}}')"
  while IFS= read -r owned_volume; do
    [ -n "$owned_volume" ] || continue
    if ! volume_label="$(docker_cleanup_bounded volume inspect -f '{{{{index .Labels "trading-bot.production-backup-run"}}}}' "$owned_volume" 2>/dev/null)"; then
      restore_cleanup_error='could not inspect temporary restore volume ownership'
      return 1
    fi
    if [ "$volume_label" != "$run_id" ]; then
      restore_cleanup_error='temporary restore volume ownership mismatch'
      return 1
    fi
    if ! docker_cleanup_bounded volume rm "$owned_volume" >/dev/null 2>&1; then
      restore_cleanup_error='temporary restore volume cleanup failed'
      return 1
    fi
  done < "$restore_owned_volumes_file"

  if ! container_names="$(docker_cleanup_bounded container ls -a --format '{{{{.Names}}}}' 2>/dev/null)"; then
    restore_cleanup_error='could not verify temporary restore container cleanup'
    return 1
  fi
  if printf '%s\n' "$container_names" | grep -Fxq -- "$restore_name"; then
    restore_cleanup_error='temporary restore container residue remains'
    return 1
  fi
  restore_cleanup_container_absent=true

  if ! all_volume_names="$(docker_cleanup_bounded volume ls --format '{{{{.Name}}}}' 2>/dev/null)"; then
    restore_cleanup_error='could not verify intended restore volume cleanup'
    return 1
  fi
  if printf '%s\n' "$all_volume_names" | grep -Fxq -- "$restore_volume"; then
    restore_cleanup_error='intended restore volume residue remains'
    return 1
  fi
  restore_cleanup_named_volume_absent=true

  if ! remaining_owned_volumes="$(docker_cleanup_bounded volume ls --filter "label=trading-bot.production-backup-run=$run_id" --format '{{{{.Name}}}}' 2>/dev/null)"; then
    restore_cleanup_error='could not verify temporary restore volume cleanup'
    return 1
  fi
  if [ -n "$(printf '%s\n' "$remaining_owned_volumes" | sed '/^$/d')" ]; then
    restore_cleanup_error='temporary restore volume residue remains'
    return 1
  fi
  restore_cleanup_owned_volumes_absent=true
  restore_cleanup_proof_sha256="$(printf '%s\\0%s\\0%s\\0%s\\0%s\\0%s' "$run_id" "$restore_cleanup_container_absent" "$restore_cleanup_named_volume_absent" "$restore_cleanup_owned_volume_count" "$restore_cleanup_owned_volumes_absent" "$restore_cleanup_volume_names_sha256" | sha256sum | awk '{{print $1}}')"
  restore_cleanup_status=passed
  return 0
}}

cleanup_backup_run() {{
  status=$?
  trap - EXIT INT TERM HUP
  cleanup_failed=0
  if [ "$restore_smoke" = "1" ] && [ "$restore_cleanup_status" != "passed" ]; then
    if ! cleanup_restore_resources; then
      cleanup_failed=1
    fi
  fi
  if ! rmdir "$lock_dir" 2>/dev/null; then
    cleanup_failed=1
  fi
  if [ "$status" -eq 0 ] && [ "$cleanup_failed" -ne 0 ]; then
    status=126
  fi
  exit "$status"
}}
trap cleanup_backup_run EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
files_jsonl="$run_dir/$role-manifest-files-$stamp.jsonl"
( set -o noclobber; : > "$files_jsonl" )
chmod 0600 "$files_jsonl"

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

project_label="$(docker inspect -f '{{{{index .Config.Labels "com.docker.compose.project"}}}}' trading_bot_db)"
release_sha="$(docker exec trading_bot_app printenv RELEASE_SHA | tr -d '[:space:]')"
database_name="$(docker exec trading_bot_db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select current_database();"' | tr -d '[:space:]')"
database_system_identifier="$(docker exec trading_bot_db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select system_identifier::text from pg_control_system();"' | tr -d '[:space:]')"
schema_head="$(docker exec trading_bot_db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select version_num from alembic_version;"' | tr -d '[:space:]')"
if ! printf '%s' "$release_sha" | grep -Eq '^[0-9a-f]{{40}}$' || [ -z "$database_name" ] || ! printf '%s' "$database_system_identifier" | grep -Eq '^[0-9]+$' || ! printf '%s' "$schema_head" | grep -Eq '^[0-9A-Za-z_]+$'; then
  echo 'Runtime release/database identity could not be established.' >&2
  exit 126
fi
database_identity_sha256="$(printf '%s\\0%s\\0%s' "$role" "$database_name" "$database_system_identifier" | sha256sum | awk '{{print $1}}')"

db_file="$run_dir/$role-db-$stamp.sql.gz"
( set -o noclobber; docker exec trading_bot_db sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip -c > "$db_file" )
chmod 0600 "$db_file"
record_file db "$db_file"

if [ "$include_redis" = "1" ]; then
  redis_file="$run_dir/$role-redis-$stamp.tar.gz"
  docker exec trading_bot_redis sh -lc 'redis-cli SAVE >/dev/null'
  ( set -o noclobber; docker exec trading_bot_redis sh -lc 'cd /data && tar -cf - .' | gzip -c > "$redis_file" )
  chmod 0600 "$redis_file"
  record_file redis "$redis_file"
fi

if [ "$include_uploads" = "1" ]; then
  uploads_file="$run_dir/$role-uploads-$stamp.tar.gz"
  ( set -o noclobber; $compose_cmd -p "$compose_project" -f "$compose_file" exec -T app sh -lc 'if [ -d /app/uploads ]; then tar -C /app -cf - uploads; else tmp="$(mktemp -d)"; tar -C "$tmp" -cf - .; rm -rf "$tmp"; fi' | gzip -c > "$uploads_file" )
  chmod 0600 "$uploads_file"
  record_file uploads "$uploads_file"
fi

if [ "$include_audit" = "1" ]; then
  audit_file="$run_dir/$role-audit-$stamp.tar.gz"
  ( set -o noclobber; $compose_cmd -p "$compose_project" -f "$compose_file" exec -T app sh -lc 'if [ -d /app/audit_trail ]; then tar -C /app -cf - audit_trail; else tmp="$(mktemp -d)"; tar -C "$tmp" -cf - .; rm -rf "$tmp"; fi' | gzip -c > "$audit_file" )
  chmod 0600 "$audit_file"
  record_file audit "$audit_file"
fi

restore_status=skipped
restore_validation_status=skipped
restore_table_count=
restore_error=
if [ "$restore_smoke" = "1" ]; then
  restore_log="$run_dir/$role-restore-smoke-$stamp.log"
  ( set -o noclobber; : > "$restore_log" )
  chmod 0600 "$restore_log"
  restore_status=failed
  restore_validation_status=failed
  volume_created=0
  if docker volume create --label "trading-bot.production-backup-run=$run_id" "$restore_volume" >/dev/null; then
    volume_label="$(docker volume inspect -f '{{{{index .Labels "trading-bot.production-backup-run"}}}}' "$restore_volume" 2>/dev/null || true)"
    if [ "$volume_label" = "$run_id" ]; then
      volume_created=1
    else
      restore_error='temporary restore volume ownership mismatch'
    fi
  else
    restore_error='temporary restore volume could not be created'
  fi
  if [ "$volume_created" = "1" ] && docker run -d --name "$restore_name" --label "trading-bot.production-backup-run=$run_id" --mount "type=volume,source=$restore_volume,target=/var/lib/postgresql/data" -e POSTGRES_USER=restore -e POSTGRES_PASSWORD=restore -e POSTGRES_DB=restore postgres:15-alpine >/dev/null; then
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
      restore_missing_tables=''
      for required_table in $required_restore_tables; do
        table_present="$(docker exec "$restore_name" psql -U restore -d restore -tAc "select to_regclass('public.$required_table') is not null;" | tr -d '[:space:]')"
        if [ "$table_present" != "t" ]; then
          restore_missing_tables="$restore_missing_tables $required_table"
        fi
      done
      if [ -z "$restore_missing_tables" ]; then
        restore_validation_status=passed
      else
        restore_error="restored database is missing required tables:$restore_missing_tables"
      fi
    elif [ -z "${{restore_error:-}}" ]; then
      restore_error="$(tail -40 "$restore_log" 2>/dev/null | tr '\\n' ' ' | cut -c1-1000)"
      if [ -z "$restore_error" ]; then
        restore_error='psql restore failed'
      fi
    fi
  else
    if [ -z "${{restore_error:-}}" ]; then
      restore_error='temporary postgres container did not start'
    fi
  fi
  if cleanup_restore_resources; then
    if [ "$restore_validation_status" = "passed" ]; then
      restore_status=passed
    fi
  else
    restore_status=failed
    if [ -n "${{restore_error:-}}" ]; then
      restore_error="$restore_error; $restore_cleanup_error"
    else
      restore_error="$restore_cleanup_error"
    fi
  fi
fi

manifest_file="$run_dir/$role-backup-$stamp.json"
python3 - "$role" "$stamp" "$run_dir" "$compose_file" "$restore_status" "${{restore_table_count:-}}" "${{restore_error:-}}" "$files_jsonl" "$manifest_file" "$project_label" "$release_sha" "$database_name" "$database_identity_sha256" "$schema_head" "$restore_cleanup_status" "$restore_cleanup_container_absent" "$restore_cleanup_named_volume_absent" "$restore_cleanup_owned_volume_count" "$restore_cleanup_owned_volumes_absent" "$restore_cleanup_volume_names_sha256" "$restore_cleanup_proof_sha256" "$restore_cleanup_error" <<'PY'
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

role, stamp, backup_dir, compose_file, restore_status, table_count, restore_error, files_jsonl, manifest_file, project_label, release_sha, database_name, database_identity_sha256, schema_head, cleanup_status, container_absent, named_volume_absent, owned_volume_count, owned_volumes_absent, volume_names_sha256, cleanup_proof_sha256, cleanup_error = sys.argv[1:]
files = []
for line in Path(files_jsonl).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line:
        files.append(json.loads(line))
cleanup_proven = (
    restore_status == "skipped"
    or (
        restore_status == "passed"
        and cleanup_status == "passed"
        and container_absent == "true"
        and named_volume_absent == "true"
        and owned_volumes_absent == "true"
        and bool(re.fullmatch(r"[0-9a-f]{{64}}", volume_names_sha256))
        and bool(re.fullmatch(r"[0-9a-f]{{64}}", cleanup_proof_sha256))
    )
)
payload = {{
    "status": "ok" if files and restore_status != "failed" and cleanup_proven else "failed",
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "stamp": stamp,
    "role": role,
    "hostname": socket.gethostname(),
    "backup_dir": backup_dir,
    "compose_file": compose_file,
    "project_label": project_label,
    "release_sha": release_sha,
    "database_name": database_name,
    "database_identity_sha256": database_identity_sha256,
    "schema_head": schema_head,
    "files": files,
    "restore_smoke": {{
        "status": restore_status,
        "table_count": int(table_count) if str(table_count).isdigit() else None,
        "error": restore_error or None,
        "cleanup": {{
            "status": cleanup_status,
            "container_absent": container_absent == "true",
            "named_volume_absent": named_volume_absent == "true",
            "owned_volume_count": int(owned_volume_count) if str(owned_volume_count).isdigit() else None,
            "owned_volumes_absent": owned_volumes_absent == "true",
            "owned_volume_names_sha256": volume_names_sha256 or None,
            "proof_sha256": cleanup_proof_sha256 or None,
            "commands_bounded": True,
            "error": cleanup_error or None,
        }},
    }},
    "notes": [
        "sync replication is not a backup; keep this artifact off-host too",
        "uploads/audit/redis archives are captured alongside the PostgreSQL dump when enabled",
    ],
}}
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
fd = os.open(manifest_file, flags, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n")
    handle.flush()
    os.fsync(handle.fileno())
payload["manifest_path"] = manifest_file
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
chmod 0600 "$manifest_file"
if [ "$restore_smoke" = "1" ] && [ "$restore_cleanup_status" != "passed" ]; then
  exit 126
fi
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _process_group_exists(process_group_id: int) -> bool:
    """Return whether the captured process group still has kernel members."""

    try:
        os.killpg(int(process_group_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM still proves that the process group exists.
        return True
    return True


def _process_group_has_live_members(process_group_id: int) -> bool:
    """Return whether the group has a non-zombie member.

    ``killpg(..., 0)`` also succeeds for a group containing only zombies.  A
    bounded cleanup is complete once no live member can execute further work,
    so inspect proc state after the cheap kernel-level existence probe.
    """

    group_id = int(process_group_id)
    if not _process_group_exists(group_id):
        return False
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            suffix = stat_text[stat_text.rindex(") ") + 2 :].split()
            state = suffix[0]
            member_group = int(suffix[2])
        except (OSError, IndexError, ValueError):
            continue
        if member_group == group_id and state != "Z":
            return True
    return False


def _wait_for_process_group_exit(process_group_id: int, timeout: float) -> bool:
    """Poll group existence for at most ``timeout`` seconds."""

    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _process_group_has_live_members(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def _stop_process_group(
    process: subprocess.Popen[str],
    *,
    process_group_id: int | None = None,
    grace_seconds: float = 5.0,
    kill_seconds: float = 5.0,
) -> tuple[str, str]:
    group_id = int(process_group_id or process.pid)
    term_deadline = time.monotonic() + max(float(grace_seconds), 0.0)
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    communicate_timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        communicate_timed_out = True
        stdout = stderr = ""
    remaining_grace = max(0.0, term_deadline - time.monotonic())
    group_stopped = _wait_for_process_group_exit(group_id, remaining_grace)
    needs_kill = communicate_timed_out or process.poll() is None or not group_stopped
    if needs_kill:
        kill_deadline = time.monotonic() + max(float(kill_seconds), 0.0)
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kill_communicate_timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=kill_seconds)
        except subprocess.TimeoutExpired:
            kill_communicate_timed_out = True
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        remaining_kill = max(0.0, kill_deadline - time.monotonic())
        group_stopped = _wait_for_process_group_exit(group_id, remaining_kill)
        if kill_communicate_timed_out or not group_stopped:
            raise RuntimeError(
                "child process group did not stop within bounded cleanup"
            ) from None
    if process.poll() is None:
        raise RuntimeError("child process leader did not terminate")
    return stdout or "", stderr or ""


def run_command(args: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    safe_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if str(os.environ.get(key) or "").strip()
    }
    process = subprocess.Popen(
        args,
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=safe_env,
    )
    process_group_id = process.pid
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_process_group(
            process, process_group_id=process_group_id
        )
        return subprocess.CompletedProcess(args, 124, stdout, stderr)
    except BaseException:
        _stop_process_group(process, process_group_id=process_group_id)
        raise
    if _process_group_has_live_members(process_group_id):
        _stop_process_group(
            process,
            process_group_id=process_group_id,
            grace_seconds=NORMAL_RETURN_PROCESS_TERMINATION_GRACE_SECONDS,
            kill_seconds=NORMAL_RETURN_PROCESS_KILL_TIMEOUT_SECONDS,
        )
        return subprocess.CompletedProcess(args, 125, stdout or "", stderr or "")
    return subprocess.CompletedProcess(
        args, int(process.returncode or 0), stdout or "", stderr or ""
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
    manifest_values: dict[str, str],
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
    payload["target_binding_sha256"] = backup_target_binding_sha256(
        target.role, manifest_values
    )
    return payload


def pull_iran_files(settings: dict[str, str], payload: dict[str, Any], destination: Path) -> list[dict[str, str]]:
    supplied = destination.expanduser()
    approved = DEFAULT_IRAN_PULL_DIR
    if (
        not supplied.is_absolute()
        or supplied != approved
        or supplied.is_symlink()
        or supplied.resolve(strict=False) != approved
    ):
        raise RuntimeError("Iran backup pull directory is not a secure production path")
    parent = approved.parent
    if (
        not parent.is_dir()
        or parent.is_symlink()
        or parent.resolve(strict=True) != parent
        or parent.stat().st_uid != os.geteuid()
        or stat.S_IMODE(parent.stat().st_mode) != 0o700
    ):
        raise RuntimeError("Iran backup pull parent is not an approved secure directory")
    if not approved.exists():
        approved.mkdir(mode=0o700)
    destination = approved
    destination_stat = destination.stat()
    if (
        destination.is_symlink()
        or not destination.is_dir()
        or destination.resolve(strict=True) != destination
        or destination_stat.st_uid != os.geteuid()
        or stat.S_IMODE(destination_stat.st_mode) != 0o700
    ):
        raise RuntimeError("Iran backup pull directory ownership or mode is unsafe")
    pulled: list[dict[str, str]] = []
    target = f"{settings['IRAN_SSH_USER']}@{settings['IRAN_HOST']}"
    for item in payload.get("files") or []:
        remote_path = item.get("path")
        if not remote_path:
            continue
        local_path = destination / Path(remote_path).name
        if local_path.exists() or local_path.is_symlink():
            raise RuntimeError("Iran backup pull destination already exists")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{local_path.name}.", suffix=".partial", dir=destination
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        try:
            args = remote_scp_args(
                settings, f"{target}:{remote_path}", str(temporary_path)
            )
            result = run_command(args, timeout=1800)
            if result.returncode != 0:
                raise RuntimeError("failed to pull Iran backup artifact")
            temporary_path.chmod(0o600)
            expected_size = int(item.get("bytes") or 0)
            expected_sha = str(item.get("sha256") or "")
            if (
                expected_size <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
                or temporary_path.stat().st_size != expected_size
                or _sha256_file(temporary_path) != expected_sha
            ):
                raise RuntimeError("pulled Iran backup artifact failed integrity validation")
            # Hard-link creation is an atomic O_EXCL-style publication: it
            # refuses an existing destination instead of overwriting it.
            os.link(temporary_path, local_path)
            local_path.chmod(0o600)
            with local_path.open("rb") as handle:
                os.fsync(handle.fileno())
            directory_fd = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)
        pulled.append({"remote_path": remote_path, "local_path": display_path(local_path)})
    return pulled


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if Path(args.backup_dir) != Path(DEFAULT_BACKUP_DIR):
        raise RuntimeError("production backup directory must use the approved root")
    if args.pull_to and (
        Path(args.pull_to).expanduser().resolve(strict=False)
        != DEFAULT_IRAN_PULL_DIR.resolve(strict=False)
    ):
        raise RuntimeError("Iran backup pull directory must use the approved root")
    manifest_path = Path(args.manifest).expanduser().resolve(strict=False) if args.manifest else None
    manifest_values = production_backup_manifest_values(manifest_path)
    settings = resolve_deploy_settings(
        manifest_path=str(manifest_path), environ={}
    )
    stamp = args.timestamp or utc_stamp()
    roles = ("foreign", "iran") if args.role == "both" else (args.role,)
    results: list[dict[str, Any]] = []
    for role in roles:
        payload = backup_role(
            target_for_role(role, settings),
            settings,
            manifest_values,
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
    if args.receipt:
        supplied_receipt = Path(args.receipt).expanduser()
        approved_parent = DEFAULT_BACKUP_RECEIPT_DIR
        if (
            not supplied_receipt.is_absolute()
            or supplied_receipt.parent != approved_parent
            or supplied_receipt.is_symlink()
            or supplied_receipt.resolve(strict=False) != supplied_receipt
            or supplied_receipt.exists()
        ):
            raise RuntimeError("production backup receipt path is not approved")
        receipt_path = supplied_receipt
        receipt_root_parent = approved_parent.parent
        if (
            not receipt_root_parent.is_dir()
            or receipt_root_parent.is_symlink()
            or receipt_root_parent.resolve(strict=True) != receipt_root_parent
            or receipt_root_parent.stat().st_uid != os.geteuid()
            or stat.S_IMODE(receipt_root_parent.stat().st_mode) != 0o700
        ):
            raise RuntimeError("production backup receipt parent is unsafe")
        if not approved_parent.exists():
            approved_parent.mkdir(mode=0o700)
        parent_metadata = approved_parent.stat()
        if (
            approved_parent.is_symlink()
            or not approved_parent.is_dir()
            or approved_parent.resolve(strict=True) != approved_parent
            or parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        ):
            raise RuntimeError("production backup receipt directory is unsafe")
        descriptor = os.open(
            receipt_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(approved_parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Production backup {output['status']} for roles: {', '.join(roles)}")
        for item in results:
            print(f"- {item['role']}: {item.get('manifest_path')}")
    return 0 if output["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
