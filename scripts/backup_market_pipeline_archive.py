#!/usr/bin/env python3
"""Create and verify a release-bound Market Pipeline PostgreSQL backup.

The command runs on the web/data host against an already-running, healthy
Market Pipeline database container.  It never starts or changes that database.
The custom-format dump is written atomically under a root-only directory and is
restored into an isolated, unpublished, labelled PostgreSQL container.  The
caller must copy the verified artifact to the bot/authority host before any
migration; this tool records that requirement but does not perform transport.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from scripts.prepare_market_pipeline_release import (
    DYNAMIC_VALUES,
    IMAGE_ID,
    RELEASE_SHA,
    parse_env,
    validate_source,
)


POSTGRES_IMAGE = (
    "postgres:15-alpine@"
    "sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b"
)
CREATE_CONFIRMATION = "create-production-market-pipeline-archive-backup"
RECEIPT_SCHEMA = "market_pipeline_backup_restore/1.0"
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
SAFE_SQL_ID = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_NAME = re.compile(
    r"^market-archive-before-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.dump$"
)


class BackupError(RuntimeError):
    """A stable, content-free backup refusal."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _secure_directory(path: Path, *, create: bool) -> Path:
    if not path.is_absolute() or path in {Path("/"), Path("/root"), Path("/srv")}:
        raise BackupError("backup_directory_invalid")
    if Path("/tmp") == path or Path("/tmp") in path.parents:
        raise BackupError("backup_directory_tmp_forbidden")
    if "staging" in str(path).lower():
        raise BackupError("backup_directory_staging_forbidden")
    if path.resolve(strict=False) != path:
        raise BackupError("backup_directory_noncanonical")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError("backup_directory_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BackupError("backup_directory_owner_mode_invalid")
    return path


def _secure_regular(path: Path, *, expected_mode: int = 0o600) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError("backup_file_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != expected_mode
        or info.st_nlink != 1
    ):
        raise BackupError("backup_file_owner_mode_invalid")


def _run_text(arguments: Sequence[str], *, label: str) -> str:
    result = subprocess.run(
        list(arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise BackupError(f"{label}_failed_rc_{result.returncode}")
    return result.stdout.strip()


def validate_release_env(
    env_file: Path, *, release_sha: str, image_id: str
) -> dict[str, str]:
    values = parse_env(env_file, secure_input=True)
    source_values = {key: value for key, value in values.items() if key not in DYNAMIC_VALUES}
    validate_source("web", source_values)
    expected = {
        "MARKET_PIPELINE_RELEASE_SHA": release_sha,
        "MARKET_PIPELINE_IMAGE": image_id,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise BackupError("backup_release_env_identity_mismatch")
    project = values.get("MARKET_PIPELINE_PROJECT_NAME", "")
    user = values.get("MARKET_POSTGRES_USER", "market_data")
    database = values.get("MARKET_POSTGRES_DB", "market_archive")
    if not PROJECT_NAME.fullmatch(project):
        raise BackupError("backup_project_name_invalid")
    if not SAFE_SQL_ID.fullmatch(user) or not SAFE_SQL_ID.fullmatch(database):
        raise BackupError("backup_database_identity_invalid")
    return values


def _postgres_path(values: Mapping[str, str]) -> Path:
    return Path(values["MARKET_WEB_DATA_ROOT"]) / "postgres"


def _initial_store_is_empty(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink() and next(path.iterdir(), None) is None
    except OSError as exc:
        raise BackupError("backup_postgres_root_unavailable") from exc


def _container_ids(*, project: str, postgres_path: Path) -> tuple[str, str]:
    labelled = _run_text(
        [
            "docker", "ps", "-q",
            "--filter", f"label=com.docker.compose.project={project}",
            "--filter", "label=com.docker.compose.service=market-database",
        ],
        label="backup_database_inventory",
    ).splitlines()
    mounted = _run_text(
        ["docker", "ps", "-q", "--filter", f"volume={postgres_path}"],
        label="backup_database_mount_inventory",
    ).splitlines()
    labelled = [item for item in labelled if item]
    mounted = [item for item in mounted if item]
    if len(labelled) != 1 or mounted != labelled:
        raise BackupError("backup_database_single_owner_mismatch")
    return labelled[0], mounted[0]


def inspect_source_database(values: Mapping[str, str]) -> dict[str, Any]:
    postgres_path = _postgres_path(values)
    container_id, _ = _container_ids(
        project=values["MARKET_PIPELINE_PROJECT_NAME"], postgres_path=postgres_path
    )
    try:
        document = json.loads(
            _run_text(["docker", "inspect", container_id], label="backup_database_inspect")
        )[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise BackupError("backup_database_inspect_invalid") from exc
    labels = document.get("Config", {}).get("Labels", {}) or {}
    health = document.get("State", {}).get("Health", {}).get("Status")
    mounts = [
        mount
        for mount in document.get("Mounts", [])
        if mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    if (
        document.get("State", {}).get("Running") is not True
        or health != "healthy"
        or document.get("Config", {}).get("Image") != POSTGRES_IMAGE
        or labels.get("com.docker.compose.project")
        != values["MARKET_PIPELINE_PROJECT_NAME"]
        or labels.get("com.docker.compose.service") != "market-database"
        or len(mounts) != 1
        or Path(str(mounts[0].get("Source") or "")) != postgres_path
    ):
        raise BackupError("backup_database_runtime_identity_mismatch")
    user = values.get("MARKET_POSTGRES_USER", "market_data")
    database = values.get("MARKET_POSTGRES_DB", "market_archive")

    def query(sql: str, *, label: str) -> str:
        return _run_text(
            [
                "docker", "exec", container_id, "psql",
                "-X", "-v", "ON_ERROR_STOP=1", "-At",
                "-U", user, "-d", database, "-c", sql,
            ],
            label=label,
        )

    has_migrations = query(
        "SELECT to_regclass('market_data.schema_migrations') IS NOT NULL",
        label="backup_schema_probe",
    ) == "t"
    if not has_migrations:
        raise BackupError("backup_database_schema_unavailable")
    versions = query(
        "SELECT COALESCE(string_agg(version::text, ',' ORDER BY version),'') "
        "FROM market_data.schema_migrations",
        label="backup_schema_versions",
    )
    facts = int(
        query(
            "SELECT count(*) FROM market_data.market_facts",
            label="backup_fact_count",
        )
    )
    table_count = int(
        query(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='market_data'",
            label="backup_table_count",
        )
    )
    system_identifier = query(
        "SELECT system_identifier FROM pg_control_system()",
        label="backup_system_identifier",
    )
    size_bytes = int(
        query("SELECT pg_database_size(current_database())", label="backup_database_size")
    )
    if not system_identifier.isdigit() or size_bytes <= 0 or facts < 0 or table_count < 0:
        raise BackupError("backup_database_metadata_invalid")
    schema_versions = [int(item) for item in versions.split(",") if item]
    if not schema_versions or schema_versions != sorted(set(schema_versions)):
        raise BackupError("backup_database_schema_versions_invalid")
    return {
        "container_id": str(document.get("Id") or container_id),
        "database": database,
        "database_size_bytes": size_bytes,
        "database_identity_sha256": sha256(
            f"web\0{database}\0{system_identifier}".encode("utf-8")
        ).hexdigest(),
        "schema_versions": schema_versions,
        "table_count": table_count,
        "fact_count": facts,
    }


def _write_dump(
    *, container_id: str, user: str, database: str, destination: Path
) -> None:
    candidate = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            process = subprocess.run(
                [
                    "docker", "exec", container_id, "pg_dump",
                    "-U", user, "-d", database, "-Fc",
                    "--no-owner", "--no-privileges",
                ],
                stdout=stream,
                stderr=subprocess.PIPE,
                check=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if process.returncode:
            raise BackupError(f"backup_pg_dump_failed_rc_{process.returncode}")
        if candidate.stat().st_size <= 0:
            raise BackupError("backup_pg_dump_empty")
        os.replace(candidate, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _restore_query(container: str, sql: str) -> str:
    return _run_text(
        [
            "docker", "exec", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
            "-At", "-U", "restore", "-d", "restore", "-c", sql,
        ],
        label="backup_restore_query",
    )


def _assert_restore_resource_absent(kind: str, name: str) -> None:
    result = subprocess.run(
        ["docker", kind, "inspect", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        raise BackupError(f"backup_restore_{kind}_cleanup_incomplete")


def restore_smoke(artifact: Path, source: Mapping[str, Any]) -> dict[str, Any]:
    nonce = secrets.token_hex(8)
    container = f"market_pipeline_restore_{nonce}"
    volume = f"market_pipeline_restore_{nonce}_data"
    label = f"market-pipeline-backup-{nonce}"
    created_volume = False
    created_container = False
    cleanup_error: BackupError | None = None
    try:
        _run_text(
            [
                "docker", "volume", "create", "--label",
                f"io.gold-trade.market-backup-run={label}", volume,
            ],
            label="backup_restore_volume_create",
        )
        created_volume = True
        _run_text(
            [
                "docker", "run", "-d", "--name", container,
                "--label", f"io.gold-trade.market-backup-run={label}",
                "--network", "none",
                "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
                "-e", "POSTGRES_USER=restore",
                "-e", "POSTGRES_DB=restore",
                "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            ],
            label="backup_restore_container_start",
        )
        created_container = True
        ready = False
        for _attempt in range(60):
            result = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "restore", "-d", "restore"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise BackupError("backup_restore_not_ready")
        with artifact.open("rb") as stream:
            result = subprocess.run(
                [
                    "docker", "exec", "-i", container, "pg_restore",
                    "-U", "restore", "-d", "restore",
                    "--exit-on-error", "--no-owner", "--no-privileges",
                ],
                stdin=stream,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode:
            raise BackupError(f"backup_restore_failed_rc_{result.returncode}")
        versions_raw = _restore_query(
            container,
            "SELECT COALESCE(string_agg(version::text, ',' ORDER BY version),'') "
            "FROM market_data.schema_migrations",
        )
        restored = {
            "schema_versions": [int(item) for item in versions_raw.split(",") if item],
            "table_count": int(
                _restore_query(
                    container,
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='market_data'",
                )
            ),
            "fact_count": int(
                _restore_query(container, "SELECT count(*) FROM market_data.market_facts")
            ),
        }
        for key in ("schema_versions", "table_count", "fact_count"):
            if restored[key] != source[key]:
                raise BackupError("backup_restore_reconciliation_mismatch")
        return {"status": "PASS", **restored, "cleanup_status": "PASS"}
    finally:
        try:
            if created_container:
                owner = _run_text(
                    [
                        "docker", "inspect", "-f",
                        '{{index .Config.Labels "io.gold-trade.market-backup-run"}}',
                        container,
                    ],
                    label="backup_restore_container_owner",
                )
                if owner != label:
                    raise BackupError("backup_restore_container_owner_mismatch")
                _run_text(["docker", "rm", "-f", container], label="backup_restore_container_remove")
            if created_volume:
                owner = _run_text(
                    [
                        "docker", "volume", "inspect", "-f",
                        '{{index .Labels "io.gold-trade.market-backup-run"}}', volume,
                    ],
                    label="backup_restore_volume_owner",
                )
                if owner != label:
                    raise BackupError("backup_restore_volume_owner_mismatch")
                _run_text(["docker", "volume", "rm", volume], label="backup_restore_volume_remove")
            if created_container:
                _assert_restore_resource_absent("container", container)
            if created_volume:
                _assert_restore_resource_absent("volume", volume)
        except BackupError as exc:
            cleanup_error = exc
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise cleanup_error


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_directory(path.parent, create=False)
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def create_backup(
    *,
    env_file: Path,
    backup_dir: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
) -> dict[str, Any]:
    values = validate_release_env(env_file, release_sha=release_sha, image_id=image_id)
    root = _secure_directory(backup_dir, create=True)
    if receipt.parent != root or receipt.name != "market-pipeline-backup-receipt.json":
        raise BackupError("backup_receipt_destination_invalid")
    postgres = _postgres_path(values)
    if root == postgres or root in postgres.parents or postgres in root.parents:
        raise BackupError("backup_database_directory_overlap")
    created_at = utc_now()
    if not (postgres / "pgdata" / "PG_VERSION").is_file():
        if not _initial_store_is_empty(postgres):
            raise BackupError("backup_uninitialized_store_not_empty")
        payload: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "status": "INITIAL_EMPTY",
            "created_at_utc": utc_text(created_at),
            "release_sha": release_sha,
            "release_tree": release_tree,
            "image_id": image_id,
            "image_input_signature": image_input_signature,
            "role_env_sha256": file_digest(env_file),
            "source": {"database_initialized": False},
            "backup": None,
            "restore_smoke": {"status": "NOT_APPLICABLE"},
            "off_host_copy_required": False,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        _write_receipt(receipt, payload)
        return payload

    source = inspect_source_database(values)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    artifact = root / f"market-archive-before-{release_sha[:12]}-{stamp}-{secrets.token_hex(4)}.dump"
    _write_dump(
        container_id=str(source["container_id"]),
        user=values.get("MARKET_POSTGRES_USER", "market_data"),
        database=values.get("MARKET_POSTGRES_DB", "market_archive"),
        destination=artifact,
    )
    os.chmod(artifact, 0o600)
    _secure_regular(artifact)
    restore = restore_smoke(artifact, source)
    payload = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "created_at_utc": utc_text(created_at),
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "role_env_sha256": file_digest(env_file),
        "source": source,
        "backup": {
            "path": str(artifact),
            "sha256": file_digest(artifact),
            "size_bytes": artifact.stat().st_size,
            "format": "postgres_custom",
        },
        "restore_smoke": restore,
        "off_host_copy_required": True,
        "database_mutated": False,
        "services_started": False,
        "secrets_disclosed": False,
    }
    _write_receipt(receipt, payload)
    return payload


def verify_receipt(
    *,
    env_file: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    maximum_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    values = validate_release_env(env_file, release_sha=release_sha, image_id=image_id)
    _secure_regular(receipt)
    _secure_directory(receipt.parent, create=False)
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup_receipt_invalid") from exc
    expected = {
        "schema": RECEIPT_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "role_env_sha256": file_digest(env_file),
        "database_mutated": False,
        "services_started": False,
        "secrets_disclosed": False,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise BackupError("backup_receipt_identity_mismatch")
    common_keys = {
        "schema", "status", "created_at_utc", "release_sha", "release_tree",
        "image_id", "image_input_signature", "role_env_sha256", "source",
        "backup", "restore_smoke", "off_host_copy_required", "database_mutated",
        "services_started", "secrets_disclosed",
    }
    if set(payload) != common_keys:
        raise BackupError("backup_receipt_schema_invalid")
    try:
        created = datetime.fromisoformat(
            str(payload["created_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("backup_receipt_timestamp_invalid") from exc
    reference = (now or utc_now()).astimezone(timezone.utc)
    if created > reference + timedelta(seconds=30) or reference - created > timedelta(
        seconds=maximum_age_seconds
    ):
        raise BackupError("backup_receipt_stale")
    status = payload.get("status")
    postgres = _postgres_path(values)
    if status == "INITIAL_EMPTY":
        if (
            payload.get("source") != {"database_initialized": False}
            or payload.get("backup") is not None
            or payload.get("restore_smoke") != {"status": "NOT_APPLICABLE"}
            or payload.get("off_host_copy_required") is not False
        ):
            raise BackupError("backup_initial_empty_contract_invalid")
        if not _initial_store_is_empty(postgres):
            raise BackupError("backup_initial_store_changed")
    elif status == "PASS":
        backup = payload.get("backup")
        restore = payload.get("restore_smoke")
        if (
            not isinstance(backup, dict)
            or not isinstance(restore, dict)
            or set(backup) != {"path", "sha256", "size_bytes", "format"}
            or set(restore)
            != {"status", "schema_versions", "table_count", "fact_count", "cleanup_status"}
            or not isinstance(payload.get("source"), dict)
            or set(payload["source"])
            != {
                "container_id", "database", "database_size_bytes",
                "database_identity_sha256", "schema_versions", "table_count",
                "fact_count",
            }
            or restore.get("status") != "PASS"
            or restore.get("cleanup_status") != "PASS"
            or payload.get("off_host_copy_required") is not True
        ):
            raise BackupError("backup_restore_receipt_invalid")
        source = payload["source"]
        if (
            not re.fullmatch(r"[0-9a-f]{12,64}", str(source.get("container_id") or ""))
            or not SAFE_SQL_ID.fullmatch(str(source.get("database") or ""))
            or not HEX64.fullmatch(str(source.get("database_identity_sha256") or ""))
            or int(source.get("database_size_bytes") or 0) <= 0
            or int(source.get("table_count") or -1) < 0
            or int(source.get("fact_count") or -1) < 0
            or not isinstance(source.get("schema_versions"), list)
            or not source["schema_versions"]
            or source["schema_versions"] != sorted(set(source["schema_versions"]))
            or any(not isinstance(item, int) or item <= 0 for item in source["schema_versions"])
            or restore.get("schema_versions") != source.get("schema_versions")
            or restore.get("table_count") != source.get("table_count")
            or restore.get("fact_count") != source.get("fact_count")
        ):
            raise BackupError("backup_source_restore_metadata_invalid")
        artifact = Path(str(backup.get("path") or ""))
        if artifact.parent != receipt.parent or not ARTIFACT_NAME.fullmatch(artifact.name):
            raise BackupError("backup_artifact_location_invalid")
        _secure_regular(artifact)
        if (
            not HEX64.fullmatch(str(backup.get("sha256") or ""))
            or file_digest(artifact) != backup["sha256"]
            or artifact.stat().st_size != int(backup.get("size_bytes") or -1)
            or backup.get("format") != "postgres_custom"
        ):
            raise BackupError("backup_artifact_drifted")
    else:
        raise BackupError("backup_receipt_status_invalid")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--env-file", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--release-sha", required=True)
        command.add_argument("--release-tree", required=True)
        command.add_argument("--image-id", required=True)
        command.add_argument("--image-input-signature", required=True)
        if name == "create":
            command.add_argument("--backup-dir", type=Path, required=True)
            command.add_argument("--confirm", required=True)
        else:
            command.add_argument("--maximum-age-seconds", type=int, default=3600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not RELEASE_SHA.fullmatch(args.release_sha):
            raise BackupError("backup_release_sha_invalid")
        if not RELEASE_SHA.fullmatch(args.release_tree):
            raise BackupError("backup_release_tree_invalid")
        if not IMAGE_ID.fullmatch(args.image_id):
            raise BackupError("backup_image_id_invalid")
        if not HEX64.fullmatch(args.image_input_signature):
            raise BackupError("backup_image_signature_invalid")
        common = {
            "env_file": args.env_file,
            "receipt": args.receipt,
            "release_sha": args.release_sha,
            "release_tree": args.release_tree,
            "image_id": args.image_id,
            "image_input_signature": args.image_input_signature,
        }
        if args.command == "create":
            if args.confirm != CREATE_CONFIRMATION:
                raise BackupError("backup_confirmation_invalid")
            document = create_backup(backup_dir=args.backup_dir, **common)
        else:
            if not 60 <= args.maximum_age_seconds <= 86400:
                raise BackupError("backup_maximum_age_invalid")
            document = verify_receipt(
                maximum_age_seconds=args.maximum_age_seconds, **common
            )
        print(
            json.dumps(
                {
                    "status": "pass",
                    "backup_status": document["status"],
                    "release_sha": document["release_sha"],
                    "database_mutated": False,
                    "services_started": False,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, BackupError) as exc:
        print(
            json.dumps(
                {"status": "fail", "reason_code": str(exc), "secrets_disclosed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
