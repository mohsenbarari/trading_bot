#!/usr/bin/env python3
"""Create read-only WebApp-FI artifacts for the WA-IR S3 snapshot transport.

This command only reads the running legacy 2c08 PostgreSQL and uploads volume
through local Docker exec.  It does not contact WA-IR, Object Storage, Nginx,
or any public endpoint.  Its output is the exact custom PostgreSQL dump and
gzip uploads archive accepted by ``manage_webapp_ir_snapshot.py publish``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from restore_webapp_ir_snapshot import (
    ALEMBIC_RE,
    DEFAULT_MAX_UPLOAD_BYTES,
    GENERATION_RE,
    RELEASE_RE,
    RestoreError,
    parse_env_file,
    sha256_file,
    validate_upload_archive,
)


SCHEMA_VERSION = "webapp_ir_snapshot_artifacts_v1"
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATABASE_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_generation() -> str:
    stamp = datetime.now(timezone.utc).strftime("snapshot-%Y%m%dt%H%M%Sz")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def require_container_name(value: str, *, label: str) -> str:
    if not CONTAINER_RE.fullmatch(value):
        raise RestoreError(f"{label} is not a safe Docker container name")
    return value


def require_output_root(value: str) -> Path:
    root = Path(value)
    if not root.is_absolute():
        raise RestoreError("output-root must be absolute")
    if root.is_symlink() or not root.is_dir():
        raise RestoreError("output-root must be an existing non-symlink directory")
    metadata = root.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise RestoreError("output-root must be root-only")
    return root.resolve(strict=True)


def run_capture(
    arguments: Sequence[str],
    *,
    stdout_path: Path | None = None,
    input_bytes: bytes | None = None,
    timeout: int,
) -> str:
    stdin: int | None = subprocess.DEVNULL if input_bytes is None else None
    if stdout_path is None:
        result = subprocess.run(
            [str(item) for item in arguments],
            text=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_bytes,
            stdin=stdin,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RestoreError(f"read-only Docker capture command failed (exit {result.returncode})")
        return result.stdout.decode("utf-8", errors="strict").strip()
    with stdout_path.open("xb") as output:
        result = subprocess.run(
            [str(item) for item in arguments],
            text=False,
            stdout=output,
            stderr=subprocess.PIPE,
            input=input_bytes,
            stdin=stdin,
            timeout=timeout,
            check=False,
        )
    if result.returncode != 0:
        raise RestoreError(f"read-only Docker capture command failed (exit {result.returncode})")
    return ""


def load_read_only_db_client(path: Path) -> tuple[str, bytes]:
    values = parse_env_file(path, label="read-only database capture env")
    user = values.get("CAPTURE_DB_USER", "")
    password = values.get("CAPTURE_DB_PASSWORD", "")
    if not DATABASE_USER_RE.fullmatch(user):
        raise RestoreError("CAPTURE_DB_USER must be a safe PostgreSQL role name")
    if not password or "\n" in password or "\r" in password:
        raise RestoreError("CAPTURE_DB_PASSWORD must be non-empty and single-line")
    return user, (password + "\n").encode("utf-8")


def read_only_docker_command(db_container: str, user: str, command: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        "-e",
        f"CAPTURE_DB_USER={user}",
        db_container,
        "sh",
        "-ec",
        "IFS= read -r PGPASSWORD; export PGPASSWORD; " + command,
    ]


def assert_source_role_read_only(db_container: str, *, user: str, password: bytes) -> None:
    result = run_capture(
        read_only_docker_command(
            db_container,
            user,
            "exec psql -v ON_ERROR_STOP=1 -U \"$CAPTURE_DB_USER\" -d \"$POSTGRES_DB\" -tAc "
            "\"WITH candidate_tables AS ("
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND c.relkind IN ('r','p','v','m','f')"
            ") SELECT CASE WHEN "
            "NOT (SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user) "
            "AND NOT has_database_privilege(current_user, current_database(), 'CREATE') "
            "AND NOT EXISTS (SELECT 1 FROM candidate_tables WHERE "
            "has_table_privilege(current_user, oid, 'INSERT') OR "
            "has_table_privilege(current_user, oid, 'UPDATE') OR "
            "has_table_privilege(current_user, oid, 'DELETE') OR "
            "has_table_privilege(current_user, oid, 'TRUNCATE') OR "
            "has_table_privilege(current_user, oid, 'REFERENCES') OR "
            "has_table_privilege(current_user, oid, 'TRIGGER')) "
            "THEN 'read_only' ELSE 'write_capable' END;\"",
        ),
        input_bytes=password,
        timeout=60,
    )
    if result != "read_only":
        raise RestoreError("CAPTURE_DB_USER is not a read-only PostgreSQL role")


def source_alembic_revision(db_container: str, *, user: str, password: bytes) -> str:
    revision = run_capture(
        read_only_docker_command(
            db_container,
            user,
            'exec psql -v ON_ERROR_STOP=1 -U "$CAPTURE_DB_USER" -d "$POSTGRES_DB" -tAc '
            '"SELECT version_num FROM alembic_version LIMIT 1;"',
        ),
        input_bytes=password,
        timeout=60,
    ).lower()
    if not ALEMBIC_RE.fullmatch(revision):
        raise RestoreError("source database returned an invalid Alembic revision")
    return revision


def make_manifest(
    *,
    generation: str,
    release_sha: str,
    alembic_revision: str,
    database: Path,
    uploads: Path,
    source_db_snapshot_started_at: str,
    source_capture_completed_at: str,
    source_db_client_lifetime_seconds: int,
) -> dict[str, Any]:
    database_sha, database_bytes = sha256_file(database)
    uploads_sha, uploads_bytes = sha256_file(uploads)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "snapshot_id": generation,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "release_sha": release_sha,
        "alembic_revision": alembic_revision,
        # These names intentionally match the immutable Object Storage
        # transport.  The first value is recorded immediately before pg_dump.
        "source_db_snapshot_started_at": source_db_snapshot_started_at,
        "source_capture_completed_at": source_capture_completed_at,
        "source_database_capture": {
            "client_mode": "short_lived_read_only",
            "client_lifetime_seconds": source_db_client_lifetime_seconds,
        },
        "source_volume_capture": {"mode": "read_only_no_mutation"},
        "database_dump_path": str(database),
        "uploads_archive_path": str(uploads),
        "database": {
            "path": str(database),
            "sha256": database_sha,
            "bytes": database_bytes,
            "format": "pg_dump_custom",
        },
        "uploads": {
            "path": str(uploads),
            "sha256": uploads_sha,
            "bytes": uploads_bytes,
            "format": "tar_gz_uploads_root",
        },
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = path.with_suffix(".tmp")
    with descriptor.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(descriptor, 0o600)
    os.replace(descriptor, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, help="existing root-only local artifact directory")
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--alembic-revision", required=True)
    parser.add_argument("--generation", default=None)
    parser.add_argument("--db-container", default="trading_bot_db")
    parser.add_argument("--app-container", default="trading_bot_app")
    parser.add_argument("--db-capture-env", required=True, help="root-only CAPTURE_DB_USER/PASSWORD for a read-only role")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--max-upload-bytes", type=int, default=DEFAULT_MAX_UPLOAD_BYTES)
    parser.add_argument("--apply", action="store_true", help="write local artifacts; default is a no-Docker plan")
    parser.add_argument("--json", action="store_true")
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    release_sha = str(args.release_sha).lower()
    revision = str(args.alembic_revision).lower()
    generation = str(args.generation or default_generation()).lower()
    if not RELEASE_RE.fullmatch(release_sha):
        raise RestoreError("release-sha must be a 40-character lowercase Git SHA")
    if not ALEMBIC_RE.fullmatch(revision):
        raise RestoreError("alembic-revision must be a 12-character lowercase revision")
    if not GENERATION_RE.fullmatch(generation):
        raise RestoreError("generation is not safe")
    if args.attempts < 1 or args.attempts > 5:
        raise RestoreError("attempts must be between 1 and 5")
    if args.max_upload_bytes < 1:
        raise RestoreError("max-upload-bytes must be positive")
    output_root = require_output_root(args.output_root)
    db_container = require_container_name(args.db_container, label="db-container")
    app_container = require_container_name(args.app_container, label="app-container")
    capture_user, capture_password = load_read_only_db_client(Path(args.db_capture_env))
    artifact_dir = output_root / "snapshots" / generation
    if artifact_dir.exists():
        raise RestoreError("generation output directory already exists")
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "planned" if not args.apply else "running",
        "snapshot_id": generation,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "release_sha": release_sha,
        "alembic_revision": revision,
        "artifact_dir": str(artifact_dir),
        "source_db_container": db_container,
        "source_app_container": app_container,
        "source_database_capture": {"client_mode": "short_lived_read_only"},
        "source_volume_capture": {"mode": "read_only_no_mutation"},
        "remote_transfer": "none",
        "services_stopped": False,
        "source_data_mutated": False,
    }
    if not args.apply:
        return plan

    artifact_dir.mkdir(parents=True, mode=0o700)
    assert_source_role_read_only(db_container, user=capture_user, password=capture_password)
    source_before = source_alembic_revision(db_container, user=capture_user, password=capture_password)
    if source_before != revision:
        raise RestoreError("source Alembic revision does not match the pinned production schema")
    database = artifact_dir / "database.dump"
    uploads = artifact_dir / "uploads.tar.gz"
    source_db_snapshot_started_at = ""
    source_capture_completed_at = ""
    for attempt in range(1, args.attempts + 1):
        for temporary in (database, uploads):
            temporary.unlink(missing_ok=True)
        capture_started = time.monotonic()
        # This is the conservative RPO clock.  It must be as close as possible
        # to the pg_dump launch, rather than the earlier local preflight reads.
        source_db_snapshot_started_at = utc_now()
        run_capture(
            read_only_docker_command(
                db_container,
                capture_user,
                'exec pg_dump --format=custom --no-owner --no-privileges -U "$CAPTURE_DB_USER" -d "$POSTGRES_DB"',
            ),
            stdout_path=database,
            input_bytes=capture_password,
            timeout=900,
        )
        source_db_client_lifetime_seconds = max(1, int(round(time.monotonic() - capture_started)))
        if source_db_client_lifetime_seconds > 300:
            raise RestoreError("source read-only pg_dump exceeded the 300-second lifetime bound")
        os.chmod(database, 0o600)
        with database.open("rb") as handle:
            if handle.read(5) != b"PGDMP":
                raise RestoreError("source pg_dump did not produce PostgreSQL custom format")
        try:
            run_capture(
                [
                    "docker",
                    "exec",
                    app_container,
                    "sh",
                    "-ec",
                    "exec tar -C /app -czf - uploads",
                ],
                stdout_path=uploads,
                timeout=900,
            )
            os.chmod(uploads, 0o600)
            validate_upload_archive(uploads, max_uncompressed_bytes=args.max_upload_bytes)
            source_capture_completed_at = utc_now()
        except RestoreError:
            if attempt == args.attempts:
                raise
            time.sleep(1)
            continue
        break
    else:  # pragma: no cover - loop always returns or breaks
        raise RestoreError("could not capture a stable uploads archive")
    source_after = source_alembic_revision(db_container, user=capture_user, password=capture_password)
    if source_after != source_before:
        raise RestoreError("source Alembic revision changed while the snapshot was captured")
    payload = make_manifest(
        generation=generation,
        release_sha=release_sha,
        alembic_revision=revision,
        database=database,
        uploads=uploads,
        source_db_snapshot_started_at=source_db_snapshot_started_at,
        source_capture_completed_at=source_capture_completed_at,
        source_db_client_lifetime_seconds=source_db_client_lifetime_seconds,
    )
    manifest_path = artifact_dir / "snapshot-artifacts.json"
    write_manifest(manifest_path, payload)
    payload["manifest_path"] = str(manifest_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = execute(args)
    except (RestoreError, OSError, subprocess.TimeoutExpired) as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": str(exc),
            "remote_transfer": "none",
            "services_stopped": False,
            "source_data_mutated": False,
        }
        exit_code = 1
    else:
        exit_code = 0
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    else:
        print(f"WebApp-FI snapshot artifacts: {payload['status']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
