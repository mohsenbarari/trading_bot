#!/usr/bin/env python3
"""Run an inventory-bound PostgreSQL backup/restore drill on one Stage 3 host."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, BinaryIO

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from scripts.render_three_site_staging_role_compose import _atomic_write, parse_env_values
from scripts.verify_three_site_staging_inventory import load_inventory
from scripts.verify_three_site_staging_role_bundle import (
    _verify_bundle_source,
    verify_role_bundle,
)


DOCKER = "/usr/bin/docker"
ROLE_DB = {
    "bot-fi": ("bot_fi_db", "BOT_FI_POSTGRES_USER", "BOT_FI_POSTGRES_DB"),
    "webapp-fi": (
        "webapp_fi_db", "WEBAPP_FI_POSTGRES_USER", "WEBAPP_FI_POSTGRES_DB",
    ),
    "webapp-ir": (
        "webapp_ir_db", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB",
    ),
    "witness": ("witness_db", "WITNESS_POSTGRES_USER", "WITNESS_POSTGRES_DB"),
}
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PG_DUMP_RESTRICT_RE = re.compile(br"^\\(?:un)?restrict [A-Za-z0-9]+\r?\n$")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
MAX_DUMP_BYTES = 2 * 1024 * 1024 * 1024


class RestoreDrillError(RuntimeError):
    pass


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def logical_dump_hash(raw: bytes) -> str:
    """Hash logical dump bytes without PostgreSQL's per-run psql nonce."""
    canonical = b"".join(
        line for line in raw.splitlines(keepends=True)
        if PG_DUMP_RESTRICT_RE.fullmatch(line) is None
    )
    return hashlib.sha256(canonical).hexdigest()


def verify_drill_document(
    document: dict[str, Any],
    *,
    role: str,
    campaign_id: str,
    release_sha: str,
    postgres_system_id: str,
) -> dict[str, Any]:
    fields = {
        "schema", "status", "campaign_id", "release_sha", "role",
        "observed_at", "source_database", "scratch_database",
        "postgres_system_id", "backup", "source_fingerprints",
        "restored_fingerprints", "scratch_removed", "database_restarted",
        "application_started", "production_touched",
    }
    fingerprint_fields = {"schema_sha256", "data_sha256"}
    backup_fields = {"path", "bytes", "sha256", "format"}
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document.get("schema") != "three-site-stage3-postgres-restore-drill-v1"
        or document.get("status") != "backup-restored-and-compared"
        or document.get("campaign_id") != campaign_id
        or document.get("release_sha") != release_sha
        or document.get("role") != role
        or str(document.get("postgres_system_id")) != postgres_system_id
        or document.get("scratch_removed") is not True
        or document.get("database_restarted") is not False
        or document.get("application_started") is not False
        or document.get("production_touched") is not False
    ):
        raise RestoreDrillError("restore-drill identity/status is invalid")
    backup = document.get("backup")
    source = document.get("source_fingerprints")
    restored = document.get("restored_fingerprints")
    if (
        not isinstance(backup, dict)
        or set(backup) != backup_fields
        or backup.get("format") != "pg_dump-custom"
        or not Path(str(backup.get("path", ""))).is_absolute()
        or type(backup.get("bytes")) is not int
        or not 1 <= backup["bytes"] <= MAX_DUMP_BYTES
        or SHA256_RE.fullmatch(str(backup.get("sha256", ""))) is None
        or not isinstance(source, dict)
        or set(source) != fingerprint_fields
        or not isinstance(restored, dict)
        or set(restored) != fingerprint_fields
        or source != restored
        or any(SHA256_RE.fullmatch(str(value)) is None for value in source.values())
        or not IDENT_RE.fullmatch(str(document.get("source_database", "")))
        or not IDENT_RE.fullmatch(str(document.get("scratch_database", "")))
        or document["source_database"] == document["scratch_database"]
    ):
        raise RestoreDrillError("restore-drill backup/fingerprint evidence is invalid")
    try:
        observed = datetime.fromisoformat(
            str(document["observed_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RestoreDrillError("restore-drill timestamp is invalid") from exc
    if observed.tzinfo is None:
        raise RestoreDrillError("restore-drill timestamp lacks timezone")
    return {"status": "verified", "document_sha256": _canonical_hash(document)}


def _run(arguments: list[str], *, timeout: int = 120, stdin: BinaryIO | None = None) -> bytes:
    try:
        result = subprocess.run(
            arguments,
            stdin=stdin or subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RestoreDrillError(
            f"restore-drill command unavailable: {Path(arguments[0]).name}"
        ) from exc
    if result.returncode != 0:
        raise RestoreDrillError(
            f"restore-drill command failed closed: {Path(arguments[0]).name}"
        )
    return result.stdout


def _compose(role_compose: Path, env_file: Path) -> list[str]:
    return [DOCKER, "compose", "-f", str(role_compose), "--env-file", str(env_file)]


def _database_command(
    prefix: list[str], service: str, arguments: list[str], *, timeout: int = 120,
    stdin: BinaryIO | None = None,
) -> bytes:
    return _run(
        [*prefix, "exec", "-T", service, *arguments],
        timeout=timeout,
        stdin=stdin,
    )


def _stream_dump(arguments: list[str], target: Path) -> tuple[str, int]:
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=SAFE_ENV,
            )
            assert process.stdout is not None
            while chunk := process.stdout.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DUMP_BYTES:
                    process.kill()
                    raise RestoreDrillError("PostgreSQL backup exceeds Stage 3 bound")
                digest.update(chunk)
                output.write(chunk)
            try:
                _stderr = process.communicate(timeout=1800)[1]
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate()
                raise RestoreDrillError("PostgreSQL backup timed out") from exc
            if process.returncode != 0:
                raise RestoreDrillError("PostgreSQL backup failed closed")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if size < 1:
        target.unlink(missing_ok=True)
        raise RestoreDrillError("PostgreSQL backup is empty")
    return digest.hexdigest(), size


def _fingerprints(
    prefix: list[str], service: str, *, user: str, database: str,
) -> dict[str, str]:
    common = ["-U", user, "-d", database, "--no-owner", "--no-privileges"]
    schema = _database_command(
        prefix, service, ["pg_dump", *common, "--schema-only"], timeout=600
    )
    data = _database_command(
        prefix,
        service,
        ["pg_dump", *common, "--data-only", "--inserts", "--column-inserts"],
        timeout=1800,
    )
    return {
        "schema_sha256": logical_dump_hash(schema),
        "data_sha256": logical_dump_hash(data),
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    inventory = load_inventory(args.inventory)
    role_compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
    env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
    bundle = verify_role_bundle(
        role=args.role,
        canonical_compose=yaml.safe_load(args.canonical_compose.read_text(encoding="utf-8")),
        role_compose_bytes=role_compose_bytes,
        env_bytes=env_bytes,
        inventory=inventory,
        approval=load_inventory(args.inventory_approval),
        approval_policy=load_inventory(args.approval_policy),
        verify_files=True,
        required_inventory_stage="provisioned",
    )
    role_inventory = next(
        row for row in inventory["roles"] if row["role"] == args.role.replace("-", "_")
    )
    service, user_key, database_key = ROLE_DB[args.role]
    env = parse_env_values(env_bytes.decode("utf-8"))
    user = env.get(user_key, "")
    database = env.get(database_key, "")
    if not IDENT_RE.fullmatch(user) or not IDENT_RE.fullmatch(database):
        raise RestoreDrillError("PostgreSQL role/database identity is invalid")
    campaign_id = str(inventory["campaign_id"])
    scratch = f"stage3_restore_{campaign_id.replace('-', '')[:8]}_{args.role.replace('-', '_')}"
    expected_root = (
        Path(str(role_inventory["storage_root"]))
        / "backups" / campaign_id / "restore-drill"
    )
    if (
        args.backup_output != expected_root / f"{args.role}.dump"
        or args.evidence_output != expected_root / f"{args.role}.json"
        or args.backup_output.exists()
        or args.evidence_output.exists()
        or args.backup_output.is_symlink()
        or args.evidence_output.is_symlink()
    ):
        raise RestoreDrillError("restore-drill output boundary is invalid or already used")
    expected_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    expected_root.chmod(0o700)
    metadata = expected_root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RestoreDrillError("restore-drill directory is unsafe")
    prefix = _compose(args.role_compose, args.env_file)
    running = set(
        _run([*prefix, "ps", "--status", "running", "--services"]).decode().splitlines()
    )
    if running != {service}:
        raise RestoreDrillError("restore drill requires exactly the role database service")
    system_id = _database_command(
        prefix,
        service,
        [
            "psql", "-U", user, "-d", database, "-Atqc",
            "SELECT system_identifier FROM pg_control_system()",
        ],
    ).decode().strip()
    if system_id != str(role_inventory["postgres_system_id"]):
        raise RestoreDrillError("live PostgreSQL identity differs from provisioned inventory")
    exists_query = "SELECT 1 FROM pg_database WHERE datname='" + scratch + "'"
    if _database_command(
        prefix, service, ["psql", "-U", user, "-d", "postgres", "-Atqc", exists_query]
    ).strip():
        raise RestoreDrillError("deterministic scratch database already exists")

    partial = args.backup_output.with_suffix(".dump.partial")
    if partial.exists() or partial.is_symlink():
        raise RestoreDrillError("restore-drill partial backup already exists")
    source_fingerprints = _fingerprints(
        prefix, service, user=user, database=database
    )
    dump_args = [
        *prefix, "exec", "-T", service, "pg_dump", "-U", user, "-d", database,
        "--format=custom", "--no-owner", "--no-privileges",
    ]
    backup_sha256, backup_bytes = _stream_dump(dump_args, partial)
    scratch_created = False
    try:
        try:
            _database_command(
                prefix,
                service,
                ["createdb", "-U", user, "--template=template0", scratch],
            )
            scratch_created = True
            with partial.open("rb") as source:
                _database_command(
                    prefix,
                    service,
                    [
                        "pg_restore", "-U", user, "-d", scratch,
                        "--exit-on-error", "--no-owner", "--no-privileges",
                    ],
                    timeout=1800,
                    stdin=source,
                )
            restored_fingerprints = _fingerprints(
                prefix, service, user=user, database=scratch
            )
            if restored_fingerprints != source_fingerprints:
                raise RestoreDrillError("restored PostgreSQL logical fingerprint differs")
        finally:
            if scratch_created:
                _database_command(
                    prefix,
                    service,
                    ["dropdb", "-U", user, "--if-exists", "--force", scratch],
                )
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if _database_command(
        prefix, service, ["psql", "-U", user, "-d", "postgres", "-Atqc", exists_query]
    ).strip():
        raise RestoreDrillError("scratch database survived cleanup")
    os.replace(partial, args.backup_output)
    document = {
        "schema": "three-site-stage3-postgres-restore-drill-v1",
        "status": "backup-restored-and-compared",
        "campaign_id": campaign_id,
        "release_sha": bundle["release_sha"],
        "role": args.role,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_database": database,
        "scratch_database": scratch,
        "postgres_system_id": system_id,
        "backup": {
            "path": str(args.backup_output),
            "bytes": backup_bytes,
            "sha256": backup_sha256,
            "format": "pg_dump-custom",
        },
        "source_fingerprints": source_fingerprints,
        "restored_fingerprints": restored_fingerprints,
        "scratch_removed": True,
        "database_restarted": False,
        "application_started": False,
        "production_touched": False,
    }
    result = verify_drill_document(
        document,
        role=args.role,
        campaign_id=campaign_id,
        release_sha=bundle["release_sha"],
        postgres_system_id=system_id,
    )
    _atomic_write(
        args.evidence_output,
        (json.dumps(document, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    return {**result, "role": args.role, "output": str(args.evidence_output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_DB), required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--role-compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--backup-output", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
