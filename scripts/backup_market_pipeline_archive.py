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
from collections.abc import Callable
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        RELEASE_SHA,
        parse_env,
        validate_source,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
HOT_CREATE_CONFIRMATION = "create-production-market-pipeline-archive-hot-backup"
RECEIPT_SCHEMA = "market_pipeline_backup_restore/1.2"
JOURNAL_SCHEMA = "market_pipeline_backup_journal/1.0"
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


def _database_invariants(query: Any) -> dict[str, Any]:
    """Return exact, deterministic restore invariants for all market_data data.

    A total table count and one representative fact count can both match after
    data loss.  The receipt therefore binds every table row count, every owned
    sequence position, the ordered migration set, and the schema catalogue.
    """

    table_names = [
        item
        for item in query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='market_data' ORDER BY table_name",
            label="backup_table_names",
        ).splitlines()
        if item
    ]
    if not table_names or len(table_names) != len(set(table_names)) or any(
        not SAFE_SQL_ID.fullmatch(item) for item in table_names
    ):
        raise BackupError("backup_database_table_inventory_invalid")
    table_row_counts = {
        table: int(
            query(
                f"SELECT count(*) FROM market_data.{table}",
                label="backup_table_row_count",
            )
        )
        for table in table_names
    }
    if any(value < 0 for value in table_row_counts.values()):
        raise BackupError("backup_database_table_inventory_invalid")
    sequence_names = [
        item
        for item in query(
            "SELECT sequencename FROM pg_sequences "
            "WHERE schemaname='market_data' ORDER BY sequencename",
            label="backup_sequence_names",
        ).splitlines()
        if item
    ]
    if len(sequence_names) != len(set(sequence_names)) or any(
        not SAFE_SQL_ID.fullmatch(item) for item in sequence_names
    ):
        raise BackupError("backup_database_sequence_inventory_invalid")
    sequence_values: dict[str, dict[str, Any]] = {}
    for name in sequence_names:
        raw = query(
            f"SELECT last_value::text || '|' || is_called::text "
            f"FROM market_data.{name}",
            label="backup_sequence_value",
        )
        try:
            value, is_called = raw.split("|", 1)
        except ValueError as exc:
            raise BackupError("backup_database_sequence_inventory_invalid") from exc
        if is_called not in {"t", "f"}:
            raise BackupError("backup_database_sequence_inventory_invalid")
        sequence_values[name] = {
            "last_value": int(value),
            "is_called": is_called == "t",
        }
    catalogue = query(
        "SELECT COALESCE(json_agg(row_to_json(x) ORDER BY "
        "x.table_name,x.ordinal_position)::text,'[]') FROM ("
        "SELECT table_name,ordinal_position,column_name,data_type,is_nullable,"
        "COALESCE(column_default,'') AS column_default "
        "FROM information_schema.columns WHERE table_schema='market_data') x",
        label="backup_schema_catalogue",
    )
    schema_objects = query(
        "SELECT COALESCE(json_agg(row_to_json(x) ORDER BY x.kind,x.identity)::text,'[]') "
        "FROM ("
        "SELECT 'constraint' AS kind, c.oid::regclass::text || ':' || con.conname || ':' || "
        "pg_get_constraintdef(con.oid, true) AS identity "
        "FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='market_data' "
        "UNION ALL SELECT 'index', indexname || ':' || indexdef FROM pg_indexes "
        "WHERE schemaname='market_data' "
        "UNION ALL SELECT 'view', table_name || ':' || view_definition FROM information_schema.views "
        "WHERE table_schema='market_data' "
        "UNION ALL SELECT 'trigger', c.oid::regclass::text || ':' || t.tgname || ':' || "
        "pg_get_triggerdef(t.oid, true) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='market_data' AND NOT t.tgisinternal "
        "UNION ALL SELECT 'function', p.oid::regprocedure::text || ':' || pg_get_functiondef(p.oid) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='market_data') x",
        label="backup_schema_objects",
    )
    return {
        "schema_versions": [
            int(item)
            for item in query(
                "SELECT COALESCE(string_agg(version::text, ',' ORDER BY version),'') "
                "FROM market_data.schema_migrations",
                label="backup_schema_versions",
            ).split(",")
            if item
        ],
        "table_count": len(table_names),
        "fact_count": table_row_counts.get("market_facts", 0),
        "table_row_counts": table_row_counts,
        "sequence_values": sequence_values,
        "schema_catalog_sha256": sha256(catalogue.encode("utf-8")).hexdigest(),
        "schema_objects_sha256": sha256(schema_objects.encode("utf-8")).hexdigest(),
    }


def _running_project_services(project: str) -> list[str]:
    output = _run_text(
        [
            "docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
            "--format", '{{.Label "com.docker.compose.service"}}',
        ],
        label="backup_project_workload_inventory",
    )
    return sorted({line for line in output.splitlines() if line})


def _assert_writer_workloads_quiesced(project: str) -> None:
    if _running_project_services(project) != ["market-database"]:
        raise BackupError("backup_writer_workloads_not_quiesced")


def inspect_source_database(
    values: Mapping[str, str],
    *,
    require_quiesce: bool = True,
) -> dict[str, Any]:
    project = values["MARKET_PIPELINE_PROJECT_NAME"]
    running = _running_project_services(project)
    if require_quiesce:
        _assert_writer_workloads_quiesced(project)
    elif "market-database" not in running:
        raise BackupError("backup_database_not_running")
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
    invariants = _database_invariants(query)
    system_identifier = query(
        "SELECT system_identifier FROM pg_control_system()",
        label="backup_system_identifier",
    )
    size_bytes = int(
        query("SELECT pg_database_size(current_database())", label="backup_database_size")
    )
    if not system_identifier.isdigit() or size_bytes <= 0:
        raise BackupError("backup_database_metadata_invalid")
    schema_versions = invariants["schema_versions"]
    if not schema_versions or schema_versions != sorted(set(schema_versions)):
        raise BackupError("backup_database_schema_versions_invalid")
    return {
        "container_id": str(document.get("Id") or container_id),
        "database": database,
        "database_size_bytes": size_bytes,
        "database_identity_sha256": sha256(
            f"web\0{database}\0{system_identifier}".encode("utf-8")
        ).hexdigest(),
        **invariants,
    }


def _dump_archive_is_valid(*, container_id: str, path: Path) -> bool:
    _secure_regular(path)
    with path.open("rb") as stream:
        result = subprocess.run(
            ["docker", "exec", "-i", container_id, "pg_restore", "--list"],
            stdin=stream,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    return result.returncode == 0


def _write_dump(
    *, container_id: str, user: str, database: str, destination: Path
) -> None:
    candidate = destination.parent / f".{destination.name}.pending"
    if candidate.exists() or candidate.is_symlink():
        _secure_regular(candidate)
        if destination.exists() or destination.is_symlink():
            raise BackupError("backup_dump_resume_ambiguous")
        # A process can be killed while pg_dump is still writing.  Presence of
        # the deterministic candidate alone is therefore not evidence that it
        # is complete.  pg_restore's archive parser is the authoritative local
        # completeness check; only a complete archive is promoted.
        if _dump_archive_is_valid(container_id=container_id, path=candidate):
            os.replace(candidate, destination)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return
        # The name is not discovered or guessed: it is the exact candidate
        # bound by the durable journal.  An invalid partial write is safe to
        # discard and recreate from the still-quiesced source.
        candidate.unlink()
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
        if not _dump_archive_is_valid(container_id=container_id, path=candidate):
            raise BackupError("backup_pg_dump_archive_invalid")
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


def _cleanup_owned_restore_resource(kind: str, name: str, label: str) -> None:
    template = (
        '{{index .Config.Labels "io.gold-trade.market-backup-run"}}'
        if kind == "container"
        else '{{index .Labels "io.gold-trade.market-backup-run"}}'
    )
    result = subprocess.run(
        ["docker", kind, "inspect", "-f", template, name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return
    if result.stdout.strip() != label:
        raise BackupError(f"backup_restore_{kind}_owner_mismatch")
    command = (
        ["docker", "rm", "-f", name]
        if kind == "container"
        else ["docker", "volume", "rm", name]
    )
    _run_text(command, label=f"backup_restore_{kind}_recover")
    _assert_restore_resource_absent(kind, name)


def restore_smoke(
    artifact: Path,
    source: Mapping[str, Any],
    *,
    resource_binding: Mapping[str, str] | None = None,
    before_cleanup: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if resource_binding is None:
        nonce = secrets.token_hex(8)
        resource_binding = {
            "container": f"market_pipeline_restore_{nonce}",
            "volume": f"market_pipeline_restore_{nonce}_data",
            "label": f"market-pipeline-backup-{nonce}",
        }
    if set(resource_binding) != {"container", "volume", "label"}:
        raise BackupError("backup_restore_resource_binding_invalid")
    container = str(resource_binding["container"])
    volume = str(resource_binding["volume"])
    label = str(resource_binding["label"])
    nonce = container.removeprefix("market_pipeline_restore_")
    if (
        not re.fullmatch(r"[0-9a-f]{16}", nonce)
        or volume != f"{container}_data"
        or label != f"market-pipeline-backup-{nonce}"
    ):
        raise BackupError("backup_restore_resource_binding_invalid")
    _cleanup_owned_restore_resource("container", container, label)
    _cleanup_owned_restore_resource("volume", volume, label)
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
        def restore_query(sql: str, *, label: str) -> str:
            del label
            return _restore_query(container, sql)

        restored = _database_invariants(restore_query)
        # pg_dump owns a transactionally consistent snapshot while the live
        # source may continue receiving facts.  The restored dump is therefore
        # the authoritative row/sequence inventory.  The migration set must
        # still match the pre-dump source observation; a concurrent schema
        # transition is not safe to accept.
        if restored["schema_versions"] != source["schema_versions"]:
            raise BackupError("backup_restore_reconciliation_mismatch")
        if before_cleanup is not None:
            before_cleanup(container)
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
        if cleanup_error is not None:
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


def _journal_path(root: Path) -> Path:
    return root / "market-pipeline-backup-journal.json"


def _read_journal(path: Path) -> dict[str, Any]:
    _secure_regular(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup_journal_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != JOURNAL_SCHEMA:
        raise BackupError("backup_journal_invalid")
    return payload


def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
    _write_receipt(path, payload)


def _invariant_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_versions",
        "table_count",
        "fact_count",
        "table_row_counts",
        "sequence_values",
        "schema_catalog_sha256",
        "schema_objects_sha256",
    )
    return {key: payload.get(key) for key in keys}


def _validate_journal_identity(
    payload: Mapping[str, Any],
    *,
    root: Path,
    env_file: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
) -> None:
    expected = {
        "schema": JOURNAL_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "role_env_sha256": file_digest(env_file),
        "receipt_path": str(receipt),
        "secrets_disclosed": False,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise BackupError("backup_journal_identity_mismatch")
    journal_keys = {
        "schema", "status", "backup_status", "run_id", "created_at_utc",
        "release_sha", "release_tree", "image_id", "image_input_signature",
        "role_env_sha256", "receipt_path", "artifact_path", "candidate_path",
        "source_before", "source_after", "backup", "restore_smoke",
        "restore_resources", "secrets_disclosed",
    }
    if (
        set(payload) != journal_keys
        or payload.get("status")
        not in {"PREPARED", "DUMP_READY", "RESTORE_RUNNING", "COMPLETE"}
        or not re.fullmatch(r"[0-9a-f]{16}", str(payload.get("run_id") or ""))
    ):
        raise BackupError("backup_journal_schema_invalid")
    artifact = Path(str(payload.get("artifact_path") or ""))
    candidate = Path(str(payload.get("candidate_path") or ""))
    if (
        artifact.parent != root
        or not ARTIFACT_NAME.fullmatch(artifact.name)
        or candidate != root / f".{artifact.name}.pending"
    ):
        raise BackupError("backup_journal_artifact_invalid")
    resources = payload.get("restore_resources")
    if resources is not None and (
        not isinstance(resources, dict)
        or set(resources) != {"container", "volume", "label"}
    ):
        raise BackupError("backup_journal_resource_binding_invalid")


def _receipt_from_journal(journal: Mapping[str, Any]) -> dict[str, Any]:
    status = journal.get("backup_status")
    if status == "INITIAL_EMPTY":
        source: Mapping[str, Any] = {"database_initialized": False}
        source_after: Mapping[str, Any] = {"database_initialized": False}
        backup_payload = None
        restore: Mapping[str, Any] = {"status": "NOT_APPLICABLE"}
        offhost = False
    elif status == "PASS":
        source = journal["source_before"]
        source_after = journal["source_after"]
        backup_payload = journal["backup"]
        restore = journal["restore_smoke"]
        offhost = True
    else:
        raise BackupError("backup_journal_status_invalid")
    return {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "created_at_utc": journal["created_at_utc"],
        "release_sha": journal["release_sha"],
        "release_tree": journal["release_tree"],
        "image_id": journal["image_id"],
        "image_input_signature": journal["image_input_signature"],
        "role_env_sha256": journal["role_env_sha256"],
        "backup_run_id": journal["run_id"],
        "source": source,
        "source_after": source_after,
        "backup": backup_payload,
        "restore_smoke": restore,
        "off_host_copy_required": offhost,
        "database_mutated": False,
        "services_started": False,
        "secrets_disclosed": False,
    }


def create_backup(
    *,
    env_file: Path,
    backup_dir: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
    refresh_complete: bool = False,
    allow_running_writers: bool = False,
) -> dict[str, Any]:
    values = validate_release_env(env_file, release_sha=release_sha, image_id=image_id)
    require_quiesce = not allow_running_writers
    root = _secure_directory(backup_dir, create=True)
    if receipt.parent != root or receipt.name != "market-pipeline-backup-receipt.json":
        raise BackupError("backup_receipt_destination_invalid")
    postgres = _postgres_path(values)
    if root == postgres or root in postgres.parents or postgres in root.parents:
        raise BackupError("backup_database_directory_overlap")
    journal_path = _journal_path(root)
    journal: dict[str, Any] | None = None
    if journal_path.exists() or journal_path.is_symlink():
        journal = _read_journal(journal_path)
        _validate_journal_identity(
            journal,
            root=root,
            env_file=env_file,
            receipt=receipt,
            release_sha=release_sha,
            release_tree=release_tree,
            image_id=image_id,
            image_input_signature=image_input_signature,
        )
        if journal.get("status") == "COMPLETE":
            payload = _receipt_from_journal(journal)
            if not refresh_complete:
                _write_receipt(receipt, payload)
                return payload
            run_id = str(journal.get("run_id") or "")
            if not re.fullmatch(r"[0-9a-f]{16}", run_id):
                raise BackupError("backup_journal_invalid")
            archived_receipt = root / f"market-pipeline-backup-receipt.{run_id}.json"
            archived_journal = root / f"market-pipeline-backup-journal.{run_id}.json"
            if receipt.exists() or receipt.is_symlink():
                _secure_regular(receipt)
                try:
                    current_payload = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupError("backup_refresh_receipt_drift") from exc
                if current_payload != payload:
                    raise BackupError("backup_refresh_receipt_drift")
            elif not (archived_receipt.exists() or archived_receipt.is_symlink()):
                raise BackupError("backup_refresh_receipt_missing")
            if archived_journal.exists() or archived_journal.is_symlink():
                raise BackupError("backup_refresh_archive_exists")
            if archived_receipt.exists() or archived_receipt.is_symlink():
                _secure_regular(archived_receipt)
                try:
                    archived_payload = json.loads(
                        archived_receipt.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupError("backup_refresh_archive_invalid") from exc
                if archived_payload != payload or receipt.exists() or receipt.is_symlink():
                    raise BackupError("backup_refresh_archive_invalid")
            else:
                os.replace(receipt, archived_receipt)
                directory = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            # If a kill happened after archiving the receipt but before this
            # replace, the exact archived receipt above authorizes resumption.
            os.replace(journal_path, archived_journal)
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            journal = None
    created_at = utc_now()
    if not (postgres / "pgdata" / "PG_VERSION").is_file():
        if _running_project_services(values["MARKET_PIPELINE_PROJECT_NAME"]):
            raise BackupError("backup_writer_workloads_not_quiesced")
        if not _initial_store_is_empty(postgres):
            raise BackupError("backup_uninitialized_store_not_empty")
        if journal is None:
            nonce = secrets.token_hex(8)
            stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
            artifact = root / (
                f"market-archive-before-{release_sha[:12]}-{stamp}-{nonce[:8]}.dump"
            )
            journal = {
                "schema": JOURNAL_SCHEMA,
                "status": "COMPLETE",
                "backup_status": "INITIAL_EMPTY",
                "run_id": nonce,
                "created_at_utc": utc_text(created_at),
                "release_sha": release_sha,
                "release_tree": release_tree,
                "image_id": image_id,
                "image_input_signature": image_input_signature,
                "role_env_sha256": file_digest(env_file),
                "receipt_path": str(receipt),
                "artifact_path": str(artifact),
                "candidate_path": str(root / f".{artifact.name}.pending"),
                "source_before": {"database_initialized": False},
                "source_after": {"database_initialized": False},
                "backup": None,
                "restore_smoke": {"status": "NOT_APPLICABLE"},
                "restore_resources": None,
                "secrets_disclosed": False,
            }
            _write_journal(journal_path, journal)
        payload = _receipt_from_journal(journal)
        _write_receipt(receipt, payload)
        return payload

    if journal is None:
        source = inspect_source_database(values, require_quiesce=require_quiesce)
        nonce = secrets.token_hex(8)
        stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        restore_nonce = secrets.token_hex(8)
        artifact = root / (
            f"market-archive-before-{release_sha[:12]}-{stamp}-{nonce[:8]}.dump"
        )
        journal = {
            "schema": JOURNAL_SCHEMA,
            "status": "PREPARED",
            "backup_status": None,
            "run_id": nonce,
            "created_at_utc": utc_text(created_at),
            "release_sha": release_sha,
            "release_tree": release_tree,
            "image_id": image_id,
            "image_input_signature": image_input_signature,
            "role_env_sha256": file_digest(env_file),
            "receipt_path": str(receipt),
            "artifact_path": str(artifact),
            "candidate_path": str(root / f".{artifact.name}.pending"),
            "source_before": source,
            "source_after": None,
            "backup": None,
            "restore_smoke": None,
            "restore_resources": {
                "container": f"market_pipeline_restore_{restore_nonce}",
                "volume": f"market_pipeline_restore_{restore_nonce}_data",
                "label": f"market-pipeline-backup-{restore_nonce}",
            },
            "secrets_disclosed": False,
        }
        _write_journal(journal_path, journal)
    source = journal["source_before"]
    current_source = inspect_source_database(values, require_quiesce=require_quiesce)
    if (
        current_source.get("database_identity_sha256")
        != source.get("database_identity_sha256")
        or _invariant_view(current_source) != _invariant_view(source)
    ):
        raise BackupError("backup_source_changed_before_dump")
    artifact = Path(str(journal["artifact_path"]))
    if not artifact.exists():
        _write_dump(
            container_id=str(source["container_id"]),
            user=values.get("MARKET_POSTGRES_USER", "market_data"),
            database=values.get("MARKET_POSTGRES_DB", "market_archive"),
            destination=artifact,
        )
    _secure_regular(artifact)
    if not _dump_archive_is_valid(
        container_id=str(source["container_id"]), path=artifact
    ):
        raise BackupError("backup_artifact_archive_invalid")
    observed_backup = {
        "path": str(artifact),
        "sha256": file_digest(artifact),
        "size_bytes": artifact.stat().st_size,
        "format": "postgres_custom",
    }
    if journal.get("backup") is not None and journal["backup"] != observed_backup:
        raise BackupError("backup_artifact_drifted_during_resume")
    journal["backup"] = observed_backup
    journal["status"] = "DUMP_READY"
    _write_journal(journal_path, journal)
    source_after = inspect_source_database(values, require_quiesce=require_quiesce)
    if (
        source.get("database_identity_sha256")
        != source_after.get("database_identity_sha256")
        or _invariant_view(source) != _invariant_view(source_after)
    ):
        raise BackupError("backup_source_changed_during_dump")
    journal["source_after"] = source_after
    journal["status"] = "RESTORE_RUNNING"
    _write_journal(journal_path, journal)
    restore = restore_smoke(
        artifact,
        source,
        resource_binding=journal["restore_resources"],
    )
    if not (
        _invariant_view(source)
        == _invariant_view(source_after)
        == _invariant_view(restore)
    ):
        raise BackupError("backup_restore_reconciliation_mismatch")
    journal["restore_smoke"] = restore
    journal["backup_status"] = "PASS"
    journal["status"] = "COMPLETE"
    _write_journal(journal_path, journal)
    payload = _receipt_from_journal(journal)
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
    maximum_age_seconds: int | None,
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
        "image_id", "image_input_signature", "role_env_sha256", "backup_run_id",
        "source", "source_after",
        "backup", "restore_smoke", "off_host_copy_required", "database_mutated",
        "services_started", "secrets_disclosed",
    }
    if set(payload) != common_keys:
        raise BackupError("backup_receipt_schema_invalid")
    if not re.fullmatch(r"[0-9a-f]{16}", str(payload.get("backup_run_id") or "")):
        raise BackupError("backup_receipt_run_id_invalid")
    journal_path = _journal_path(receipt.parent)
    journal = _read_journal(journal_path)
    _validate_journal_identity(
        journal,
        root=receipt.parent,
        env_file=env_file,
        receipt=receipt,
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_input_signature=image_input_signature,
    )
    if journal.get("status") != "COMPLETE" or payload != _receipt_from_journal(journal):
        raise BackupError("backup_receipt_journal_mismatch")
    try:
        created = datetime.fromisoformat(
            str(payload["created_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("backup_receipt_timestamp_invalid") from exc
    reference = (now or utc_now()).astimezone(timezone.utc)
    if created > reference + timedelta(seconds=30) or (
        maximum_age_seconds is not None
        and reference - created > timedelta(seconds=maximum_age_seconds)
    ):
        raise BackupError("backup_receipt_stale")
    status = payload.get("status")
    postgres = _postgres_path(values)
    if status == "INITIAL_EMPTY":
        if (
            payload.get("source") != {"database_initialized": False}
            or payload.get("source_after") != {"database_initialized": False}
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
        source_after = payload.get("source_after")
        if (
            not isinstance(backup, dict)
            or not isinstance(restore, dict)
            or set(backup) != {"path", "sha256", "size_bytes", "format"}
            or set(restore)
            != {
                "status", "schema_versions", "table_count", "fact_count",
                "table_row_counts", "sequence_values", "schema_catalog_sha256",
                "schema_objects_sha256", "cleanup_status",
            }
            or not isinstance(payload.get("source"), dict)
            or not isinstance(source_after, dict)
            or set(payload["source"])
            != {
                "container_id", "database", "database_size_bytes",
                "database_identity_sha256", "schema_versions", "table_count",
                "fact_count", "table_row_counts", "sequence_values",
                "schema_catalog_sha256", "schema_objects_sha256",
            }
            or set(source_after) != set(payload["source"])
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
            or not isinstance(source.get("table_count"), int)
            or source["table_count"] <= 0
            or not isinstance(source.get("fact_count"), int)
            or source["fact_count"] < 0
            or not isinstance(source.get("table_row_counts"), dict)
            or set(source["table_row_counts"]) == set()
            or len(source["table_row_counts"]) != source.get("table_count")
            or any(
                not SAFE_SQL_ID.fullmatch(str(key))
                or not isinstance(value, int)
                or value < 0
                for key, value in source["table_row_counts"].items()
            )
            or source["table_row_counts"].get("market_facts")
            != source.get("fact_count")
            or not isinstance(source.get("sequence_values"), dict)
            or any(
                not SAFE_SQL_ID.fullmatch(str(key))
                or not isinstance(value, dict)
                or set(value) != {"last_value", "is_called"}
                or not isinstance(value.get("last_value"), int)
                or not isinstance(value.get("is_called"), bool)
                for key, value in source["sequence_values"].items()
            )
            or not HEX64.fullmatch(str(source.get("schema_catalog_sha256") or ""))
            or not HEX64.fullmatch(str(source.get("schema_objects_sha256") or ""))
            or not isinstance(source.get("schema_versions"), list)
            or not source["schema_versions"]
            or source["schema_versions"] != sorted(set(source["schema_versions"]))
            or any(not isinstance(item, int) or item <= 0 for item in source["schema_versions"])
            or source.get("database_identity_sha256")
            != source_after.get("database_identity_sha256")
            or _invariant_view(source) != _invariant_view(source_after)
            or _invariant_view(source) != _invariant_view(restore)
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
            command.add_argument("--refresh-complete", action="store_true")
            command.add_argument("--allow-running-writers", action="store_true")
            command.add_argument("--confirm", required=True)
        else:
            command.add_argument("--maximum-age-seconds", type=int, default=3600)
            command.add_argument("--allow-stale", action="store_true")
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
            expected_confirm = (
                HOT_CREATE_CONFIRMATION
                if args.allow_running_writers
                else CREATE_CONFIRMATION
            )
            if args.confirm != expected_confirm:
                raise BackupError("backup_confirmation_invalid")
            document = create_backup(
                backup_dir=args.backup_dir,
                refresh_complete=args.refresh_complete,
                allow_running_writers=args.allow_running_writers,
                **common,
            )
        else:
            if not args.allow_stale and not 60 <= args.maximum_age_seconds <= 86400:
                raise BackupError("backup_maximum_age_invalid")
            document = verify_receipt(
                maximum_age_seconds=(None if args.allow_stale else args.maximum_age_seconds),
                **common,
            )
        artifact = document.get("backup")
        print(
            json.dumps(
                {
                    "status": "pass",
                    "backup_status": document["status"],
                    "release_sha": document["release_sha"],
                    "artifact_name": (
                        Path(str(artifact["path"])).name
                        if isinstance(artifact, dict)
                        else None
                    ),
                    "artifact_sha256": (
                        artifact["sha256"] if isinstance(artifact, dict) else None
                    ),
                    "artifact_size_bytes": (
                        artifact["size_bytes"] if isinstance(artifact, dict) else None
                    ),
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
