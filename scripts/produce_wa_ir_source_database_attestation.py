#!/usr/bin/env python3
"""Attest an exact source backup through an isolated restore drill."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Any, Callable, Mapping

from scripts.wa_ir_production_operation import (
    DATABASE_FINGERPRINT_ALGORITHM,
    DATABASE_FINGERPRINT_CLIENT_ENCODING,
    DATABASE_FINGERPRINT_PGOPTIONS,
    MAX_PAYLOAD_BYTES,
    ProductionOperationError,
    StreamDigest,
    _fingerprint_from_streams,
    _run_streaming_sha256,
)
from scripts.wa_ir_production_object_storage_transport import (
    _journal_ciphertext_path,
    _load_journal,
    _validate_prepared_journal,
)
from scripts.wa_ir_production_transport_contract import (
    PRODUCTION_BUCKET,
    ProductionTransportError,
    SHA256_RE,
    validate_operation_id,
)


ATTESTATION_SCHEMA = "wa-ir-source-backup-database-attestation-v1"
DOCKER = "/usr/bin/docker"
MAX_QUERY_OUTPUT_BYTES = 8 * 1024 * 1024
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-z]{1,64}$")
_VERSION_RE = re.compile(r"^[\x21-\x7e]{1,1024}$")
_OBJECT_KEY_RE = re.compile(r"^dark-standby/[a-z0-9._/-]{8,1024}$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
}
_LABEL_OPERATION = "trading-bot.production.source-backup-operation-id"
_LABEL_PURPOSE = "trading-bot.production.purpose"
_PURPOSE = "wa-ir-source-backup-restore-attestation"
_SOURCE_BACKUP_ARTIFACT_KIND = "database-backup"


class SourceDatabaseAttestationError(RuntimeError):
    """A redacted fail-closed source-backup attestation error."""


@dataclass(frozen=True)
class BackupIdentity:
    sha256: str
    bytes: int
    stat_fields: tuple[int, ...]


def confirmation_phrase(operation_id: str, release_sha: str) -> str:
    return f"attest-wa-ir-source-backup:{operation_id}:{release_sha}"


@contextmanager
def _source_operation_lock(
    operation_id: str,
    *,
    lock_root: Path = Path("/run"),
    required_uid: int = 0,
):  # noqa: ANN202
    if type(required_uid) is not int or required_uid < 0:
        raise SourceDatabaseAttestationError(
            "source backup operation lock owner is invalid"
        )
    try:
        operation_id = validate_operation_id(operation_id)
        root_metadata = lock_root.stat(follow_symlinks=False)
    except (OSError, ProductionTransportError) as exc:
        raise SourceDatabaseAttestationError(
            "source backup operation lock root is unavailable"
        ) from exc
    if (
        not lock_root.is_absolute()
        or not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != required_uid
        or stat.S_IMODE(root_metadata.st_mode) & 0o022
    ):
        raise SourceDatabaseAttestationError(
            "source backup operation lock root is unsafe"
        )
    path = lock_root / (
        f"trading-bot-wa-ir-source-backup-{operation_id}.lock"
    )
    descriptor = -1
    directory_descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise SourceDatabaseAttestationError(
                "source backup operation lock is unsafe"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceDatabaseAttestationError(
                "another source backup restore drill is already active"
            ) from exc
        os.fsync(descriptor)
        directory_descriptor = os.open(
            lock_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(directory_descriptor)
        yield
    except SourceDatabaseAttestationError:
        raise
    except OSError as exc:
        raise SourceDatabaseAttestationError(
            "source backup operation lock is unavailable"
        ) from exc
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _run(arguments: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=_SAFE_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceDatabaseAttestationError(
            f"required scratch command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_QUERY_OUTPUT_BYTES
        or len(result.stderr) > 2 * 1024 * 1024
    ):
        raise SourceDatabaseAttestationError(
            f"required scratch command failed closed: {Path(arguments[0]).name}"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise SourceDatabaseAttestationError(
            "required scratch command returned non-UTF-8 output"
        ) from exc


def _inspect_optional(kind: str, name: str) -> Mapping[str, Any] | None:
    try:
        result = subprocess.run(
            [DOCKER, kind, "inspect", name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=_SAFE_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceDatabaseAttestationError(
            "scratch resource inspection is unavailable"
        ) from exc
    if result.returncode != 0:
        try:
            error = result.stderr.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SourceDatabaseAttestationError(
                "scratch resource inspection failed ambiguously"
            ) from exc
        escaped = re.escape(name)
        missing = (
            rf"(?:(?:Error response from daemon|Error): )?"
            rf"(?:No such container: {escaped}|"
            rf"No such object: {escaped}|"
            rf"No such volume: {escaped})"
        )
        if result.returncode == 1 and re.fullmatch(missing, error):
            return None
        raise SourceDatabaseAttestationError(
            "scratch resource inspection failed ambiguously"
        )
    if len(result.stdout) > 2 * 1024 * 1024:
        raise SourceDatabaseAttestationError(
            "scratch resource inspection is oversized"
        )
    try:
        documents = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SourceDatabaseAttestationError(
            "scratch resource inspection is invalid"
        ) from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
    ):
        raise SourceDatabaseAttestationError(
            "scratch resource inspection is invalid"
        )
    return documents[0]


def _open_stable_backup(path: Path) -> tuple[int, BackupIdentity]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= MAX_PAYLOAD_BYTES
        ):
            raise SourceDatabaseAttestationError(
                "source backup file is unsafe"
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        fields = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_fields = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if fields != after_fields or size != before.st_size:
            raise SourceDatabaseAttestationError(
                "source backup changed while hashing"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, BackupIdentity(digest.hexdigest(), size, fields)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _verify_held_backup(
    descriptor: int,
    path: Path,
    identity: BackupIdentity,
) -> None:
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise SourceDatabaseAttestationError(
            "held source backup identity is unavailable"
        ) from exc
    descriptor_fields = (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
        descriptor_metadata.st_mode,
        descriptor_metadata.st_uid,
        descriptor_metadata.st_nlink,
        descriptor_metadata.st_size,
        descriptor_metadata.st_mtime_ns,
        descriptor_metadata.st_ctime_ns,
    )
    path_fields = (
        path_metadata.st_dev,
        path_metadata.st_ino,
        path_metadata.st_mode,
        path_metadata.st_uid,
        path_metadata.st_nlink,
        path_metadata.st_size,
        path_metadata.st_mtime_ns,
        path_metadata.st_ctime_ns,
    )
    if descriptor_fields != identity.stat_fields or path_fields != identity.stat_fields:
        raise SourceDatabaseAttestationError(
            "source backup path or held descriptor changed"
        )


def _rehash_held_backup(
    descriptor: int,
    path: Path,
    identity: BackupIdentity,
) -> None:
    _verify_held_backup(descriptor, path, identity)
    digest = hashlib.sha256()
    size = 0
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > identity.bytes:
                raise SourceDatabaseAttestationError(
                    "held source backup grew during restore"
                )
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise SourceDatabaseAttestationError(
            "held source backup could not be rehashed"
        ) from exc
    _verify_held_backup(descriptor, path, identity)
    if (digest.hexdigest(), size) != (identity.sha256, identity.bytes):
        raise SourceDatabaseAttestationError(
            "held source backup content changed during restore"
        )


def _verified_publication(
    journal_path: Path,
    *,
    operation_id: str,
    release_sha: str,
    backup_identity: BackupIdentity,
) -> tuple[str, str]:
    try:
        state = _load_journal(journal_path)
        published = _validate_prepared_journal(
            state,
            ciphertext_path=_journal_ciphertext_path(journal_path),
        )
    except ProductionTransportError as exc:
        raise SourceDatabaseAttestationError(
            "source backup publication journal is invalid"
        ) from exc
    if (
        state.get("phase") != "verified"
        or published.bucket != PRODUCTION_BUCKET
        or state.get("operation_id") != operation_id
        or state.get("artifact_kind") != _SOURCE_BACKUP_ARTIFACT_KIND
        or published.plaintext_sha256 != backup_identity.sha256
        or published.plaintext_bytes != backup_identity.bytes
        or state.get("requested_metadata")
        != {
            "destination-name": "database.dump",
            "release-sha": release_sha,
        }
        or not published.version_id
    ):
        raise SourceDatabaseAttestationError(
            "source backup publication/readback binding differs"
        )
    return published.object_key, published.version_id


def _validate_scratch_container(
    document: Mapping[str, Any],
    *,
    operation_id: str,
    name: str,
    volume: str,
    postgres_image_id: str,
) -> str:
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    host = document.get("HostConfig")
    mounts = document.get("Mounts")
    identifier = document.get("Id")
    restart = host.get("RestartPolicy") if isinstance(host, dict) else None
    if (
        not isinstance(identifier, str)
        or not _CONTAINER_ID_RE.fullmatch(identifier)
        or document.get("Name") != f"/{name}"
        or document.get("Image") != postgres_image_id
        or not isinstance(config, dict)
        or config.get("Image") != postgres_image_id
        or not isinstance(labels, dict)
        or labels
        != {
            _LABEL_OPERATION: operation_id,
            _LABEL_PURPOSE: _PURPOSE,
        }
        or not isinstance(host, dict)
        or host.get("NetworkMode") != "none"
        or host.get("PortBindings") not in (None, {})
        or host.get("Privileged") is not False
        or not isinstance(restart, dict)
        or restart.get("Name") != "no"
        or restart.get("MaximumRetryCount") not in (None, 0)
        or not isinstance(mounts, list)
        or len(mounts) != 1
    ):
        raise SourceDatabaseAttestationError(
            "scratch container identity or isolation differs"
        )
    mount = mounts[0]
    if (
        not isinstance(mount, dict)
        or mount.get("Type") != "volume"
        or mount.get("Name") != volume
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
    ):
        raise SourceDatabaseAttestationError(
            "scratch container mount closure differs"
        )
    return identifier


def _validate_scratch_volume(
    document: Mapping[str, Any],
    *,
    operation_id: str,
    name: str,
) -> None:
    if (
        document.get("Name") != name
        or document.get("Driver") != "local"
        or document.get("Labels")
        != {
            _LABEL_OPERATION: operation_id,
            _LABEL_PURPOSE: _PURPOSE,
        }
        or document.get("Options") not in (None, {})
    ):
        raise SourceDatabaseAttestationError(
            "scratch volume identity differs"
        )


def _cleanup_exact_scratch(
    *,
    operation_id: str,
    container: str,
    volume: str,
    postgres_image_id: str,
) -> bool:
    removed = False
    container_document = _inspect_optional("container", container)
    if container_document is not None:
        _validate_scratch_container(
            container_document,
            operation_id=operation_id,
            name=container,
            volume=volume,
            postgres_image_id=postgres_image_id,
        )
        _run(
            [DOCKER, "container", "rm", "--force", container],
            timeout=120,
        )
        removed = True
    volume_document = _inspect_optional("volume", volume)
    if volume_document is not None:
        _validate_scratch_volume(
            volume_document,
            operation_id=operation_id,
            name=volume,
        )
        _run([DOCKER, "volume", "rm", volume], timeout=120)
        removed = True
    if (
        _inspect_optional("container", container) is not None
        or _inspect_optional("volume", volume) is not None
    ):
        raise SourceDatabaseAttestationError(
            "scratch restore drill did not reach exact zero residue"
        )
    return removed


def _scratch_psql_arguments(
    container: str,
    sql: str,
    *,
    streaming: bool,
) -> list[str]:
    arguments = [
        DOCKER,
        "exec",
        "--env",
        f"PGOPTIONS={DATABASE_FINGERPRINT_PGOPTIONS}",
        "--env",
        f"PGCLIENTENCODING={DATABASE_FINGERPRINT_CLIENT_ENCODING}",
        container,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-psqlrc",
        "-U",
        "restore",
        "-d",
        "restore",
    ]
    if streaming:
        arguments.extend(("--quiet", "--command", sql))
    else:
        arguments.extend(("-Atqc", sql))
    return arguments


def _scratch_query(container: str, sql: str) -> str:
    return _run(
        _scratch_psql_arguments(container, sql, streaming=False),
        timeout=300,
    )


def _scratch_stream(container: str, sql: str) -> StreamDigest:
    try:
        return _run_streaming_sha256(
            _scratch_psql_arguments(container, sql, streaming=True),
            timeout=1800,
            env=_SAFE_ENV,
        )
    except ProductionOperationError as exc:
        raise SourceDatabaseAttestationError(
            "scratch streaming fingerprint failed closed"
        ) from exc


def build_attestation(
    *,
    operation_id: str,
    release_sha: str,
    database_backup_sha256: str,
    database_backup_bytes: int,
    database_backup_object_key: str,
    database_backup_version_id: str,
    postgres_image_id: str,
    scratch_postgres_system_id: str,
    query: Callable[[str], str],
    stream_copy: Callable[[str], StreamDigest],
) -> dict[str, object]:
    try:
        operation_id = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise SourceDatabaseAttestationError("operation id is invalid") from exc
    if (
        not _RELEASE_RE.fullmatch(release_sha)
        or not SHA256_RE.fullmatch(database_backup_sha256)
        or type(database_backup_bytes) is not int
        or not 1 <= database_backup_bytes <= MAX_PAYLOAD_BYTES
        or not _OBJECT_KEY_RE.fullmatch(database_backup_object_key)
        or ".." in database_backup_object_key.split("/")
        or not _VERSION_RE.fullmatch(database_backup_version_id)
        or any(character.isspace() for character in database_backup_version_id)
        or not _IMAGE_ID_RE.fullmatch(postgres_image_id)
        or not re.fullmatch(r"[0-9]{10,20}", scratch_postgres_system_id)
    ):
        raise SourceDatabaseAttestationError(
            "source backup operation binding is invalid"
        )
    revision = query("SELECT version_num FROM alembic_version")
    if not _REVISION_RE.fullmatch(revision):
        raise SourceDatabaseAttestationError(
            "restored source migration revision is invalid"
        )
    tables = [
        value
        for value in query(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' ORDER BY tablename"
        ).splitlines()
        if value
    ]
    try:
        fingerprint, row_count, table_count = _fingerprint_from_streams(
            tables,
            stream_copy,
        )
    except ProductionOperationError as exc:
        raise SourceDatabaseAttestationError(
            "restored source fingerprint contract failed"
        ) from exc
    if row_count > 10**15 or not 1 <= table_count <= 100_000:
        raise SourceDatabaseAttestationError(
            "restored source inventory is outside its bound"
        )
    return {
        "schema": ATTESTATION_SCHEMA,
        "status": "source-backup-database-attested",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "database_backup_sha256": database_backup_sha256,
        "database_backup_bytes": database_backup_bytes,
        "database_backup_object_key": database_backup_object_key,
        "database_backup_version_id": database_backup_version_id,
        "postgres_image_id": postgres_image_id,
        "scratch_postgres_system_id": scratch_postgres_system_id,
        "source_database": {
            "alembic_revision": revision,
            "fingerprint_algorithm": DATABASE_FINGERPRINT_ALGORITHM,
            "database_fingerprint_sha256": fingerprint,
            "row_count": row_count,
            "table_count": table_count,
        },
        "restore_single_transaction": True,
        "scratch_network_mode": "none",
        "source_database_mutated": False,
        "source_or_current_mounted": False,
    }


def _restore_and_attest(
    *,
    operation_id: str,
    release_sha: str,
    backup: Path,
    backup_descriptor: int,
    backup_identity: BackupIdentity,
    backup_object_key: str,
    backup_version_id: str,
    postgres_image_id: str,
) -> dict[str, object]:
    compact = operation_id.replace("-", "")
    container = f"tb-wa-src-attest-{compact}"
    volume = f"{container}-pgdata"
    recovered_prior_residue = _cleanup_exact_scratch(
        operation_id=operation_id,
        container=container,
        volume=volume,
        postgres_image_id=postgres_image_id,
    )
    image_identity = _run(
        [DOCKER, "image", "inspect", "--format", "{{.Id}}", postgres_image_id],
        timeout=60,
    )
    if image_identity != postgres_image_id:
        raise SourceDatabaseAttestationError(
            "scratch PostgreSQL image identity differs"
        )
    scratch_container_id: str | None = None
    result: dict[str, object] | None = None
    failure: Exception | None = None
    try:
        observed_volume = _run(
            [
                DOCKER,
                "volume",
                "create",
                "--driver",
                "local",
                "--label",
                f"{_LABEL_OPERATION}={operation_id}",
                "--label",
                f"{_LABEL_PURPOSE}={_PURPOSE}",
                volume,
            ],
            timeout=60,
        )
        if observed_volume != volume:
            raise SourceDatabaseAttestationError(
                "scratch volume creation identity differs"
            )
        volume_document = _inspect_optional("volume", volume)
        if volume_document is None:
            raise SourceDatabaseAttestationError(
                "scratch volume disappeared after creation"
            )
        _validate_scratch_volume(
            volume_document,
            operation_id=operation_id,
            name=volume,
        )
        observed_container = _run(
            [
                DOCKER,
                "run",
                "--detach",
                "--name",
                container,
                "--network",
                "none",
                "--pull",
                "never",
                "--restart",
                "no",
                "--label",
                f"{_LABEL_OPERATION}={operation_id}",
                "--label",
                f"{_LABEL_PURPOSE}={_PURPOSE}",
                "--mount",
                (
                    f"type=volume,source={volume},"
                    "target=/var/lib/postgresql/data"
                ),
                "--env",
                "POSTGRES_USER=restore",
                "--env",
                "POSTGRES_DB=restore",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                postgres_image_id,
            ],
            timeout=120,
        )
        if not _CONTAINER_ID_RE.fullmatch(observed_container):
            raise SourceDatabaseAttestationError(
                "scratch container creation identity differs"
            )
        container_document = _inspect_optional("container", container)
        if container_document is None:
            raise SourceDatabaseAttestationError(
                "scratch container disappeared after creation"
            )
        identifier = _validate_scratch_container(
            container_document,
            operation_id=operation_id,
            name=container,
            volume=volume,
            postgres_image_id=postgres_image_id,
        )
        if identifier != observed_container:
            raise SourceDatabaseAttestationError(
                "scratch container ID changed after creation"
            )
        scratch_container_id = identifier
        ready = False
        for _attempt in range(120):
            probe = subprocess.run(
                [
                    DOCKER,
                    "exec",
                    container,
                    "pg_isready",
                    "-U",
                    "restore",
                    "-d",
                    "restore",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                env=_SAFE_ENV,
                check=False,
            )
            if probe.returncode == 0:
                try:
                    if _scratch_query(container, "SELECT 1") == "1":
                        ready = True
                        break
                except SourceDatabaseAttestationError:
                    pass
            time.sleep(1)
        if not ready:
            raise SourceDatabaseAttestationError(
                "scratch restore database did not become ready"
            )
        _verify_held_backup(backup_descriptor, backup, backup_identity)
        os.lseek(backup_descriptor, 0, os.SEEK_SET)
        restore = subprocess.run(
            [
                DOCKER,
                "exec",
                "--interactive",
                container,
                "pg_restore",
                "-U",
                "restore",
                "-d",
                "restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
            ],
            stdin=backup_descriptor,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=3600,
            env=_SAFE_ENV,
            check=False,
        )
        _rehash_held_backup(backup_descriptor, backup, backup_identity)
        if restore.returncode != 0 or len(restore.stderr) > 2 * 1024 * 1024:
            raise SourceDatabaseAttestationError(
                "source backup restore drill failed"
            )
        scratch_system_id = _scratch_query(
            container,
            "SELECT system_identifier FROM pg_control_system()",
        )
        result = build_attestation(
            operation_id=operation_id,
            release_sha=release_sha,
            database_backup_sha256=backup_identity.sha256,
            database_backup_bytes=backup_identity.bytes,
            database_backup_object_key=backup_object_key,
            database_backup_version_id=backup_version_id,
            postgres_image_id=postgres_image_id,
            scratch_postgres_system_id=scratch_system_id,
            query=lambda sql: _scratch_query(container, sql),
            stream_copy=lambda sql: _scratch_stream(container, sql),
        )
    except Exception as exc:
        failure = exc
    cleanup_error: Exception | None = None
    try:
        _cleanup_exact_scratch(
            operation_id=operation_id,
            container=container,
            volume=volume,
            postgres_image_id=postgres_image_id,
        )
    except Exception as exc:
        cleanup_error = exc
    if cleanup_error is not None:
        raise SourceDatabaseAttestationError(
            "validated scratch cleanup failed closed"
        ) from cleanup_error
    if failure is not None:
        if isinstance(failure, SourceDatabaseAttestationError):
            raise failure
        raise SourceDatabaseAttestationError(
            "source backup restore attestation failed closed"
        ) from failure
    if result is None:
        raise SourceDatabaseAttestationError(
            "source backup restore attestation produced no result"
        )
    return {
        **result,
        "scratch_container_id": scratch_container_id,
        "recovered_prior_scratch_residue": recovered_prior_residue,
        "scratch_resources_removed": True,
        "zero_residue": True,
    }


def _error_payload(message: str) -> dict[str, str]:
    return {
        "status": "blocked",
        "error": message,
        "error_class": "SourceDatabaseAttestationError",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--database-backup", type=Path, required=True)
    parser.add_argument(
        "--database-backup-publication-journal",
        type=Path,
        required=True,
    )
    parser.add_argument("--postgres-image-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        try:
            operation_id = validate_operation_id(args.operation_id)
        except ProductionTransportError as exc:
            raise SourceDatabaseAttestationError("operation id is invalid") from exc
        if not _RELEASE_RE.fullmatch(args.release_sha):
            raise SourceDatabaseAttestationError("release SHA is invalid")
        required = confirmation_phrase(operation_id, args.release_sha)
        if not args.apply:
            if args.confirm is not None:
                raise SourceDatabaseAttestationError(
                    "--confirm is valid only with --apply"
                )
            result: dict[str, object] = {
                "schema": ATTESTATION_SCHEMA,
                "status": "planned",
                "operation_id": operation_id,
                "release_sha": args.release_sha,
                "required_confirmation": required,
                "source_database_mutated": False,
                "scratch_resources_created": False,
                "network_io": False,
            }
        else:
            if args.confirm != required:
                raise SourceDatabaseAttestationError(
                    f"restore attestation requires --confirm {required}"
                )
            if os.geteuid() != 0:
                raise SourceDatabaseAttestationError(
                    "source backup attestation must run as root"
                )
            with _source_operation_lock(operation_id):
                descriptor = -1
                try:
                    descriptor, backup_identity = _open_stable_backup(
                        args.database_backup
                    )
                    object_key, version_id = _verified_publication(
                        args.database_backup_publication_journal,
                        operation_id=operation_id,
                        release_sha=args.release_sha,
                        backup_identity=backup_identity,
                    )
                    result = _restore_and_attest(
                        operation_id=operation_id,
                        release_sha=args.release_sha,
                        backup=args.database_backup,
                        backup_descriptor=descriptor,
                        backup_identity=backup_identity,
                        backup_object_key=object_key,
                        backup_version_id=version_id,
                        postgres_image_id=args.postgres_image_id,
                    )
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (ProductionOperationError, SourceDatabaseAttestationError) as exc:
        print(json.dumps(_error_payload(str(exc)), sort_keys=True, separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                _error_payload("source backup database attestation failed closed"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
