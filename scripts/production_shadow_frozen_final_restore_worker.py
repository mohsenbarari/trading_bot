#!/usr/bin/env python3
"""Restore one sealed frozen-final source into one isolated shadow role.

The command is plan-only by default.  Apply is intentionally available only
through the Python API with a controller-owned live-authority verifier.  A
copied live-lease claim is immutable input material, not proof that the
controller still owns its lock.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, BinaryIO, Callable, Mapping, Protocol, Sequence
from uuid import UUID

import yaml


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    build_production_shadow_frozen_final_restore_set as RESTORE_SET,
)
from scripts import produce_production_shadow_prepare_material as PREPARE  # noqa: E402
from scripts import produce_production_shadow_source_snapshot as SOURCE  # noqa: E402
from scripts.production_shadow_cutover_controller import (  # noqa: E402
    CutoverContractError,
    read_root_only_manifest,
)
from scripts.render_three_site_production_shadow_role_compose import (  # noqa: E402
    ProductionShadowRoleError,
    canonical_role_compose_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.wa_ir_production_operation import (  # noqa: E402
    DATABASE_FINGERPRINT_CLIENT_ENCODING,
    DATABASE_FINGERPRINT_PGOPTIONS,
    ProductionOperationError,
    StreamDigest,
    _fingerprint_from_streams,
    _load_migration_graph,
    _migration_ancestors,
    _run_streaming_sha256,
)


ROLE_MANIFEST_SCHEMA = "production-shadow-frozen-final-restore-role-v1"
INSTALLER_RECEIPT_SCHEMA = (
    "production-shadow-frozen-final-restore-installation-v1"
)
JOURNAL_EVENT_SCHEMA = (
    "production-shadow-frozen-final-restore-journal-event-v1"
)
EVIDENCE_SCHEMA = "production-shadow-frozen-final-restore-evidence-v1"
RESULT_SCHEMA = "production-shadow-frozen-final-restore-result-v1"
LIVE_AUTHORITY_SCHEMA = (
    "production-shadow-controller-live-authority-verification-v1"
)
LIVE_LEASE_CLAIM_SCHEMA = (
    "production-shadow-nginx-coordinator-live-lease-claim-v1"
)
LIVE_LEASE_OWNER_ACTION = "restore-shadow-frozen-final"
LIVE_LEASE_SUCCESS_OUTCOME = "frozen-final-shadow-restored"

PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
DATA_ROOT_PREFIX = Path(
    "/srv/trading-bot-three-site-production-shadow-data"
)
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
RUNNING_WORKER_PATH = Path(__file__).resolve()
DOCKER = "/usr/bin/docker"
GIT = "/usr/bin/git"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_TAR_MEMBERS = 250_000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
POSTGRES_RUNTIME_UID = 70
POSTGRES_RUNTIME_GID = 70
ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{7,127}$")
ROLE_NAMES = ("bot_fi", "webapp_fi", "webapp_ir")
ROLE_PATHS = {
    "bot_fi": "bot-fi",
    "webapp_fi": "webapp-fi",
    "webapp_ir": "webapp-ir",
}
ROLE_PREFIXES = {
    "bot_fi": "BOT_FI",
    "webapp_fi": "WEBAPP_FI",
    "webapp_ir": "WEBAPP_IR",
}
ROLE_TRANSPORTS = {
    "bot_fi": "host-local-create-only",
    "webapp_fi": "ssh-control",
    "webapp_ir": "arvan-private-versioned-age",
}
ARTIFACT_KINDS = (
    "database-backup",
    "uploads-archive",
    "audit-archive",
)
ACTIONS = (
    "verify-inputs",
    "initialize-generation",
    "restore-postgres",
    "restore-files",
    "verify-final",
)
MUTATING_ACTIONS = frozenset(
    {"initialize-generation", "restore-postgres", "restore-files"}
)
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/root/.docker",
}
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}

ROLE_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_path",
        "controller_manifest_sha256",
        "restore_set_path",
        "restore_set_sha256",
        "restore_generation_sha256",
        "source_role",
        "target_transport",
        "legacy_frozen_receipt_sha256",
        "snapshot_authorization_claim_sha256",
        "installer_receipt_path",
        "installer_receipt_sha256",
        "canonical_compose_path",
        "canonical_compose_sha256",
        "role_compose_path",
        "role_compose_sha256",
        "prepare_compose_path",
        "prepare_compose_sha256",
        "ca_path",
        "ca_sha256",
        "environment_path",
        "environment_sha256",
        "worker_path",
        "worker_sha256",
        "release_root",
        "project_base",
        "project_name",
        "data_generation_root",
        "secret_generation_root",
        "postgres_image_id",
        "postgres_image_content_identity",
        "app_image_id",
        "app_image_content_identity",
        "target_migration_revision",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
        "artifacts",
        "source_database",
        "constraints",
    }
)
ARTIFACT_FIELDS = frozenset(
    {"path", "sha256", "bytes", "restored_tree_sha256"}
)
SOURCE_DATABASE_FIELDS = frozenset(
    {
        "alembic_revision",
        "fingerprint_algorithm",
        "database_fingerprint_sha256",
        "row_count",
        "table_count",
    }
)
CONSTRAINT_FIELDS = frozenset(
    {
        "generation_isolated",
        "restore_only_compose",
        "redis_archive_absent",
        "redis_pristine_required",
        "no_pull",
        "no_build",
        "no_app_services",
        "no_current_mutation",
        "no_legacy_mutation",
        "no_object_storage_mutation",
        "static_claim_not_authority",
        "controller_live_verifier_required",
    }
)
INSTALLER_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "source_role",
        "target_transport",
        "app_image_id",
        "app_image_content_identity",
        "target_migration_revision",
        "installed_files",
        "data_generation_root",
        "secret_generation_root",
        "redis_restore_bytes",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
    }
)
INSTALLER_FILE_NAMES = frozenset(
    {
        "controller-manifest",
        "restore-set",
        "canonical-compose",
        "role-compose",
        "prepare-compose",
        "ca",
        "environment",
        "worker",
        *ARTIFACT_KINDS,
    }
)
INSTALLER_FILE_FIELDS = frozenset({"path", "sha256", "bytes"})
JOURNAL_EVENT_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "role",
        "release_sha",
        "restore_set_sha256",
        "restore_generation_sha256",
        "role_manifest_sha256",
        "installer_receipt_sha256",
        "legacy_frozen_receipt_sha256",
        "live_lease_claim_path",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "index",
        "kind",
        "action",
        "attempt",
        "evidence_sha256",
        "authority_verification_sha256",
        "previous_event_sha256",
        "event_sha256",
    }
)
EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "role_manifest_sha256",
        "installer_receipt_sha256",
        "legacy_frozen_receipt_sha256",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "business_write_allowed",
        "public_or_private_app_started",
        "redis_restored",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
        "semantic",
    }
)
LIVE_AUTHORITY_FIELDS = frozenset(
    {
        "schema",
        "status",
        "boundary",
        "claim_sha256",
        "claim_epoch",
        "claim_nonce",
        "legacy_frozen_receipt_sha256",
        "controller_lock_held",
        "controller_authoritative",
        "verification_sequence",
        "verification_nonce",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "installer_receipt_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "source_role",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "legacy_frozen_receipt_sha256",
        "database",
        "file_trees",
        "redis_restore_bytes",
        "redis_pristine",
        "public_or_private_app_started",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
        "nginx_state",
        "final_evidence_sha256",
        "claim_consume_outcome",
        "aggregate_three_role_receipt_required",
        "claim_consumed_by_worker",
    }
)
RESULT_DATABASE_FIELDS = frozenset(
    {
        "alembic_revision",
        "database_fingerprint_sha256",
        "row_count",
        "table_count",
    }
)
RESULT_FILE_TREE_FIELDS = frozenset({"uploads", "audit"})


class FrozenFinalRestoreWorkerError(RuntimeError):
    """A redacted, fail-closed final restore error."""


@dataclass(frozen=True)
class ArtifactBinding:
    path: Path
    sha256: str
    bytes: int
    restored_tree_sha256: str | None


@dataclass(frozen=True)
class DatabaseExpectation:
    alembic_revision: str
    fingerprint_algorithm: str
    database_fingerprint_sha256: str
    row_count: int
    table_count: int


@dataclass(frozen=True)
class RuntimePaths:
    project_base: str
    project_name: str
    project_root: Path
    release_root: Path
    data_generation_root: Path
    secret_generation_root: Path
    role_data_root: Path
    restore_input_root: Path
    postgres: Path
    redis: Path
    uploads: Path
    audit: Path
    journal: Path
    evidence: Path
    lock: Path
    prepare_compose: Path
    ca: Path


@dataclass(frozen=True)
class RoleManifest:
    document: Mapping[str, Any]
    canonical_sha256: str
    operation_id: str
    role: str
    release_sha: str
    release_tree_sha: str
    restore_set_sha256: str
    restore_generation_sha256: str
    source_role: str
    controller_manifest_sha256: str
    installer_receipt_sha256: str
    postgres_image_id: str
    postgres_image_content_identity: str
    app_image_id: str
    app_image_content_identity: str
    target_migration_revision: str
    artifacts: Mapping[str, ArtifactBinding]
    source_database: DatabaseExpectation
    paths: RuntimePaths
    controller_manifest_path: Path
    restore_set_path: Path
    canonical_compose_path: Path
    role_compose_path: Path
    prepare_compose_path: Path
    ca_path: Path
    environment_path: Path
    worker_path: Path


@dataclass(frozen=True)
class LeaseBinding:
    document: Mapping[str, Any]
    path: Path
    sha256: str
    epoch: int
    nonce: str
    receipt_path: Path
    receipt_sha256: str


@dataclass(frozen=True)
class DatabaseState:
    alembic_revision: str | None
    database_fingerprint_sha256: str | None
    row_count: int
    table_count: int

    @property
    def public_empty(self) -> bool:
        return self.table_count == 0 and self.row_count == 0


@dataclass(frozen=True)
class DatabaseRuntimeContract:
    service: str
    container_name: str
    config_hash: str
    command: tuple[str, ...] | None
    entrypoint: tuple[str, ...] | None
    user: str
    working_dir: str
    stop_signal: str
    environment: Mapping[str, str]
    healthcheck: Mapping[str, Any] | None
    labels: Mapping[str, str]
    cgroup_parent: str
    restart_policy: str
    nano_cpus: int
    memory: int
    pids_limit: int
    log_config: Mapping[str, Any]


@dataclass(frozen=True)
class NetworkRuntimeContract:
    logical_name: str
    name: str
    project_name: str
    labels: Mapping[str, str]
    compose_version: str


class DockerCommandRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
        stdin: BinaryIO | int | None = subprocess.DEVNULL,
    ) -> str:
        """Run one bounded Docker command and return UTF-8 stdout."""

    def stream(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
    ) -> StreamDigest:
        """Run one bounded Docker command and attest its stdout stream."""


LiveAuthorityVerifier = Callable[
    [LeaseBinding, str], Mapping[str, Any]
]


class SubprocessDockerRunner:
    """Production command runner; tests inject a deterministic replacement."""

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
        stdin: BinaryIO | int | None = subprocess.DEVNULL,
    ) -> str:
        try:
            result = subprocess.run(
                list(arguments),
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                env=dict(env),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FrozenFinalRestoreWorkerError(
                "required Docker command is unavailable"
            ) from exc
        if (
            result.returncode != 0
            or len(result.stdout) > MAX_OUTPUT_BYTES
            or len(result.stderr) > 2 * 1024 * 1024
        ):
            raise FrozenFinalRestoreWorkerError(
                "required Docker command failed closed"
            )
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise FrozenFinalRestoreWorkerError(
                "required Docker command returned non-UTF-8 output"
            ) from exc

    def stream(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
    ) -> StreamDigest:
        try:
            return _run_streaming_sha256(
                list(arguments),
                timeout=timeout,
                env=env,
            )
        except ProductionOperationError as exc:
            raise FrozenFinalRestoreWorkerError(
                "database fingerprint stream failed closed"
            ) from exc


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "document contains non-canonical JSON data"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenFinalRestoreWorkerError(
                "JSON contains a duplicate field"
            )
        result[key] = value
    return result


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid") from exc
    if str(parsed) != value or parsed.version != 4:
        raise FrozenFinalRestoreWorkerError(f"{label} is not canonical UUID4")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise FrozenFinalRestoreWorkerError(f"{label} is outside its bound")
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str):
        raise FrozenFinalRestoreWorkerError(f"{label} path is invalid")
    path = Path(value)
    if (
        not path.is_absolute()
        or path != Path(os.path.abspath(os.fspath(path)))
        or ".." in PurePosixPath(value).parts
        or "current" in PurePosixPath(value).parts
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{label} path is not canonical or isolated"
        )
    return path


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise FrozenFinalRestoreWorkerError(
            "secure no-follow directory traversal is unavailable"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )


def _open_secure_directory(
    path: Path,
    *,
    label: str,
    exact_mode: int | None = None,
    allowed_leaf_owners: frozenset[tuple[int, int]] = frozenset({(0, 0)}),
    missing_ok: bool = False,
) -> tuple[int, os.stat_result] | None:
    if (
        not path.is_absolute()
        or path != Path(os.path.abspath(os.fspath(path)))
        or ".." in path.parts
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{label} directory path is not canonical"
        )
    descriptor = -1
    current = Path("/")
    try:
        descriptor = os.open("/", _directory_flags())
        root_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != 0
            or root_metadata.st_gid != 0
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} root ancestor is unsafe"
            )
        for index, component in enumerate(path.parts[1:]):
            try:
                child = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if missing_ok:
                    os.close(descriptor)
                    return None
                raise
            os.close(descriptor)
            descriptor = child
            current /= component
            metadata = os.fstat(descriptor)
            is_leaf = index == len(path.parts[1:]) - 1
            owners = (
                allowed_leaf_owners if is_leaf else frozenset({(0, 0)})
            )
            writable_system_directory = current in {
                Path("/tmp"),
                Path("/var/tmp"),
            }
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_uid, metadata.st_gid) not in owners
                or (
                    stat.S_IMODE(metadata.st_mode) & 0o022
                    and not writable_system_directory
                )
                or (
                    is_leaf
                    and exact_mode is not None
                    and stat.S_IMODE(metadata.st_mode) != exact_mode
                )
            ):
                raise FrozenFinalRestoreWorkerError(
                    f"{label} directory ancestry is unsafe"
                )
        metadata = os.fstat(descriptor)
        if Path(os.path.realpath(path)) != path:
            raise FrozenFinalRestoreWorkerError(
                f"{label} directory ancestry is not lexical"
            )
        return descriptor, metadata
    except FrozenFinalRestoreWorkerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise FrozenFinalRestoreWorkerError(
            f"{label} directory ancestry is unavailable or unsafe"
        ) from exc


def _open_secure_parent(
    path: Path,
    *,
    label: str,
) -> tuple[int, str]:
    if path.name in {"", ".", ".."}:
        raise FrozenFinalRestoreWorkerError(
            f"{label} leaf name is invalid"
        )
    opened = _open_secure_directory(path.parent, label=f"{label} parent")
    if opened is None:
        raise FrozenFinalRestoreWorkerError(
            f"{label} parent is unavailable"
        )
    return opened[0], path.name


def _runtime_directory_specs(
    manifest: RoleManifest,
) -> tuple[tuple[Path, str, int | None, frozenset[tuple[int, int]]], ...]:
    root_only = frozenset({(0, 0)})
    postgres_owners = frozenset(
        {(0, 0), (POSTGRES_RUNTIME_UID, POSTGRES_RUNTIME_GID)}
    )
    return (
        (manifest.paths.project_root, "operation project root", None, root_only),
        (manifest.paths.release_root, "immutable release root", None, root_only),
        (
            manifest.paths.data_generation_root,
            "data generation root",
            0o700,
            root_only,
        ),
        (
            manifest.paths.restore_input_root,
            "restore-input root",
            0o700,
            root_only,
        ),
        (
            manifest.paths.secret_generation_root.parent,
            "secret generation base",
            0o700,
            root_only,
        ),
        (
            manifest.paths.secret_generation_root,
            "secret generation root",
            0o700,
            root_only,
        ),
        (
            manifest.paths.ca.parent,
            "secret generation TLS root",
            0o700,
            root_only,
        ),
        (manifest.paths.role_data_root, "role data root", 0o700, root_only),
        (
            manifest.paths.postgres,
            "PostgreSQL data root",
            0o700,
            postgres_owners,
        ),
        (manifest.paths.redis, "Redis data root", 0o700, root_only),
        (manifest.paths.uploads, "uploads data root", 0o700, root_only),
        (manifest.paths.audit, "audit data root", 0o700, root_only),
        (manifest.paths.journal, "restore journal", 0o700, root_only),
        (manifest.paths.evidence, "restore evidence", 0o700, root_only),
    )


def _capture_runtime_path_identities(
    manifest: RoleManifest,
    *,
    require_stores: bool,
) -> dict[str, tuple[int, int]]:
    identities: dict[str, tuple[int, int]] = {}
    stores = {
        manifest.paths.role_data_root,
        manifest.paths.postgres,
        manifest.paths.redis,
        manifest.paths.uploads,
        manifest.paths.audit,
    }
    publication_directories = {
        manifest.paths.journal,
        manifest.paths.evidence,
    }
    for path, label, exact_mode, owners in _runtime_directory_specs(manifest):
        opened = _open_secure_directory(
            path,
            label=label,
            exact_mode=exact_mode,
            allowed_leaf_owners=owners,
            missing_ok=(
                path in publication_directories
                or (not require_stores and path in stores)
            ),
        )
        if opened is None:
            continue
        descriptor, metadata = opened
        os.close(descriptor)
        identities[str(path)] = (metadata.st_dev, metadata.st_ino)
    return identities


def _recheck_runtime_path_identities(
    manifest: RoleManifest,
    identities: Mapping[str, tuple[int, int]],
    *,
    require_stores: bool,
) -> None:
    observed = _capture_runtime_path_identities(
        manifest,
        require_stores=require_stores,
    )
    for path, identity in identities.items():
        if observed.get(path) != identity:
            raise FrozenFinalRestoreWorkerError(
                "runtime directory identity changed across a safety boundary"
            )


def _read_root_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    mode: int = 0o600,
) -> bytes:
    descriptor = -1
    parent_descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(path, label=label)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or not 1 <= before.st_size <= maximum
            or (
                expected_bytes is not None
                and before.st_size != expected_bytes
            )
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} is unavailable or unsafe"
            )
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum:
                raise FrozenFinalRestoreWorkerError(
                    f"{label} exceeds its bound"
                )
            chunks.append(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} changed while being read"
            )
        if (
            expected_sha256 is not None
            and _sha256(payload) != expected_sha256
        ):
            raise FrozenFinalRestoreWorkerError(f"{label} digest differs")
        return payload
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _read_json(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], bytes, str]:
    payload = _read_root_file(
        path,
        label=label,
        maximum=maximum,
        expected_sha256=expected_sha256,
    )
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise FrozenFinalRestoreWorkerError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(document, dict) or payload.rstrip(b"\n") != _canonical_json(
        document
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{label} is not canonical JSON"
        )
    return document, payload, _sha256(payload.rstrip(b"\n"))


def _verify_root_file_identity(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    descriptor = -1
    parent_descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(path, label=label)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != expected_bytes
            or not 1 <= expected_bytes <= MAX_ARTIFACT_BYTES
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} is unavailable or unsafe"
            )
        digest = hashlib.sha256()
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > expected_bytes:
                raise FrozenFinalRestoreWorkerError(
                    f"{label} exceeded its bound"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise FrozenFinalRestoreWorkerError(
                f"{label} path changed while being verified"
            ) from exc
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            consumed != expected_bytes
            or digest.hexdigest() != expected_sha256
            or any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(path_after, field)
                for field in stable
            )
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} identity differs"
            )
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _verify_release_file(
    path: Path,
    *,
    expected_sha256: str,
) -> None:
    descriptor = -1
    parent_descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(
            path,
            label="immutable release worker",
        )
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
            or not 1 <= before.st_size <= MAX_JSON_BYTES
        ):
            raise FrozenFinalRestoreWorkerError(
                "immutable release worker is unavailable or unsafe"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            digest.hexdigest() != expected_sha256
            or any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(path_after, field)
                for field in stable
            )
        ):
            raise FrozenFinalRestoreWorkerError(
                "immutable release worker identity differs"
            )
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "immutable release worker is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _run_readonly(arguments: Sequence[str], *, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=SAFE_GIT_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "immutable release verification command is unavailable"
        ) from exc
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_OUTPUT_BYTES
        or len(result.stderr) > 1024 * 1024
    ):
        raise FrozenFinalRestoreWorkerError(
            "immutable release verification failed closed"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise FrozenFinalRestoreWorkerError(
            "immutable release verification returned invalid output"
        ) from exc


def _verify_immutable_release(
    *,
    release_root: Path,
    release_sha: str,
    release_tree_sha: str,
    worker_path: Path,
    worker_sha256: str,
) -> None:
    try:
        metadata = release_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "immutable release root is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or worker_path
        != release_root
        / "scripts"
        / "production_shadow_frozen_final_restore_worker.py"
    ):
        raise FrozenFinalRestoreWorkerError(
            "immutable release root or worker path differs"
        )
    if (
        _run_readonly([GIT, "-C", str(release_root), "rev-parse", "HEAD"])
        != release_sha
        or _run_readonly(
            [GIT, "-C", str(release_root), "rev-parse", "HEAD^{tree}"]
        )
        != release_tree_sha
        or _run_readonly(
            [GIT, "-C", str(release_root), "branch", "--show-current"]
        )
        != ""
        or _run_readonly(
            [
                GIT,
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ]
        )
        != ""
        or _run_readonly(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--exclude-standard",
            ]
        )
        != ""
        or _run_readonly(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
            ]
        )
        != ""
    ):
        raise FrozenFinalRestoreWorkerError(
            "immutable release is not detached, exact, and clean"
        )
    tracked = _run_readonly(
        [
            GIT,
            "-C",
            str(release_root),
            "ls-files",
            "--stage",
            "--",
            "scripts/production_shadow_frozen_final_restore_worker.py",
        ]
    )
    if (
        not re.fullmatch(
            r"100(644|755) [0-9a-f]{40} 0\t"
            r"scripts/production_shadow_frozen_final_restore_worker\.py",
            tracked,
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "immutable release worker is not an exact tracked file"
        )
    _verify_release_file(
        worker_path,
        expected_sha256=worker_sha256,
    )


def _project_identity(
    operation_id: str,
    restore_generation_sha256: str,
    role: str,
) -> tuple[str, str]:
    basis = {
        "schema": "production-shadow-frozen-final-project-v1",
        "operation_id": operation_id,
        "restore_generation_sha256": restore_generation_sha256,
        "role": role,
    }
    digest = _sha256(_canonical_json(basis))
    # The canonical Compose appends the role path.  Forty-eight hex characters
    # retain a 192-bit collision boundary while keeping its project name short.
    base = f"tb3f-{digest[:48]}"
    return base, f"{base}-{ROLE_PATHS[role]}"


def runtime_paths(
    operation_id: str,
    release_sha: str,
    restore_generation_sha256: str,
    role: str,
) -> RuntimePaths:
    _canonical_uuid(operation_id, label="operation id")
    if (
        SHA40_RE.fullmatch(release_sha) is None
        or SHA256_RE.fullmatch(restore_generation_sha256) is None
        or role not in ROLE_NAMES
    ):
        raise FrozenFinalRestoreWorkerError(
            "runtime path identity is invalid"
        )
    role_path = ROLE_PATHS[role]
    project_base, project_name = _project_identity(
        operation_id,
        restore_generation_sha256,
        role,
    )
    project_root = PROJECT_ROOT_PREFIX / operation_id
    data_generation_root = (
        DATA_ROOT_PREFIX
        / operation_id
        / "frozen-final-generations"
        / restore_generation_sha256
    )
    secret_generation_root = (
        SECRET_ROOT_PREFIX
        / operation_id
        / "frozen-final-generations"
        / restore_generation_sha256
        / role_path
    )
    role_data_root = data_generation_root / role_path
    restore_input_root = data_generation_root / "restore-input" / role_path
    return RuntimePaths(
        project_base=project_base,
        project_name=project_name,
        project_root=project_root,
        release_root=project_root / "releases" / release_sha,
        data_generation_root=data_generation_root,
        secret_generation_root=secret_generation_root,
        role_data_root=role_data_root,
        restore_input_root=restore_input_root,
        postgres=role_data_root / "postgres",
        redis=role_data_root / "redis",
        uploads=role_data_root / "uploads",
        audit=role_data_root / "audit",
        journal=secret_generation_root / "journal",
        evidence=secret_generation_root / "evidence",
        lock=secret_generation_root / "restore.lock",
        prepare_compose=(
            secret_generation_root / "docker-compose.prepare.yml"
        ),
        ca=secret_generation_root.parent / "tls" / "ca.crt",
    )


def target_migration_revision(release_root: Path) -> str:
    """Return the unique closed Alembic head from the immutable release."""
    try:
        graph = _load_migration_graph(release_root)
    except ProductionOperationError as exc:
        raise FrozenFinalRestoreWorkerError(
            "immutable release migration graph is invalid"
        ) from exc
    children = {
        parent
        for parents in graph.parents.values()
        for parent in parents
    }
    heads = set(graph.parents) - children
    if len(heads) != 1:
        raise FrozenFinalRestoreWorkerError(
            "immutable release migration graph lacks one closed head"
        )
    revision = next(iter(heads))
    try:
        reachable = _migration_ancestors(revision, graph)
    except ProductionOperationError as exc:
        raise FrozenFinalRestoreWorkerError(
            "immutable release migration graph is cyclic"
        ) from exc
    if (
        REVISION_RE.fullmatch(revision) is None
        or reachable != set(graph.parents)
    ):
        raise FrozenFinalRestoreWorkerError(
            "immutable release migration graph is not one closed lineage"
        )
    return revision


def _validate_source_database(value: Any) -> DatabaseExpectation:
    if not isinstance(value, dict) or set(value) != SOURCE_DATABASE_FIELDS:
        raise FrozenFinalRestoreWorkerError(
            "source database fields are not exact"
        )
    revision = value["alembic_revision"]
    algorithm = value["fingerprint_algorithm"]
    fingerprint = value["database_fingerprint_sha256"]
    if (
        not isinstance(revision, str)
        or REVISION_RE.fullmatch(revision) is None
        or algorithm
        != "pg-copy-jsonl-sha256-canonical-session-v1"
    ):
        raise FrozenFinalRestoreWorkerError(
            "source database revision or algorithm is invalid"
        )
    return DatabaseExpectation(
        alembic_revision=revision,
        fingerprint_algorithm=algorithm,
        database_fingerprint_sha256=_nonzero_sha256(
            fingerprint,
            label="source database fingerprint",
        ),
        row_count=_bounded_int(
            value["row_count"],
            minimum=0,
            maximum=10**12,
            label="source database row count",
        ),
        table_count=_bounded_int(
            value["table_count"],
            minimum=1,
            maximum=100_000,
            label="source database table count",
        ),
    )


def load_restore_set(
    path: Path,
    *,
    require_publication_namespace: bool = True,
) -> tuple[dict[str, Any], str]:
    document, payload, digest = _read_json(
        path,
        label="frozen-final restore set",
    )
    if (
        set(document) != RESTORE_SET.RESTORE_SET_FIELDS
        or document.get("schema") != RESTORE_SET.SCHEMA
        or document.get("status") != "sealed"
        or set(document.get("target_map", {})) != set(ROLE_NAMES)
        or document["target_map"] != RESTORE_SET.TARGET_MAP
        or set(document.get("sources", {}))
        != set(RESTORE_SET.SOURCE_ROLES)
    ):
        raise FrozenFinalRestoreWorkerError(
            "frozen-final restore set fields are not exact"
        )
    _canonical_uuid(document["campaign_id"], label="campaign id")
    _canonical_uuid(document["operation_id"], label="operation id")
    if (
        document["campaign_id"] == document["operation_id"]
        or SHA40_RE.fullmatch(str(document["release_sha"])) is None
        or SHA40_RE.fullmatch(str(document["release_tree_sha"])) is None
        or SHA40_RE.fullmatch(str(document["legacy_release_sha"])) is None
        or document["release_sha"] == document["legacy_release_sha"]
    ):
        raise FrozenFinalRestoreWorkerError(
            "frozen-final restore-set release identity is invalid"
        )
    for source_role, source in document["sources"].items():
        if (
            not isinstance(source, dict)
            or set(source) != RESTORE_SET.SOURCE_RESTORE_FIELDS
            or source["redis_restore_included"] is not False
            or set(source["artifacts"]) != set(ARTIFACT_KINDS)
            or source["live_lease_claim_sha256"]
            != document["snapshot_authorization_claim"]["claim_sha256"]
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{source_role} restore source closure is invalid"
            )
        _validate_source_database(source["source_database"])
        for kind, row in source["artifacts"].items():
            if (
                not isinstance(row, dict)
                or set(row)
                != {"sha256", "bytes", "restored_tree_sha256"}
            ):
                raise FrozenFinalRestoreWorkerError(
                    f"{source_role} {kind} artifact fields are invalid"
                )
            _nonzero_sha256(row["sha256"], label=f"{kind} artifact")
            _bounded_int(
                row["bytes"],
                minimum=1,
                maximum=MAX_ARTIFACT_BYTES,
                label=f"{kind} artifact bytes",
            )
            tree = row["restored_tree_sha256"]
            if kind == "database-backup":
                if tree is not None:
                    raise FrozenFinalRestoreWorkerError(
                        "database artifact must not carry a tree digest"
                    )
            else:
                _nonzero_sha256(tree, label=f"{kind} restored tree")
    claim = document["snapshot_authorization_claim"]
    if (
        not isinstance(claim, dict)
        or set(claim)
        != RESTORE_SET.SNAPSHOT_AUTHORIZATION_CLAIM_OUTPUT_FIELDS
        or claim["copied_material_authoritative"] is not False
        or claim["claim_liveness_asserted"] is not False
        or claim["future_install_or_restore_authority_implied"] is not False
        or claim["fresh_live_authority_required_before_install_or_restore"]
        is not True
        or claim["owner_action"] != "capture-frozen-final-snapshots"
        or claim["claim_document_status"] != "active"
        or type(claim["claim_epoch"]) is not int
        or claim["claim_epoch"] < 1
        or not isinstance(claim["nonce"], str)
        or SHA256_RE.fullmatch(claim["nonce"]) is None
        or claim["nonce"] == ZERO_SHA256
    ):
        raise FrozenFinalRestoreWorkerError(
            "snapshot claim provenance contract is invalid"
        )
    nginx = document["nginx_freeze"]
    transport = document["webapp_ir_transport"]
    constraints = document["constraints"]
    if (
        not isinstance(nginx, dict)
        or set(nginx) != RESTORE_SET.NGINX_FREEZE_FIELDS
        or nginx["state"] != "legacy-frozen"
        or any(
            SHA256_RE.fullmatch(str(nginx[field])) is None
            for field in (
                "aggregate_sha256",
                "state_receipt_sha256",
                "global_generation_sha256",
                "journal_sha256",
                "journal_tail_sha256",
                "external_readback_sha256",
            )
        )
        or set(nginx["role_generation_sha256"])
        != {"bot_fi", "webapp_fi"}
        or any(
            SHA256_RE.fullmatch(str(value)) is None
            for value in nginx["role_generation_sha256"].values()
        )
        or set(nginx["role_bindings"]) != {"bot_fi", "webapp_fi"}
        or type(nginx["journal_sequence"]) is not int
        or nginx["journal_sequence"] < 1
        or not isinstance(transport, dict)
        or set(transport) != RESTORE_SET.IR_TRANSPORT_OUTPUT_FIELDS
        or transport["provider"] != "arvan-s3"
        or transport["private"] is not True
        or transport["versioned"] is not True
        or transport["encryption"] != "age"
        or transport["exact_version_readback_verified"] is not True
        or not isinstance(constraints, dict)
        or set(constraints) != RESTORE_SET.CONSTRAINT_FIELDS
        or constraints[
            "snapshot_authorization_claim_copy_is_not_live_authority"
        ]
        is not True
        or constraints["snapshot_authorization_claim_liveness_asserted"]
        is not False
        or constraints["future_install_or_restore_authority_implied"]
        is not False
        or transport["plaintext_restore_input_set_sha256"]
        != document["sources"]["webapp_fi"]["restore_input_sha256"]
        or any(
            source["freeze_generation_sha256"]
            != nginx["global_generation_sha256"]
            for source in document["sources"].values()
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore-set freeze, transport, or authority closure differs"
        )
    for source_role, source in document["sources"].items():
        restore_input = {
            "source_snapshot_manifest_sha256": source[
                "source_snapshot_manifest_sha256"
            ],
            "source_snapshot_binding_sha256": source[
                "source_snapshot_binding_sha256"
            ],
            "freeze_evidence_sha256": source[
                "freeze_evidence_sha256"
            ],
            "live_lease_claim_sha256": source[
                "live_lease_claim_sha256"
            ],
            "source_identity_sha256": source["source_identity_sha256"],
            "artifacts": source["artifacts"],
            "source_database": source["source_database"],
        }
        for field in (
            "source_snapshot_manifest_sha256",
            "source_snapshot_binding_sha256",
            "freeze_evidence_sha256",
            "live_lease_claim_sha256",
            "source_identity_sha256",
            "restore_input_sha256",
            "freeze_generation_sha256",
            "restore_drill_sha256",
            "redis_rollback_metadata_sha256",
        ):
            _nonzero_sha256(
                source[field],
                label=f"{source_role} {field}",
            )
        if source["restore_input_sha256"] != _sha256(
            _canonical_json(restore_input)
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{source_role} restore-input digest differs"
            )
    generation_basis = {
        "schema": "production-shadow-frozen-final-restore-generation-v1",
        "operation_id": document["operation_id"],
        "release_sha": document["release_sha"],
        "release_tree_sha": document["release_tree_sha"],
        "controller_manifest_sha256": document[
            "controller_manifest_sha256"
        ],
        "approval_sha256": document["approval_sha256"],
        "target_map": document["target_map"],
        "sources": document["sources"],
        "nginx_freeze": nginx,
        "snapshot_authorization_claim": claim,
        "webapp_ir_transport": transport,
    }
    if document["restore_generation_sha256"] != _sha256(
        _canonical_json(generation_basis)
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore generation digest differs"
        )
    postgres_set = {
        target: {
            "source_role": row["source_role"],
            "artifact": document["sources"][row["source_role"]][
                "artifacts"
            ]["database-backup"],
            "source_database": document["sources"][row["source_role"]][
                "source_database"
            ],
        }
        for target, row in document["target_map"].items()
    }
    file_set = {
        target: {
            "source_role": row["source_role"],
            "uploads-archive": document["sources"][row["source_role"]][
                "artifacts"
            ]["uploads-archive"],
            "audit-archive": document["sources"][row["source_role"]][
                "artifacts"
            ]["audit-archive"],
        }
        for target, row in document["target_map"].items()
    }
    if (
        document["postgres_snapshot_set_sha256"]
        != _sha256(_canonical_json(postgres_set))
        or document["reviewed_file_snapshot_set_sha256"]
        != _sha256(_canonical_json(file_set))
        or document["constraints"]["legacy_redis_restore_included"]
        is not False
        or document["constraints"][
            "fresh_live_authority_required_before_install_or_restore"
        ]
        is not True
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore-set data closure or constraints differ"
        )
    if _sha256(payload.rstrip(b"\n")) != digest:
        raise FrozenFinalRestoreWorkerError(
            "restore-set canonical document digest differs"
        )
    if require_publication_namespace and not (
        path.parent.name == digest
        and path.name == RESTORE_SET.OUTPUT_FILENAME
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore-set digest namespace differs"
        )
    return document, digest


def _load_controller_manifest(
    path: Path,
    expected_sha256: str,
) -> Mapping[str, Any]:
    try:
        document, digest = read_root_only_manifest(path)
    except CutoverContractError as exc:
        raise FrozenFinalRestoreWorkerError(
            "controller manifest is invalid"
        ) from exc
    if digest != expected_sha256:
        raise FrozenFinalRestoreWorkerError(
            "controller manifest digest differs"
        )
    return document


def _validate_installer_receipt(
    path: Path,
    expected_sha256: str,
    *,
    role_document: Mapping[str, Any],
) -> None:
    receipt, _payload, digest = _read_json(
        path,
        label="frozen-final installer receipt",
        expected_sha256=expected_sha256,
    )
    if (
        digest != expected_sha256
        or set(receipt) != INSTALLER_RECEIPT_FIELDS
        or receipt["schema"] != INSTALLER_RECEIPT_SCHEMA
        or receipt["status"] != "installed"
        or any(
            receipt[field] != role_document[field]
            for field in (
                "campaign_id",
                "operation_id",
                "role",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "restore_set_sha256",
                "restore_generation_sha256",
                "source_role",
                "target_transport",
                "app_image_id",
                "app_image_content_identity",
                "target_migration_revision",
                "data_generation_root",
                "secret_generation_root",
            )
        )
        or receipt["redis_restore_bytes"] != 0
        or receipt["current_mutated"] is not False
        or receipt["legacy_mutated"] is not False
        or receipt["object_storage_mutated"] is not False
        or not isinstance(receipt["installed_files"], dict)
        or set(receipt["installed_files"]) != INSTALLER_FILE_NAMES
    ):
        raise FrozenFinalRestoreWorkerError(
            "frozen-final installer receipt binding differs"
        )
    expected_file_pairs = {
        "controller-manifest": (
            role_document["controller_manifest_path"],
            role_document["controller_manifest_sha256"],
        ),
        "restore-set": (
            role_document["restore_set_path"],
            role_document["restore_set_sha256"],
        ),
        "canonical-compose": (
            role_document["canonical_compose_path"],
            role_document["canonical_compose_sha256"],
        ),
        "role-compose": (
            role_document["role_compose_path"],
            role_document["role_compose_sha256"],
        ),
        "prepare-compose": (
            role_document["prepare_compose_path"],
            role_document["prepare_compose_sha256"],
        ),
        "ca": (
            role_document["ca_path"],
            role_document["ca_sha256"],
        ),
        "environment": (
            role_document["environment_path"],
            role_document["environment_sha256"],
        ),
        "worker": (
            role_document["worker_path"],
            role_document["worker_sha256"],
        ),
        **{
            kind: (
                role_document["artifacts"][kind]["path"],
                role_document["artifacts"][kind]["sha256"],
            )
            for kind in ARTIFACT_KINDS
        },
    }
    try:
        expected_files = {
            name: (
                path,
                digest,
                (
                    role_document["artifacts"][name]["bytes"]
                    if name in ARTIFACT_KINDS
                    else Path(path).stat(follow_symlinks=False).st_size
                ),
            )
            for name, (path, digest) in expected_file_pairs.items()
        }
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "installer receipt file inventory is unavailable"
        ) from exc
    for name, expected in expected_files.items():
        row = receipt["installed_files"][name]
        if (
            not isinstance(row, dict)
            or set(row) != INSTALLER_FILE_FIELDS
            or (
                row["path"],
                row["sha256"],
                row["bytes"],
            )
            != expected
        ):
            raise FrozenFinalRestoreWorkerError(
                f"installer receipt {name} binding differs"
            )


def load_role_manifest(path: Path) -> RoleManifest:
    document, _payload, digest = _read_json(
        path,
        label="frozen-final role manifest",
    )
    if (
        set(document) != ROLE_MANIFEST_FIELDS
        or document.get("schema") != ROLE_MANIFEST_SCHEMA
        or document.get("status") != "installed"
        or document.get("role") not in ROLE_NAMES
    ):
        raise FrozenFinalRestoreWorkerError(
            "frozen-final role manifest fields are not exact"
        )
    campaign_id = _canonical_uuid(
        document["campaign_id"],
        label="campaign id",
    )
    operation_id = _canonical_uuid(
        document["operation_id"],
        label="operation id",
    )
    role = document["role"]
    release_sha = document["release_sha"]
    release_tree_sha = document["release_tree_sha"]
    generation = _nonzero_sha256(
        document["restore_generation_sha256"],
        label="restore generation",
    )
    if (
        campaign_id == operation_id
        or not isinstance(release_sha, str)
        or SHA40_RE.fullmatch(release_sha) is None
        or not isinstance(release_tree_sha, str)
        or SHA40_RE.fullmatch(release_tree_sha) is None
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest release identity is invalid"
        )
    paths = runtime_paths(operation_id, release_sha, generation, role)
    if (
        path
        != paths.secret_generation_root / "restore-role-manifest.json"
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest path is not generation-derived"
        )
    expected_paths = {
        "release_root": paths.release_root,
        "data_generation_root": paths.data_generation_root,
        "secret_generation_root": paths.secret_generation_root,
    }
    if any(
        _absolute_path(document[field], label=field) != expected
        for field, expected in expected_paths.items()
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest runtime root differs"
        )
    if (
        document["project_base"] != paths.project_base
        or document["project_name"] != paths.project_name
        or PROJECT_RE.fullmatch(str(document["project_name"])) is None
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest project identity differs"
        )
    restore_set_path = _absolute_path(
        document["restore_set_path"],
        label="restore set",
    )
    if (
        restore_set_path
        != paths.secret_generation_root / "frozen-final-restore-set.json"
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore-set installation path is not generation-derived"
        )
    restore_set, restore_set_sha256 = load_restore_set(
        restore_set_path,
        require_publication_namespace=False,
    )
    if (
        restore_set_sha256 != document["restore_set_sha256"]
        or restore_set["campaign_id"] != campaign_id
        or restore_set["operation_id"] != operation_id
        or restore_set["release_sha"] != release_sha
        or restore_set["release_tree_sha"] != release_tree_sha
        or restore_set["restore_generation_sha256"] != generation
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest restore-set binding differs"
        )
    mapping = restore_set["target_map"][role]
    source_role = mapping["source_role"]
    if (
        document["source_role"] != source_role
        or document["target_transport"] != mapping["transport"]
        or document["target_transport"] != ROLE_TRANSPORTS[role]
        or document["legacy_frozen_receipt_sha256"]
        != restore_set["nginx_freeze"]["state_receipt_sha256"]
        or document["snapshot_authorization_claim_sha256"]
        != restore_set["snapshot_authorization_claim"]["claim_sha256"]
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest source or freeze binding differs"
        )
    controller_manifest_path = _absolute_path(
        document["controller_manifest_path"],
        label="controller manifest",
    )
    if (
        controller_manifest_path
        != paths.secret_generation_root / "controller-manifest.json"
    ):
        raise FrozenFinalRestoreWorkerError(
            "controller manifest path is not generation-derived"
        )
    controller = _load_controller_manifest(
        controller_manifest_path,
        document["controller_manifest_sha256"],
    )
    if (
        controller["campaign_id"] != campaign_id
        or controller["operation_id"] != operation_id
        or controller["release_sha"] != release_sha
        or controller["release_tree_sha"] != release_tree_sha
        or document["controller_manifest_sha256"]
        != restore_set["controller_manifest_sha256"]
        or document["canonical_compose_sha256"]
        != controller["artifacts"]["shadow_compose_sha256"]
        or document["postgres_image_id"]
        != controller["artifacts"]["role_runtime_image_ids"][role][
            "postgres"
        ]
        or document["app_image_id"]
        != controller["artifacts"]["role_runtime_image_ids"][role]["app"]
        or document["postgres_runtime_uid"]
        != controller["artifacts"]["postgres_runtime_uid"]
        or document["postgres_runtime_gid"]
        != controller["artifacts"]["postgres_runtime_gid"]
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest controller binding differs"
        )
    postgres_image_id = document["postgres_image_id"]
    if (
        not isinstance(postgres_image_id, str)
        or IMAGE_ID_RE.fullmatch(postgres_image_id) is None
        or postgres_image_id == f"sha256:{ZERO_SHA256}"
        or document["postgres_runtime_uid"] != POSTGRES_RUNTIME_UID
        or document["postgres_runtime_gid"] != POSTGRES_RUNTIME_GID
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest PostgreSQL runtime identity is invalid"
        )
    app_image_id = document["app_image_id"]
    if (
        not isinstance(app_image_id, str)
        or IMAGE_ID_RE.fullmatch(app_image_id) is None
        or app_image_id == f"sha256:{ZERO_SHA256}"
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest application runtime identity is invalid"
        )
    postgres_binding = controller["artifacts"]["image_artifacts"][
        "postgres"
    ]
    postgres_content_identity = document[
        "postgres_image_content_identity"
    ]
    if (
        postgres_content_identity != postgres_binding["content_identity"]
        or not isinstance(postgres_content_identity, str)
        or IMAGE_ID_RE.fullmatch(postgres_content_identity) is None
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest PostgreSQL content identity differs"
        )
    app_binding = controller["artifacts"]["image_artifacts"]["app"]
    app_content_identity = document["app_image_content_identity"]
    if (
        app_content_identity != app_binding["content_identity"]
        or not isinstance(app_content_identity, str)
        or IMAGE_ID_RE.fullmatch(app_content_identity) is None
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest application content identity differs"
        )
    target_revision = document["target_migration_revision"]
    if (
        not isinstance(target_revision, str)
        or REVISION_RE.fullmatch(target_revision) is None
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest target migration revision is invalid"
        )
    canonical_compose_path = _absolute_path(
        document["canonical_compose_path"],
        label="canonical Compose",
    )
    role_compose_path = _absolute_path(
        document["role_compose_path"],
        label="role Compose",
    )
    prepare_compose_path = _absolute_path(
        document["prepare_compose_path"],
        label="prepare Compose",
    )
    ca_path = _absolute_path(document["ca_path"], label="prepare CA")
    environment_path = _absolute_path(
        document["environment_path"],
        label="role environment",
    )
    worker_path = _absolute_path(document["worker_path"], label="worker")
    expected_secret_files = {
        canonical_compose_path: (
            paths.secret_generation_root / "canonical-compose.yml"
        ),
        role_compose_path: (
            paths.secret_generation_root / "docker-compose.restore.yml"
        ),
        prepare_compose_path: paths.prepare_compose,
        ca_path: paths.ca,
        environment_path: (
            paths.secret_generation_root / "runtime.env.role"
        ),
    }
    if any(actual != expected for actual, expected in expected_secret_files.items()):
        raise FrozenFinalRestoreWorkerError(
            "role manifest installed file path differs"
        )
    for installed, field in (
        (canonical_compose_path, "canonical_compose_sha256"),
        (role_compose_path, "role_compose_sha256"),
        (prepare_compose_path, "prepare_compose_sha256"),
        (ca_path, "ca_sha256"),
        (environment_path, "environment_sha256"),
    ):
        _read_root_file(
            installed,
            label=field,
            maximum=MAX_JSON_BYTES,
            expected_sha256=document[field],
        )
    worker_sha256 = _nonzero_sha256(
        document["worker_sha256"],
        label="immutable release worker",
    )
    _verify_immutable_release(
        release_root=paths.release_root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        worker_path=worker_path,
        worker_sha256=worker_sha256,
    )
    if target_revision != target_migration_revision(paths.release_root):
        raise FrozenFinalRestoreWorkerError(
            "role manifest target migration revision differs"
        )
    if RUNNING_WORKER_PATH != worker_path:
        raise FrozenFinalRestoreWorkerError(
            "running worker is not the installed generation-bound worker"
        )
    artifacts: dict[str, ArtifactBinding] = {}
    source = restore_set["sources"][source_role]
    if not isinstance(document["artifacts"], dict) or set(
        document["artifacts"]
    ) != set(ARTIFACT_KINDS):
        raise FrozenFinalRestoreWorkerError(
            "role manifest artifact inventory is invalid"
        )
    filenames = {
        "database-backup": "database.dump",
        "uploads-archive": "uploads.tar.gz",
        "audit-archive": "audit.tar.gz",
    }
    for kind in ARTIFACT_KINDS:
        row = document["artifacts"][kind]
        source_row = source["artifacts"][kind]
        expected_path = paths.restore_input_root / filenames[kind]
        if (
            not isinstance(row, dict)
            or set(row) != ARTIFACT_FIELDS
            or _absolute_path(row["path"], label=kind) != expected_path
            or {
                "sha256": row["sha256"],
                "bytes": row["bytes"],
                "restored_tree_sha256": row["restored_tree_sha256"],
            }
            != source_row
        ):
            raise FrozenFinalRestoreWorkerError(
                f"role manifest {kind} binding differs"
            )
        binding = ArtifactBinding(
            path=expected_path,
            sha256=_nonzero_sha256(row["sha256"], label=kind),
            bytes=_bounded_int(
                row["bytes"],
                minimum=1,
                maximum=MAX_ARTIFACT_BYTES,
                label=f"{kind} bytes",
            ),
            restored_tree_sha256=row["restored_tree_sha256"],
        )
        _verify_root_file_identity(
            binding.path,
            label=kind,
            expected_sha256=binding.sha256,
            expected_bytes=binding.bytes,
        )
        artifacts[kind] = binding
    source_database = _validate_source_database(document["source_database"])
    if document["source_database"] != source["source_database"]:
        raise FrozenFinalRestoreWorkerError(
            "role manifest source database differs"
        )
    constraints = document["constraints"]
    if (
        not isinstance(constraints, dict)
        or set(constraints) != CONSTRAINT_FIELDS
        or any(value is not True for value in constraints.values())
    ):
        raise FrozenFinalRestoreWorkerError(
            "role manifest constraints are not fail-closed"
        )
    receipt_path = _absolute_path(
        document["installer_receipt_path"],
        label="installer receipt",
    )
    if receipt_path != paths.secret_generation_root / "installer-receipt.json":
        raise FrozenFinalRestoreWorkerError(
            "installer receipt path is not generation-derived"
        )
    installer_receipt_sha256 = _nonzero_sha256(
        document["installer_receipt_sha256"],
        label="installer receipt",
    )
    _validate_installer_receipt(
        receipt_path,
        installer_receipt_sha256,
        role_document=document,
    )
    manifest = RoleManifest(
        document=document,
        canonical_sha256=digest,
        operation_id=operation_id,
        role=role,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        restore_set_sha256=restore_set_sha256,
        restore_generation_sha256=generation,
        source_role=source_role,
        controller_manifest_sha256=document[
            "controller_manifest_sha256"
        ],
        installer_receipt_sha256=installer_receipt_sha256,
        postgres_image_id=postgres_image_id,
        postgres_image_content_identity=postgres_content_identity,
        app_image_id=app_image_id,
        app_image_content_identity=app_content_identity,
        target_migration_revision=target_revision,
        artifacts=artifacts,
        source_database=source_database,
        paths=paths,
        controller_manifest_path=controller_manifest_path,
        restore_set_path=restore_set_path,
        canonical_compose_path=canonical_compose_path,
        role_compose_path=role_compose_path,
        prepare_compose_path=prepare_compose_path,
        ca_path=ca_path,
        environment_path=environment_path,
        worker_path=worker_path,
    )
    _verify_prepare_inputs(manifest)
    return manifest


def load_live_lease(
    *,
    manifest: RoleManifest,
    claim_path: Path,
    claim_sha256: str,
    claim_epoch: int,
    receipt_path: Path,
) -> LeaseBinding:
    claim_sha256 = _nonzero_sha256(
        claim_sha256,
        label="fresh live-lease claim",
    )
    document, _payload, observed_sha256 = _read_json(
        claim_path,
        label="fresh live-lease claim",
        expected_sha256=claim_sha256,
    )
    restore_set, _ = load_restore_set(
        manifest.restore_set_path,
        require_publication_namespace=False,
    )
    historical = restore_set["snapshot_authorization_claim"]
    receipt_sha256 = restore_set["nginx_freeze"]["state_receipt_sha256"]
    controller_root = (
        SECRET_ROOT_PREFIX
        / manifest.operation_id
        / "nginx-coordinator"
    )
    expected_claim_path = (
        controller_root
        / "live-leases"
        / "claims"
        / f"{claim_sha256}.json"
    )
    expected_receipt_path = (
        controller_root
        / "receipts"
        / f"legacy-frozen-{receipt_sha256}.json"
    )
    _read_root_file(
        receipt_path,
        label="legacy-frozen receipt",
        maximum=MAX_JSON_BYTES,
        expected_sha256=receipt_sha256,
    )
    if (
        observed_sha256 != claim_sha256
        or claim_path != expected_claim_path
        or receipt_path != expected_receipt_path
        or set(document) != RESTORE_SET.LIVE_LEASE_FIELDS
        or document["schema"] != LIVE_LEASE_CLAIM_SCHEMA
        or document["status"] != "active"
        or document["owner_action"] != LIVE_LEASE_OWNER_ACTION
        or document["operation_id"] != manifest.operation_id
        or document["release_sha"] != manifest.release_sha
        or document["release_tree_sha"] != manifest.release_tree_sha
        or document["receipt_state"] != "legacy-frozen"
        or document["legacy_frozen_receipt_sha256"] != receipt_sha256
        or Path(document["legacy_frozen_receipt_path"]) != receipt_path
        or document["receipt_global_generation_sha256"]
        != restore_set["nginx_freeze"]["global_generation_sha256"]
        or document["receipt_role_generation_sha256"]
        != restore_set["nginx_freeze"]["role_generation_sha256"]
        or document["receipt_role_bindings"]
        != restore_set["nginx_freeze"]["role_bindings"]
        or document["receipt_journal_sha256"]
        != restore_set["nginx_freeze"]["journal_sha256"]
        or document["receipt_journal_sequence"]
        != restore_set["nginx_freeze"]["journal_sequence"]
        or document["receipt_journal_tail_sha256"]
        != restore_set["nginx_freeze"]["journal_tail_sha256"]
        or document["aggregate_sha256"]
        != restore_set["nginx_freeze"]["aggregate_sha256"]
        or document["controller_authoritative"] is not True
        or document["remote_copy_authoritative"] is not False
        or document["automatic_expiry_allowed"] is not False
        or document["reconciliation_required_after_crash"] is not True
        or type(document["claim_epoch"]) is not int
        or document["claim_epoch"] != claim_epoch
        or claim_epoch <= historical["claim_epoch"]
        or claim_sha256 == historical["claim_sha256"]
        or not isinstance(document["nonce"], str)
        or SHA256_RE.fullmatch(document["nonce"]) is None
        or document["nonce"] == ZERO_SHA256
    ):
        raise FrozenFinalRestoreWorkerError(
            "fresh live-lease claim binding differs"
        )
    return LeaseBinding(
        document=document,
        path=claim_path,
        sha256=claim_sha256,
        epoch=claim_epoch,
        nonce=document["nonce"],
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
    )


def _restore_compose_document(
    manifest: RoleManifest,
) -> Mapping[str, Any]:
    raw = _read_root_file(
        manifest.canonical_compose_path,
        label="canonical production Compose",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["canonical_compose_sha256"],
    )
    try:
        canonical = yaml.safe_load(raw.decode("utf-8"))
        rendered = render_role_compose(
            canonical,
            role=ROLE_PATHS[manifest.role],
            scope="prepare",
        )
    except (UnicodeError, yaml.YAMLError, ProductionShadowRoleError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "canonical restore Compose cannot be derived"
        ) from exc
    service_prefix = f"{manifest.role}_"
    names = {
        f"{service_prefix}db",
        f"{service_prefix}restore_tool",
    }
    rendered["services"] = {
        name: value
        for name, value in rendered["services"].items()
        if name in names
    }
    role_network = manifest.role
    rendered["networks"] = {
        role_network: rendered["networks"][role_network]
    }
    rendered.pop("volumes", None)
    return rendered


def _compose_environment(
    manifest: RoleManifest,
) -> tuple[dict[str, str], dict[str, str]]:
    payload = _read_root_file(
        manifest.environment_path,
        label="frozen-final role environment",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["environment_sha256"],
    )
    try:
        original = parse_env_values(payload.decode("ascii"))
    except (UnicodeError, ProductionShadowRoleError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "frozen-final role environment is invalid"
        ) from exc
    role_prefix = ROLE_PREFIXES[manifest.role]
    required_role = {
        f"{role_prefix}_POSTGRES_USER",
        f"{role_prefix}_POSTGRES_PASSWORD",
        f"{role_prefix}_POSTGRES_DB",
    }
    if required_role - set(original):
        raise FrozenFinalRestoreWorkerError(
            "frozen-final role environment lacks database credentials"
        )
    generation_secret_base = manifest.paths.secret_generation_root.parent
    cgroup_digest = _sha256(
        _canonical_json(
            {
                "operation_id": manifest.operation_id,
                "restore_generation_sha256": (
                    manifest.restore_generation_sha256
                ),
                "role": manifest.role,
            }
        )
    )
    overrides = {
        "PRODUCTION_SHADOW_PROJECT": manifest.paths.project_base,
        "PRODUCTION_SHADOW_OPERATION_ID": manifest.operation_id,
        "PRODUCTION_SHADOW_PROJECT_ROOT": str(
            manifest.paths.project_root
        ),
        "PRODUCTION_SHADOW_RELEASE_ROOT": str(
            manifest.paths.release_root
        ),
        "PRODUCTION_SHADOW_DATA_ROOT": str(
            manifest.paths.data_generation_root
        ),
        "PRODUCTION_SHADOW_SECRET_ROOT": str(generation_secret_base),
        "PRODUCTION_SHADOW_CGROUP_PARENT": (
            "/trading-bot-production-shadow/frozen-final-"
            f"{cgroup_digest[:32]}"
        ),
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": (
            manifest.postgres_image_id
        ),
    }
    command_env = {**SAFE_ENV, **original, **overrides}
    return command_env, overrides


def _compose_base(manifest: RoleManifest) -> list[str]:
    return [
        DOCKER,
        "compose",
        "--project-name",
        manifest.paths.project_name,
        "--env-file",
        str(manifest.environment_path),
        "--file",
        str(manifest.role_compose_path),
    ]


def _restore_compose_base(manifest: RoleManifest) -> list[str]:
    return [
        *_compose_base(manifest),
        "--profile",
        f"{ROLE_PATHS[manifest.role]}-restore",
    ]


def _load_json_output(raw: str, *, label: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_strict_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FrozenFinalRestoreWorkerError(
            f"{label} returned invalid JSON"
        ) from exc


def _verify_role_compose(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> Mapping[str, Any]:
    payload = _read_root_file(
        manifest.role_compose_path,
        label="frozen-final role Compose",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["role_compose_sha256"],
    )
    try:
        role_compose = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "frozen-final role Compose is invalid"
        ) from exc
    expected = _restore_compose_document(manifest)
    if role_compose != expected:
        raise FrozenFinalRestoreWorkerError(
            "role Compose is not the exact canonical restore-only projection"
        )
    command_env, overrides = _compose_environment(manifest)
    rendered = _load_json_output(
        runner.run(
            [
                *_restore_compose_base(manifest),
                "config",
                "--format",
                "json",
            ],
            timeout=60,
            env=command_env,
        ),
        label="rendered frozen-final Compose",
    )
    services = rendered.get("services") if isinstance(rendered, dict) else None
    expected_services = {
        f"{manifest.role}_db",
        f"{manifest.role}_restore_tool",
    }
    role_path = ROLE_PATHS[manifest.role]
    expected_mounts = {
        f"{manifest.role}_db": {
            (
                str(manifest.paths.postgres),
                "/var/lib/postgresql/data",
                False,
            )
        },
        f"{manifest.role}_restore_tool": {
            (
                str(manifest.paths.restore_input_root),
                "/run/restore-input",
                True,
            ),
            (
                str(manifest.paths.uploads),
                "/run/restore-target/uploads",
                False,
            ),
            (
                str(manifest.paths.audit),
                "/run/restore-target/audit",
                False,
            ),
        },
    }

    def mount_tuple(value: Any) -> tuple[str, str, bool]:
        if not isinstance(value, dict):
            raise FrozenFinalRestoreWorkerError(
                "rendered Compose mount is not long syntax"
            )
        return (
            str(value.get("source")),
            str(value.get("target")),
            value.get("read_only") is True,
        )

    if (
        not isinstance(services, dict)
        or set(services) != expected_services
        or rendered.get("name") != manifest.paths.project_name
        or rendered.get("volumes") not in (None, {})
        or not isinstance(rendered.get("networks"), dict)
        or set(rendered["networks"]) != {manifest.role}
    ):
        raise FrozenFinalRestoreWorkerError(
            "rendered Compose project or service closure differs"
        )
    for service_name, service in services.items():
        if (
            not isinstance(service, dict)
            or service.get("image") != manifest.postgres_image_id
            or "build" in service
            or "ports" in service
            or "container_name" in service
            or service.get("network_mode") == "host"
            or set(service.get("networks", {})) != {manifest.role}
            or {
                mount_tuple(value)
                for value in service.get("volumes", [])
            }
            != expected_mounts[service_name]
            or service.get("cgroup_parent")
            != overrides["PRODUCTION_SHADOW_CGROUP_PARENT"]
        ):
            raise FrozenFinalRestoreWorkerError(
                f"rendered {service_name} escaped the final generation"
            )
    rendered_text = json.dumps(rendered, sort_keys=True)
    for name in (
        "PRODUCTION_SHADOW_PROJECT",
        "PRODUCTION_SHADOW_DATA_ROOT",
        "PRODUCTION_SHADOW_SECRET_ROOT",
        "PRODUCTION_SHADOW_CGROUP_PARENT",
    ):
        prior = parse_env_values(
            _read_root_file(
                manifest.environment_path,
                label="frozen-final role environment",
                maximum=MAX_JSON_BYTES,
                expected_sha256=manifest.document["environment_sha256"],
            ).decode("ascii")
        ).get(name)
        if (
            prior
            and prior != overrides[name]
            and prior in rendered_text
        ):
            raise FrozenFinalRestoreWorkerError(
                f"rendered Compose retained rehearsal {name}"
            )
    required_names = referenced_environment_names(expected)
    if not required_names.issubset(command_env):
        raise FrozenFinalRestoreWorkerError(
            "rendered Compose environment closure is incomplete"
        )
    if f"/{role_path}/{role_path}/" in rendered_text:
        raise FrozenFinalRestoreWorkerError(
            "rendered Compose duplicated the role data path"
        )
    return {
        "project_name": manifest.paths.project_name,
        "data_generation_root": str(
            manifest.paths.data_generation_root
        ),
        "secret_generation_root": str(
            manifest.paths.secret_generation_root
        ),
        "cgroup_parent": overrides[
            "PRODUCTION_SHADOW_CGROUP_PARENT"
        ],
        "services": sorted(expected_services),
        "network": manifest.role,
        "no_pull": True,
        "no_build": True,
        "no_app_services": True,
    }


def _verify_prepare_inputs(manifest: RoleManifest) -> Mapping[str, Any]:
    canonical_payload = _read_root_file(
        manifest.canonical_compose_path,
        label="canonical production Compose",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["canonical_compose_sha256"],
    )
    prepare_payload = _read_root_file(
        manifest.prepare_compose_path,
        label="frozen-final prepare Compose",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["prepare_compose_sha256"],
    )
    try:
        canonical = yaml.safe_load(canonical_payload.decode("utf-8"))
        expected = render_role_compose(
            canonical,
            role=ROLE_PATHS[manifest.role],
            scope="prepare",
        )
        expected["x-production-shadow-runtime-image-ids"] = dict(
            PREPARE.RUNTIME_IMAGE_COMPOSE_EXTENSION
        )
        expected_payload = canonical_role_compose_bytes(expected)
    except (
        UnicodeError,
        yaml.YAMLError,
        ProductionShadowRoleError,
    ) as exc:
        raise FrozenFinalRestoreWorkerError(
            "canonical prepare Compose cannot be derived"
        ) from exc
    if prepare_payload != expected_payload:
        raise FrozenFinalRestoreWorkerError(
            "prepare Compose is not the exact canonical role projection"
        )
    ca_payload = _read_root_file(
        manifest.ca_path,
        label="frozen-final prepare CA",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["ca_sha256"],
    )
    if (
        ca_payload.count(b"-----BEGIN CERTIFICATE-----") != 1
        or ca_payload.count(b"-----END CERTIFICATE-----") != 1
        or b"PRIVATE KEY" in ca_payload
    ):
        raise FrozenFinalRestoreWorkerError(
            "frozen-final prepare CA is invalid"
        )
    environment_payload = _read_root_file(
        manifest.environment_path,
        label="frozen-final role environment",
        maximum=MAX_JSON_BYTES,
        expected_sha256=manifest.document["environment_sha256"],
    )
    try:
        values = parse_env_values(environment_payload.decode("ascii"))
    except (UnicodeError, ProductionShadowRoleError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "frozen-final role environment is invalid"
        ) from exc
    expected_images = {
        "PRODUCTION_SHADOW_APP_IMAGE_ID": manifest.app_image_id,
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": (
            manifest.postgres_image_id
        ),
    }
    if any(values.get(key) != value for key, value in expected_images.items()):
        raise FrozenFinalRestoreWorkerError(
            "prepare environment image identity differs"
        )
    services = expected.get("services")
    if not isinstance(services, dict) or not services:
        raise FrozenFinalRestoreWorkerError(
            "prepare Compose service closure is empty"
        )
    return {
        "prepare_compose_sha256": manifest.document[
            "prepare_compose_sha256"
        ],
        "ca_sha256": manifest.document["ca_sha256"],
        "app_image_id": manifest.app_image_id,
        "app_image_content_identity": manifest.app_image_content_identity,
        "target_migration_revision": manifest.target_migration_revision,
        "prepare_service_count": len(services),
        "app_service_invoked": False,
    }


def _verify_image(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> None:
    command_env, _ = _compose_environment(manifest)
    for label, image_id in (
        ("PostgreSQL", manifest.postgres_image_id),
        ("application", manifest.app_image_id),
    ):
        observed = runner.run(
            [
                DOCKER,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                image_id,
            ],
            timeout=30,
            env=command_env,
        )
        if observed != image_id:
            raise FrozenFinalRestoreWorkerError(
                f"local {label} image identity differs"
            )


def _string_vector(value: Any, *, label: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
    ):
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    return tuple(value)


def _environment_map(value: Any, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        rows = value.items()
    elif isinstance(value, list):
        parsed: list[tuple[str, str]] = []
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
            parsed.append(tuple(item.split("=", 1)))
        rows = parsed
    else:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    for key, item in rows:
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            or not isinstance(item, str)
            or key in result
        ):
            raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
        result[key] = item
    return result


def _duration_nanoseconds(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    if isinstance(value, int):
        if value < 0:
            raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
        return value
    if not isinstance(value, str) or not value:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    if value.isdigit():
        return int(value)
    factors = {
        "ns": Decimal(1),
        "us": Decimal(1_000),
        "µs": Decimal(1_000),
        "ms": Decimal(1_000_000),
        "s": Decimal(1_000_000_000),
        "m": Decimal(60_000_000_000),
        "h": Decimal(3_600_000_000_000),
    }
    position = 0
    total = Decimal(0)
    pattern = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ns|us|µs|ms|s|m|h)")
    try:
        while position < len(value):
            match = pattern.match(value, position)
            if match is None:
                raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
            total += Decimal(match.group(1)) * factors[match.group(2)]
            position = match.end()
    except InvalidOperation as exc:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid") from exc
    if total != total.to_integral_value() or total < 0:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    return int(total)


def _memory_bytes(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    if isinstance(value, int):
        if value <= 0:
            raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
        return value
    if not isinstance(value, str):
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    match = re.fullmatch(
        r"([0-9]+)(b|k|kb|kib|m|mb|mib|g|gb|gib)?",
        value.lower(),
    )
    if match is None:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    factors = {
        None: 1,
        "b": 1,
        "k": 1024,
        "kb": 1000,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1000**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1000**3,
        "gib": 1024**3,
    }
    result = int(match.group(1)) * factors[match.group(2)]
    if result <= 0:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    return result


def _nano_cpus(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    try:
        result = Decimal(str(value)) * Decimal(1_000_000_000)
    except InvalidOperation as exc:
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid") from exc
    if result <= 0 or result != result.to_integral_value():
        raise FrozenFinalRestoreWorkerError(f"{label} is invalid")
    return int(result)


def _compose_healthcheck(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise FrozenFinalRestoreWorkerError(
            "rendered database healthcheck is invalid"
        )
    allowed = {
        "disable",
        "test",
        "interval",
        "timeout",
        "retries",
        "start_period",
        "start_interval",
    }
    if set(value) - allowed:
        raise FrozenFinalRestoreWorkerError(
            "rendered database healthcheck is invalid"
        )
    if value.get("disable") is True:
        test = ("NONE",)
    else:
        test = _string_vector(
            value.get("test"),
            label="rendered database healthcheck test",
        )
        if test is None or not test or test[0] not in {"CMD", "CMD-SHELL"}:
            raise FrozenFinalRestoreWorkerError(
                "rendered database healthcheck is invalid"
            )
        test = tuple(item.replace("$$", "$") for item in test)
    retries = value.get("retries", 0)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise FrozenFinalRestoreWorkerError(
            "rendered database healthcheck is invalid"
        )
    return {
        "Test": test,
        "Interval": _duration_nanoseconds(
            value.get("interval", 0),
            label="rendered database health interval",
        ),
        "Timeout": _duration_nanoseconds(
            value.get("timeout", 0),
            label="rendered database health timeout",
        ),
        "Retries": retries,
        "StartPeriod": _duration_nanoseconds(
            value.get("start_period", 0),
            label="rendered database health start period",
        ),
        "StartInterval": _duration_nanoseconds(
            value.get("start_interval", 0),
            label="rendered database health start interval",
        ),
    }


def _inspect_healthcheck(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "Test",
        "Interval",
        "Timeout",
        "Retries",
        "StartPeriod",
        "StartInterval",
    }:
        raise FrozenFinalRestoreWorkerError(
            "database container healthcheck is invalid"
        )
    test = _string_vector(
        value.get("Test"),
        label="database container healthcheck test",
    )
    if test is None or not test or test[0] not in {
        "NONE",
        "CMD",
        "CMD-SHELL",
    }:
        raise FrozenFinalRestoreWorkerError(
            "database container healthcheck is invalid"
        )
    values: dict[str, Any] = {"Test": test}
    for key in (
        "Interval",
        "Timeout",
        "Retries",
        "StartPeriod",
        "StartInterval",
    ):
        observed = value.get(key, 0)
        if isinstance(observed, bool) or not isinstance(observed, int):
            raise FrozenFinalRestoreWorkerError(
                "database container healthcheck is invalid"
            )
        values[key] = observed
    return values


def _service_runtime_contract(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    *,
    service_name: str,
    expected_restart: str,
) -> DatabaseRuntimeContract:
    _verify_role_compose(manifest, runner)
    command_env, _ = _compose_environment(manifest)
    rendered = _load_json_output(
        runner.run(
            [
                *_restore_compose_base(manifest),
                "config",
                "--format",
                "json",
            ],
            timeout=60,
            env=command_env,
        ),
        label="rendered database Compose",
    )
    services = rendered.get("services") if isinstance(rendered, dict) else None
    service = services.get(service_name) if isinstance(services, dict) else None
    if (
        not isinstance(service, dict)
        or service.get("image") != manifest.postgres_image_id
        or service.get("restart") != expected_restart
        or not isinstance(service.get("cgroup_parent"), str)
        or not isinstance(service.get("pids_limit"), int)
        or isinstance(service.get("pids_limit"), bool)
        or service["pids_limit"] <= 0
        or not isinstance(service.get("labels"), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in service["labels"].items()
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "rendered database runtime contract is invalid"
        )
    image_rows = _load_json_output(
        runner.run(
            [DOCKER, "image", "inspect", manifest.postgres_image_id],
            timeout=30,
            env=command_env,
        ),
        label="PostgreSQL image runtime inspection",
    )
    if (
        not isinstance(image_rows, list)
        or len(image_rows) != 1
        or not isinstance(image_rows[0], dict)
        or image_rows[0].get("Id") != manifest.postgres_image_id
        or not isinstance(image_rows[0].get("Config"), dict)
    ):
        raise FrozenFinalRestoreWorkerError(
            "PostgreSQL image runtime inspection differs"
        )
    image_config = image_rows[0]["Config"]
    image_command = _string_vector(
        image_config.get("Cmd"),
        label="PostgreSQL image command",
    )
    image_entrypoint = _string_vector(
        image_config.get("Entrypoint"),
        label="PostgreSQL image entrypoint",
    )
    command = (
        _string_vector(
            service.get("command"),
            label="rendered database command",
        )
        if service.get("command") is not None
        else image_command
    )
    entrypoint = (
        _string_vector(
            service.get("entrypoint"),
            label="rendered database entrypoint",
        )
        if service.get("entrypoint") is not None
        else image_entrypoint
    )
    user = service.get("user", image_config.get("User", ""))
    working_dir = service.get(
        "working_dir",
        image_config.get("WorkingDir", ""),
    )
    stop_signal = service.get(
        "stop_signal",
        image_config.get("StopSignal", ""),
    )
    if any(
        not isinstance(value, str)
        for value in (user, working_dir, stop_signal)
    ):
        raise FrozenFinalRestoreWorkerError(
            "rendered database process identity is invalid"
        )
    environment = _environment_map(
        image_config.get("Env", []),
        label="PostgreSQL image environment",
    )
    environment.update(
        _environment_map(
            service.get("environment", {}),
            label="rendered database environment",
        )
    )
    image_labels = image_config.get("Labels")
    if image_labels is None:
        image_labels = {}
    if (
        not isinstance(image_labels, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in image_labels.items()
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "PostgreSQL image labels are invalid"
        )
    expected_labels = {**image_labels, **service["labels"]}
    hash_output = runner.run(
        [
            *_restore_compose_base(manifest),
            "config",
            "--hash",
            service_name,
        ],
        timeout=60,
        env=command_env,
    )
    match = re.fullmatch(
        rf"{re.escape(service_name)} ([0-9a-f]{{64}})\n?",
        hash_output,
    )
    logging = service.get("logging")
    if (
        match is None
        or not isinstance(logging, dict)
        or not isinstance(logging.get("driver"), str)
        or not isinstance(logging.get("options"), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in logging["options"].items()
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "rendered database config hash or logging differs"
        )
    return DatabaseRuntimeContract(
        service=service_name,
        container_name=f"{manifest.paths.project_name}-{service_name}-1",
        config_hash=match.group(1),
        command=command,
        entrypoint=entrypoint,
        user=user,
        working_dir=working_dir,
        stop_signal=stop_signal,
        environment=dict(sorted(environment.items())),
        healthcheck=_compose_healthcheck(service.get("healthcheck")),
        labels=dict(sorted(expected_labels.items())),
        cgroup_parent=service["cgroup_parent"],
        restart_policy=service["restart"],
        nano_cpus=_nano_cpus(
            service.get("cpus"),
            label="rendered database CPU limit",
        ),
        memory=_memory_bytes(
            service.get("mem_limit"),
            label="rendered database memory limit",
        ),
        pids_limit=service["pids_limit"],
        log_config={
            "Type": logging["driver"],
            "Config": dict(sorted(logging["options"].items())),
        },
    )


def _database_runtime_contract(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> DatabaseRuntimeContract:
    return _service_runtime_contract(
        manifest,
        runner,
        service_name=f"{manifest.role}_db",
        expected_restart="unless-stopped",
    )


def _validate_database_runtime(
    row: Mapping[str, Any],
    manifest: RoleManifest,
    contract: DatabaseRuntimeContract,
) -> None:
    config = row.get("Config")
    host = row.get("HostConfig")
    labels = config.get("Labels") if isinstance(config, dict) else None
    restart = host.get("RestartPolicy") if isinstance(host, dict) else None
    non_compose_labels = (
        {
            key: value
            for key, value in labels.items()
            if not key.startswith("com.docker.compose.")
        }
        if isinstance(labels, dict)
        else None
    )
    expected_network = f"{manifest.paths.project_name}_{manifest.role}"
    if (
        row.get("Name") != f"/{contract.container_name}"
        or not isinstance(config, dict)
        or config.get("Image") != manifest.postgres_image_id
        or _string_vector(
            config.get("Cmd"),
            label="database container command",
        )
        != contract.command
        or _string_vector(
            config.get("Entrypoint"),
            label="database container entrypoint",
        )
        != contract.entrypoint
        or config.get("User", "") != contract.user
        or config.get("WorkingDir", "") != contract.working_dir
        or config.get("StopSignal", "") != contract.stop_signal
        or _environment_map(
            config.get("Env"),
            label="database container environment",
        )
        != contract.environment
        or _inspect_healthcheck(config.get("Healthcheck"))
        != contract.healthcheck
        or not isinstance(labels, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labels.items()
        )
        or non_compose_labels != contract.labels
        or labels.get("com.docker.compose.project")
        != manifest.paths.project_name
        or labels.get("com.docker.compose.service") != contract.service
        or labels.get("com.docker.compose.oneoff") != "False"
        or labels.get("com.docker.compose.container-number") != "1"
        or labels.get("com.docker.compose.config-hash")
        != contract.config_hash
        or not isinstance(host, dict)
        or host.get("NetworkMode") != expected_network
        or host.get("CgroupParent") != contract.cgroup_parent
        or host.get("NanoCpus") != contract.nano_cpus
        or host.get("Memory") != contract.memory
        or host.get("MemoryReservation", 0) != 0
        or host.get("MemorySwap", 0) != 0
        or host.get("PidsLimit") != contract.pids_limit
        or host.get("CpuShares", 0) != 0
        or host.get("CpuPeriod", 0) != 0
        or host.get("CpuQuota", 0) != 0
        or host.get("CpusetCpus", "") != ""
        or host.get("CpusetMems", "") != ""
        or host.get("AutoRemove") is not False
        or host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not False
        or host.get("PublishAllPorts") is not False
        or host.get("PortBindings") not in (None, {})
        or host.get("CapAdd") not in (None, [])
        or host.get("CapDrop") not in (None, [])
        or host.get("SecurityOpt") not in (None, [])
        or host.get("Devices") not in (None, [])
        or host.get("DeviceRequests") not in (None, [])
        or host.get("PidMode", "") != ""
        or host.get("IpcMode", "private") != "private"
        or host.get("UTSMode", "") != ""
        or host.get("UsernsMode", "") != ""
        or host.get("Links") not in (None, [])
        or host.get("ExtraHosts") not in (None, [])
        or host.get("Dns") not in (None, [])
        or host.get("DnsOptions") not in (None, [])
        or host.get("DnsSearch") not in (None, [])
        or host.get("GroupAdd") not in (None, [])
        or host.get("Sysctls") not in (None, {})
        or host.get("Tmpfs") not in (None, {})
        or host.get("Binds")
        != [
            f"{manifest.paths.postgres}:"
            "/var/lib/postgresql/data:rw"
        ]
        or host.get("LogConfig") != contract.log_config
        or not isinstance(restart, dict)
        or restart.get("Name") != contract.restart_policy
        or restart.get("MaximumRetryCount", 0) != 0
    ):
        raise FrozenFinalRestoreWorkerError(
            "database container immutable runtime config differs"
        )


def _restore_tool_command_is_allowed(command: tuple[str, ...] | None) -> bool:
    if command == (
        "sh",
        "-ec",
        "exec pg_restore --exit-on-error --single-transaction "
        '--no-owner --no-acl --dbname "$PGDATABASE"',
    ):
        return True
    if command is None or len(command) not in {6, 7} or command[0] != "psql":
        return False
    if command[:4] != (
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-psqlrc",
    ):
        return False
    if len(command) == 6:
        return command[4] == "-Atqc" and len(command[5]) <= MAX_JSON_BYTES
    return (
        command[4:6] == ("--quiet", "--command")
        and len(command[6]) <= MAX_JSON_BYTES
    )


def _validate_restore_oneoff_runtime(
    row: Mapping[str, Any],
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> None:
    contract = _service_runtime_contract(
        manifest,
        runner,
        service_name=f"{manifest.role}_restore_tool",
        expected_restart="no",
    )
    config = row.get("Config")
    host = row.get("HostConfig")
    labels = config.get("Labels") if isinstance(config, dict) else None
    restart = host.get("RestartPolicy") if isinstance(host, dict) else None
    command = (
        _string_vector(
            config.get("Cmd"),
            label="restore one-off command",
        )
        if isinstance(config, dict)
        else None
    )
    environment = (
        _environment_map(
            config.get("Env"),
            label="restore one-off environment",
        )
        if isinstance(config, dict)
        else {}
    )
    fingerprint_environment = {
        **contract.environment,
        "PGOPTIONS": DATABASE_FINGERPRINT_PGOPTIONS,
        "PGCLIENTENCODING": DATABASE_FINGERPRINT_CLIENT_ENCODING,
    }
    environment_items = tuple(sorted(environment.items()))
    non_compose_labels = (
        {
            key: value
            for key, value in labels.items()
            if not key.startswith("com.docker.compose.")
        }
        if isinstance(labels, dict)
        else None
    )
    expected_non_compose = {
        **contract.labels,
        "trading-bot.production.restore-generation": (
            manifest.restore_generation_sha256
        ),
    }
    expected_binds = {
        f"{manifest.paths.restore_input_root}:/run/restore-input:ro",
        f"{manifest.paths.uploads}:/run/restore-target/uploads:rw",
        f"{manifest.paths.audit}:/run/restore-target/audit:rw",
    }
    binds = host.get("Binds") if isinstance(host, dict) else None
    if (
        not isinstance(config, dict)
        or config.get("Image") != manifest.postgres_image_id
        or not _restore_tool_command_is_allowed(command)
        or _string_vector(
            config.get("Entrypoint"),
            label="restore one-off entrypoint",
        )
        != contract.entrypoint
        or config.get("User", "") != contract.user
        or config.get("WorkingDir", "") != contract.working_dir
        or config.get("StopSignal", "") != contract.stop_signal
        or environment_items not in {
            tuple(sorted(contract.environment.items())),
            tuple(sorted(fingerprint_environment.items())),
        }
        or _inspect_healthcheck(config.get("Healthcheck"))
        != contract.healthcheck
        or not isinstance(labels, dict)
        or non_compose_labels != expected_non_compose
        or labels.get("com.docker.compose.project")
        != manifest.paths.project_name
        or labels.get("com.docker.compose.service") != contract.service
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("com.docker.compose.config-hash")
        != contract.config_hash
        or not isinstance(row.get("Name"), str)
        or re.fullmatch(
            rf"/{re.escape(manifest.paths.project_name)}-"
            rf"{re.escape(contract.service)}-run-[a-z0-9]+",
            str(row["Name"]),
        )
        is None
        or not isinstance(host, dict)
        or host.get("NetworkMode")
        != f"{manifest.paths.project_name}_{manifest.role}"
        or host.get("CgroupParent") != contract.cgroup_parent
        or host.get("NanoCpus") != contract.nano_cpus
        or host.get("Memory") != contract.memory
        or host.get("MemoryReservation", 0) != 0
        or host.get("MemorySwap", 0) != 0
        or host.get("PidsLimit") != contract.pids_limit
        or host.get("CpuShares", 0) != 0
        or host.get("CpuPeriod", 0) != 0
        or host.get("CpuQuota", 0) != 0
        or host.get("CpusetCpus", "") != ""
        or host.get("CpusetMems", "") != ""
        or host.get("AutoRemove") is not True
        or host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not False
        or host.get("PublishAllPorts") is not False
        or host.get("PortBindings") not in (None, {})
        or host.get("CapAdd") not in (None, [])
        or host.get("CapDrop") not in (None, [])
        or host.get("SecurityOpt") not in (None, [])
        or host.get("Devices") not in (None, [])
        or host.get("DeviceRequests") not in (None, [])
        or host.get("PidMode", "") != ""
        or host.get("IpcMode", "private") != "private"
        or host.get("UTSMode", "") != ""
        or host.get("UsernsMode", "") != ""
        or host.get("Links") not in (None, [])
        or host.get("ExtraHosts") not in (None, [])
        or host.get("Dns") not in (None, [])
        or host.get("DnsOptions") not in (None, [])
        or host.get("DnsSearch") not in (None, [])
        or host.get("GroupAdd") not in (None, [])
        or host.get("Sysctls") not in (None, {})
        or host.get("Tmpfs") not in (None, {})
        or not isinstance(binds, list)
        or len(binds) != len(expected_binds)
        or set(binds) != expected_binds
        or host.get("LogConfig") != contract.log_config
        or not isinstance(restart, dict)
        or restart.get("Name") != "no"
        or restart.get("MaximumRetryCount", 0) != 0
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore one-off immutable runtime config differs"
        )


def _network_runtime_contract(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> NetworkRuntimeContract:
    _verify_role_compose(manifest, runner)
    command_env, _ = _compose_environment(manifest)
    rendered = _load_json_output(
        runner.run(
            [
                *_restore_compose_base(manifest),
                "config",
                "--format",
                "json",
            ],
            timeout=60,
            env=command_env,
        ),
        label="rendered network Compose",
    )
    networks = rendered.get("networks") if isinstance(rendered, dict) else None
    network = (
        networks.get(manifest.role)
        if isinstance(networks, dict)
        else None
    )
    expected_name = f"{manifest.paths.project_name}_{manifest.role}"
    if (
        not isinstance(network, dict)
        or network.get("name") != expected_name
        or network.get("internal") is not True
        or network.get("driver") not in (None, "bridge")
        or network.get("attachable") not in (None, False)
        or network.get("enable_ipv4") not in (None, True)
        or network.get("enable_ipv6") not in (None, False)
        or network.get("external") not in (None, False)
        or network.get("driver_opts") not in (None, {})
        or network.get("ipam") not in (None, {})
        or not isinstance(network.get("labels"), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in network["labels"].items()
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "rendered network runtime contract is invalid"
        )
    version = runner.run(
        [DOCKER, "compose", "version", "--short"],
        timeout=30,
        env=command_env,
    )
    if re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?\n?",
        version,
    ) is None:
        raise FrozenFinalRestoreWorkerError(
            "Docker Compose version identity is invalid"
        )
    return NetworkRuntimeContract(
        logical_name=manifest.role,
        name=expected_name,
        project_name=manifest.paths.project_name,
        labels=dict(sorted(network["labels"].items())),
        compose_version=version.strip(),
    )


def _validate_network_runtime(
    row: Mapping[str, Any],
    contract: NetworkRuntimeContract,
    container_rows: Mapping[str, Mapping[str, Any]],
) -> None:
    labels = row.get("Labels")
    expected_labels = {
        **contract.labels,
        "com.docker.compose.network": contract.logical_name,
        "com.docker.compose.project": contract.project_name,
        "com.docker.compose.version": contract.compose_version,
    }
    ipam = row.get("IPAM")
    ipam_config = ipam.get("Config") if isinstance(ipam, dict) else None
    if (
        row.get("Name") != contract.name
        or row.get("Driver") != "bridge"
        or row.get("Scope") != "local"
        or row.get("Internal") is not True
        or row.get("Attachable") is not False
        or row.get("Ingress") is not False
        or row.get("ConfigOnly") is not False
        or row.get("EnableIPv4") not in (None, True)
        or row.get("EnableIPv6") is not False
        or row.get("Options") not in (None, {})
        or row.get("ConfigFrom") != {"Network": ""}
        or labels != expected_labels
        or not isinstance(ipam, dict)
        or ipam.get("Driver") != "default"
        or ipam.get("Options") not in (None, {})
        or not isinstance(ipam_config, list)
        or len(ipam_config) != 1
        or not isinstance(ipam_config[0], dict)
        or set(ipam_config[0]) != {"Subnet", "Gateway"}
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation network immutable runtime config differs"
        )
    try:
        subnet = ipaddress.ip_network(ipam_config[0]["Subnet"], strict=True)
        gateway = ipaddress.ip_address(ipam_config[0]["Gateway"])
    except (TypeError, ValueError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "generation network IPAM config is invalid"
        ) from exc
    if (
        not isinstance(subnet, ipaddress.IPv4Network)
        or not subnet.is_private
        or gateway not in subnet
        or gateway in {subnet.network_address, subnet.broadcast_address}
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation network IPAM config is invalid"
        )
    expected_attached: dict[str, Mapping[str, Any]] = {}
    for identifier, container in container_rows.items():
        settings = container.get("NetworkSettings")
        networks = (
            settings.get("Networks") if isinstance(settings, dict) else None
        )
        endpoint = (
            networks.get(contract.name)
            if isinstance(networks, dict)
            else None
        )
        if not isinstance(endpoint, dict):
            raise FrozenFinalRestoreWorkerError(
                "generation container network endpoint is invalid"
            )
        endpoint_id = endpoint.get("EndpointID")
        if endpoint_id in (None, ""):
            continue
        if CONTAINER_ID_RE.fullmatch(str(endpoint_id)) is None:
            raise FrozenFinalRestoreWorkerError(
                "generation container network endpoint is invalid"
            )
        expected_attached[identifier] = endpoint
    attached = row.get("Containers")
    if (
        not isinstance(attached, dict)
        or set(attached) != set(expected_attached)
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation network endpoint membership differs"
        )
    for identifier, endpoint in expected_attached.items():
        observed = attached[identifier]
        container = container_rows[identifier]
        name = container.get("Name")
        prefix = endpoint.get("IPPrefixLen")
        ipv4_address = endpoint.get("IPAddress")
        ipv6_prefix = endpoint.get("GlobalIPv6PrefixLen")
        ipv6_address = endpoint.get("GlobalIPv6Address")
        expected_ipv4 = (
            f"{ipv4_address}/{prefix}" if ipv4_address and prefix else ""
        )
        expected_ipv6 = (
            f"{ipv6_address}/{ipv6_prefix}"
            if ipv6_address and ipv6_prefix
            else ""
        )
        try:
            endpoint_ipv4 = (
                ipaddress.ip_interface(expected_ipv4)
                if expected_ipv4
                else None
            )
        except ValueError as exc:
            raise FrozenFinalRestoreWorkerError(
                "generation network endpoint address is invalid"
            ) from exc
        if (
            not isinstance(observed, dict)
            or set(observed)
            != {
                "Name",
                "EndpointID",
                "MacAddress",
                "IPv4Address",
                "IPv6Address",
            }
            or not isinstance(name, str)
            or observed["Name"] != name.removeprefix("/")
            or observed["EndpointID"] != endpoint["EndpointID"]
            or observed["MacAddress"] != endpoint.get("MacAddress", "")
            or observed["IPv4Address"] != expected_ipv4
            or observed["IPv6Address"] != expected_ipv6
            or endpoint.get("NetworkID") != row.get("Id")
            or endpoint_ipv4 is None
            or endpoint_ipv4.ip not in subnet
            or endpoint_ipv4.network.prefixlen != subnet.prefixlen
        ):
            raise FrozenFinalRestoreWorkerError(
                "generation network endpoint details differ"
            )


def _ensure_private_directory(path: Path, *, create: bool) -> None:
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(
            path,
            label="generation directory",
        )
        try:
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            if not create:
                raise FrozenFinalRestoreWorkerError(
                    "generation directory is absent"
                )
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or Path(os.path.realpath(path)) != path
        ):
            raise FrozenFinalRestoreWorkerError(
                "generation directory must be root-owned mode 0700"
            )
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "generation directory could not be secured"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _ensure_directory_chain(path: Path, *, existing_parent: Path) -> None:
    if path == existing_parent:
        _ensure_private_directory(path, create=False)
        return
    try:
        path.relative_to(existing_parent)
    except ValueError as exc:
        raise FrozenFinalRestoreWorkerError(
            "generation directory escaped its root"
        ) from exc
    current = existing_parent
    _ensure_private_directory(current, create=False)
    for part in path.relative_to(existing_parent).parts:
        current = current / part
        _ensure_private_directory(current, create=True)


def _directory_entries(path: Path) -> list[str]:
    opened = _open_secure_directory(
        path,
        label="generation directory",
        exact_mode=0o700,
    )
    if opened is None:
        raise FrozenFinalRestoreWorkerError(
            "generation directory is absent"
        )
    descriptor = opened[0]
    try:
        return sorted(os.listdir(descriptor))
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "generation directory cannot be enumerated"
        ) from exc
    finally:
        os.close(descriptor)


def _ensure_restore_directory(path: Path, *, create: bool) -> None:
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(
            path,
            label="restore directory",
        )
        try:
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            if not create:
                raise FrozenFinalRestoreWorkerError(
                    "restore directory is absent"
                )
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or Path(os.path.realpath(path)) != path
        ):
            raise FrozenFinalRestoreWorkerError(
                "restore directory is unsafe"
            )
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "restore directory could not be secured"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


@contextmanager
def _worker_lock(manifest: RoleManifest):  # noqa: ANN202
    _ensure_private_directory(
        manifest.paths.secret_generation_root,
        create=False,
    )
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(
            manifest.paths.lock,
            label="restore worker lock",
        )
        descriptor = os.open(
            name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise FrozenFinalRestoreWorkerError(
            "restore worker lock is unavailable"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise FrozenFinalRestoreWorkerError(
                "restore worker lock is unsafe"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _event_hash(event: Mapping[str, Any]) -> str:
    return _sha256(
        _canonical_json(
            {key: value for key, value in event.items() if key != "event_sha256"}
        )
    )


def _event_path(directory: Path, index: int) -> Path:
    return directory / f"{index:06d}.json"


def _write_secure_new_file(
    directory: Path,
    name: str,
    payload: bytes,
    *,
    label: str,
) -> None:
    if (
        not name
        or Path(name).name != name
        or "/" in name
        or len(payload) > MAX_JSON_BYTES
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{label} publication request is invalid"
        )
    opened = _open_secure_directory(
        directory,
        label=f"{label} directory",
        exact_mode=0o700,
    )
    if opened is None:
        raise FrozenFinalRestoreWorkerError(
            f"{label} directory is unavailable"
        )
    directory_descriptor, directory_before = opened
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        remainder = memoryview(payload)
        while remainder:
            written = os.write(descriptor, remainder)
            if written <= 0:
                raise FrozenFinalRestoreWorkerError(
                    f"{label} publication made no progress"
                )
            remainder = remainder[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(payload)
            or (metadata.st_dev, metadata.st_ino)
            != (visible.st_dev, visible.st_ino)
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} publication identity differs"
            )
        os.fsync(directory_descriptor)
        directory_after = os.fstat(directory_descriptor)
        if (
            directory_before.st_dev,
            directory_before.st_ino,
        ) != (
            directory_after.st_dev,
            directory_after.st_ino,
        ):
            raise FrozenFinalRestoreWorkerError(
                f"{label} directory changed during publication"
            )
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            f"{label} could not be created"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_descriptor)


def _read_events(
    manifest: RoleManifest,
    lease: LeaseBinding,
) -> tuple[list[dict[str, Any]], list[str], str | None, dict[str, str]]:
    if not manifest.paths.journal.exists():
        return [], [], None, {}
    opened = _open_secure_directory(
        manifest.paths.journal,
        label="restore journal",
        exact_mode=0o700,
    )
    if opened is None:
        raise FrozenFinalRestoreWorkerError(
            "restore journal is unavailable"
        )
    descriptor = opened[0]
    try:
        names = sorted(os.listdir(descriptor))
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "restore journal cannot be enumerated"
        ) from exc
    finally:
        os.close(descriptor)
    expected_names = [
        f"{index:06d}.json" for index in range(1, len(names) + 1)
    ]
    if names != expected_names or len(names) > 1000:
        raise FrozenFinalRestoreWorkerError(
            "restore journal sequence is not contiguous"
        )
    events: list[dict[str, Any]] = []
    completed: list[str] = []
    active: str | None = None
    evidence: dict[str, str] = {}
    previous = ZERO_SHA256
    attempts: dict[str, int] = {}
    for index, name in enumerate(names, 1):
        event, _payload, _digest = _read_json(
            manifest.paths.journal / name,
            label="restore journal event",
        )
        if (
            set(event) != JOURNAL_EVENT_FIELDS
            or event["schema"] != JOURNAL_EVENT_SCHEMA
            or event["operation_id"] != manifest.operation_id
            or event["role"] != manifest.role
            or event["release_sha"] != manifest.release_sha
            or event["restore_set_sha256"]
            != manifest.restore_set_sha256
            or event["restore_generation_sha256"]
            != manifest.restore_generation_sha256
            or event["role_manifest_sha256"]
            != manifest.canonical_sha256
            or event["installer_receipt_sha256"]
            != manifest.installer_receipt_sha256
            or event["legacy_frozen_receipt_sha256"]
            != lease.receipt_sha256
            or event["live_lease_claim_path"] != str(lease.path)
            or event["live_lease_claim_sha256"] != lease.sha256
            or event["live_lease_claim_epoch"] != lease.epoch
            or event["live_lease_claim_nonce"] != lease.nonce
            or event["index"] != index
            or event["previous_event_sha256"] != previous
            or event["event_sha256"] != _event_hash(event)
            or event["action"] not in ACTIONS
            or event["kind"] not in {"started", "resumed", "completed"}
            or type(event["attempt"]) is not int
            or not 1 <= event["attempt"] <= 100
        ):
            raise FrozenFinalRestoreWorkerError(
                "restore journal event binding differs"
            )
        action = event["action"]
        if event["kind"] == "started":
            if (
                active is not None
                or action != ACTIONS[len(completed)]
                or event["attempt"] != 1
                or event["evidence_sha256"] is not None
            ):
                raise FrozenFinalRestoreWorkerError(
                    "restore journal start ordering is invalid"
                )
            active = action
            attempts[action] = 1
        elif event["kind"] == "resumed":
            if (
                active != action
                or event["attempt"] != attempts[action] + 1
                or event["evidence_sha256"] is not None
            ):
                raise FrozenFinalRestoreWorkerError(
                    "restore journal resume ordering is invalid"
                )
            attempts[action] = event["attempt"]
        else:
            if (
                active != action
                or event["attempt"] != attempts[action]
                or not isinstance(event["evidence_sha256"], str)
                or SHA256_RE.fullmatch(event["evidence_sha256"]) is None
                or event["evidence_sha256"] == ZERO_SHA256
            ):
                raise FrozenFinalRestoreWorkerError(
                    "restore journal completion ordering is invalid"
                )
            evidence[action] = event["evidence_sha256"]
            completed.append(action)
            active = None
        if event["kind"] in {"started", "resumed"}:
            _nonzero_sha256(
                event["authority_verification_sha256"],
                label="journal authority verification",
            )
        elif event["authority_verification_sha256"] is not None:
            raise FrozenFinalRestoreWorkerError(
                "completion event has unexpected authority verification"
            )
        previous = event["event_sha256"]
        events.append(event)
    return events, completed, active, evidence


def _authority_verification(
    verifier: LiveAuthorityVerifier,
    lease: LeaseBinding,
    boundary: str,
    *,
    previous_sequence: int,
) -> tuple[dict[str, Any], str, int]:
    try:
        result = dict(verifier(lease, boundary))
    except Exception as exc:
        raise FrozenFinalRestoreWorkerError(
            "controller live-authority verification failed"
        ) from exc
    if (
        set(result) != LIVE_AUTHORITY_FIELDS
        or result["schema"] != LIVE_AUTHORITY_SCHEMA
        or result["status"] != "verified-live"
        or result["boundary"] != boundary
        or result["claim_sha256"] != lease.sha256
        or result["claim_epoch"] != lease.epoch
        or result["claim_nonce"] != lease.nonce
        or result["legacy_frozen_receipt_sha256"]
        != lease.receipt_sha256
        or result["controller_lock_held"] is not True
        or result["controller_authoritative"] is not True
        or type(result["verification_sequence"]) is not int
        or result["verification_sequence"] <= previous_sequence
        or not isinstance(result["verification_nonce"], str)
        or SHA256_RE.fullmatch(result["verification_nonce"]) is None
        or result["verification_nonce"] == ZERO_SHA256
    ):
        raise FrozenFinalRestoreWorkerError(
            "controller live-authority verification differs"
        )
    digest = _sha256(_canonical_json(result))
    return result, digest, result["verification_sequence"]


def _append_event(
    manifest: RoleManifest,
    lease: LeaseBinding,
    events: list[dict[str, Any]],
    *,
    kind: str,
    action: str,
    attempt: int,
    evidence_sha256: str | None,
    authority_verification_sha256: str | None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema": JOURNAL_EVENT_SCHEMA,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "restore_set_sha256": manifest.restore_set_sha256,
        "restore_generation_sha256": (
            manifest.restore_generation_sha256
        ),
        "role_manifest_sha256": manifest.canonical_sha256,
        "installer_receipt_sha256": manifest.installer_receipt_sha256,
        "legacy_frozen_receipt_sha256": lease.receipt_sha256,
        "live_lease_claim_path": str(lease.path),
        "live_lease_claim_sha256": lease.sha256,
        "live_lease_claim_epoch": lease.epoch,
        "live_lease_claim_nonce": lease.nonce,
        "index": len(events) + 1,
        "kind": kind,
        "action": action,
        "attempt": attempt,
        "evidence_sha256": evidence_sha256,
        "authority_verification_sha256": (
            authority_verification_sha256
        ),
        "previous_event_sha256": (
            events[-1]["event_sha256"] if events else ZERO_SHA256
        ),
        "event_sha256": "",
    }
    event["event_sha256"] = _event_hash(event)
    payload = _canonical_json(event) + b"\n"
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=False,
    )
    _write_secure_new_file(
        manifest.paths.journal,
        _event_path(manifest.paths.journal, event["index"]).name,
        payload,
        label="frozen-final restore journal event",
    )
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=False,
    )
    events.append(event)
    return event


def _publish_evidence(
    manifest: RoleManifest,
    lease: LeaseBinding,
    action: str,
    semantic: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    document = {
        "schema": EVIDENCE_SCHEMA,
        "status": "completed",
        "action": action,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "release_tree_sha": manifest.release_tree_sha,
        "controller_manifest_sha256": (
            manifest.controller_manifest_sha256
        ),
        "restore_set_sha256": manifest.restore_set_sha256,
        "restore_generation_sha256": (
            manifest.restore_generation_sha256
        ),
        "role_manifest_sha256": manifest.canonical_sha256,
        "installer_receipt_sha256": manifest.installer_receipt_sha256,
        "legacy_frozen_receipt_sha256": lease.receipt_sha256,
        "live_lease_claim_sha256": lease.sha256,
        "live_lease_claim_epoch": lease.epoch,
        "live_lease_claim_nonce": lease.nonce,
        "business_write_allowed": False,
        "public_or_private_app_started": False,
        "redis_restored": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "semantic": dict(semantic),
    }
    if set(document) != EVIDENCE_FIELDS:
        raise FrozenFinalRestoreWorkerError(
            "restore evidence fields are not exact"
        )
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload.rstrip(b"\n"))
    path = manifest.paths.evidence / f"{action}-{digest}.json"
    if path.exists() or path.is_symlink():
        observed = _read_root_file(
            path,
            label="existing restore evidence",
            maximum=MAX_JSON_BYTES,
        )
        if observed != payload:
            raise FrozenFinalRestoreWorkerError(
                "existing restore evidence differs"
            )
        return document, digest
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=False,
    )
    _write_secure_new_file(
        manifest.paths.evidence,
        path.name,
        payload,
        label="frozen-final restore evidence",
    )
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=False,
    )
    return document, digest


def _load_action_evidence(
    manifest: RoleManifest,
    lease: LeaseBinding,
    *,
    action: str,
    digest: str,
) -> dict[str, Any]:
    _nonzero_sha256(digest, label=f"{action} evidence")
    path = manifest.paths.evidence / f"{action}-{digest}.json"
    document, _payload, observed = _read_json(
        path,
        label=f"{action} restore evidence",
    )
    if (
        observed != digest
        or set(document) != EVIDENCE_FIELDS
        or document["schema"] != EVIDENCE_SCHEMA
        or document["status"] != "completed"
        or document["action"] != action
        or document["operation_id"] != manifest.operation_id
        or document["role"] != manifest.role
        or document["release_sha"] != manifest.release_sha
        or document["release_tree_sha"] != manifest.release_tree_sha
        or document["controller_manifest_sha256"]
        != manifest.controller_manifest_sha256
        or document["restore_set_sha256"]
        != manifest.restore_set_sha256
        or document["restore_generation_sha256"]
        != manifest.restore_generation_sha256
        or document["role_manifest_sha256"]
        != manifest.canonical_sha256
        or document["installer_receipt_sha256"]
        != manifest.installer_receipt_sha256
        or document["legacy_frozen_receipt_sha256"]
        != lease.receipt_sha256
        or document["live_lease_claim_sha256"] != lease.sha256
        or document["live_lease_claim_epoch"] != lease.epoch
        or document["live_lease_claim_nonce"] != lease.nonce
        or document["business_write_allowed"] is not False
        or document["public_or_private_app_started"] is not False
        or document["redis_restored"] is not False
        or document["current_mutated"] is not False
        or document["legacy_mutated"] is not False
        or document["object_storage_mutated"] is not False
        or not isinstance(document["semantic"], dict)
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{action} restore evidence differs from its journal binding"
        )
    return document


def _validate_orphan_evidence(
    manifest: RoleManifest,
    lease: LeaseBinding,
    *,
    action: str,
    journal_digest: str,
) -> None:
    prefix = f"{action}-"
    try:
        candidates = [
            path
            for path in manifest.paths.evidence.iterdir()
            if path.name.startswith(prefix) and path.suffix == ".json"
        ]
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "restore evidence directory cannot be enumerated"
        ) from exc
    for path in candidates:
        candidate = path.name[len(prefix) : -len(".json")]
        if SHA256_RE.fullmatch(candidate) is None:
            raise FrozenFinalRestoreWorkerError(
                "restore evidence directory contains an invalid name"
            )
        _load_action_evidence(
            manifest,
            lease,
            action=action,
            digest=candidate,
        )
    if not any(
        path.name == f"{action}-{journal_digest}.json"
        for path in candidates
    ):
        raise FrozenFinalRestoreWorkerError(
            f"{action} journal evidence is absent"
        )


def _project_container_ids(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> list[str]:
    command_env, _ = _compose_environment(manifest)
    raw = runner.run(
        [
            DOCKER,
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={manifest.paths.project_name}",
        ],
        timeout=30,
        env=command_env,
    )
    values = [value for value in raw.splitlines() if value]
    if (
        len(values) != len(set(values))
        or len(values) > 32
        or any(CONTAINER_ID_RE.fullmatch(value) is None for value in values)
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation container inventory is invalid"
        )
    return sorted(values)


def _inspect_container(
    identifier: str,
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> Mapping[str, Any]:
    command_env, _ = _compose_environment(manifest)
    document = _load_json_output(
        runner.run(
            [DOCKER, "inspect", identifier],
            timeout=30,
            env=command_env,
        ),
        label="generation container inspection",
    )
    if (
        not isinstance(document, list)
        or len(document) != 1
        or not isinstance(document[0], dict)
        or document[0].get("Id") != identifier
        or CONTAINER_ID_RE.fullmatch(str(document[0].get("Id"))) is None
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation container inspection is invalid"
        )
    return document[0]


def _container_semantics(
    row: Mapping[str, Any],
    manifest: RoleManifest,
) -> tuple[str, bool]:
    config = row.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    service = (
        labels.get("com.docker.compose.service")
        if isinstance(labels, dict)
        else None
    )
    oneoff = (
        labels.get("com.docker.compose.oneoff") == "True"
        if isinstance(labels, dict)
        else False
    )
    expected_services = {
        f"{manifest.role}_db",
        f"{manifest.role}_restore_tool",
    }
    expected_network = (
        f"{manifest.paths.project_name}_{manifest.role}"
    )
    network_settings = row.get("NetworkSettings")
    networks = (
        network_settings.get("Networks")
        if isinstance(network_settings, dict)
        else None
    )
    host = row.get("HostConfig")
    if (
        not isinstance(config, dict)
        or not isinstance(labels, dict)
        or service not in expected_services
        or labels.get("com.docker.compose.project")
        != manifest.paths.project_name
        or labels.get("trading-bot.production.operation-id")
        != manifest.operation_id
        or row.get("Image") != manifest.postgres_image_id
        or config.get("Image") != manifest.postgres_image_id
        or not isinstance(host, dict)
        or host.get("Privileged") is not False
        or host.get("NetworkMode") != expected_network
        or not isinstance(networks, dict)
        or set(networks) != {expected_network}
        or (
            host.get("PortBindings") is not None
            and host.get("PortBindings") != {}
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation container escaped its exact project"
        )
    if service == f"{manifest.role}_db" and oneoff:
        raise FrozenFinalRestoreWorkerError(
            "generation database is unexpectedly one-off"
        )
    if service == f"{manifest.role}_restore_tool" and not oneoff:
        raise FrozenFinalRestoreWorkerError(
            "restore tool is unexpectedly persistent"
        )
    if (
        oneoff
        and labels.get("trading-bot.production.restore-generation")
        != manifest.restore_generation_sha256
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore one-off generation label differs"
        )
    mounts = row.get("Mounts")
    if not isinstance(mounts, list):
        raise FrozenFinalRestoreWorkerError(
            "generation container mount inventory is invalid"
        )
    if service == f"{manifest.role}_db":
        observed: set[tuple[str, str, bool]] = set()
        for mount in mounts:
            if (
                not isinstance(mount, dict)
                or mount.get("Type") != "bind"
                or not isinstance(mount.get("Source"), str)
                or not isinstance(mount.get("Destination"), str)
            ):
                raise FrozenFinalRestoreWorkerError(
                    "generation database has a foreign mount"
                )
            observed.add(
                (
                    mount["Source"],
                    mount["Destination"],
                    mount.get("RW") is True,
                )
            )
        if observed != {
            (
                str(manifest.paths.postgres),
                "/var/lib/postgresql/data",
                True,
            )
        }:
            raise FrozenFinalRestoreWorkerError(
                "generation database bind escaped its final data root"
            )
    return str(service), oneoff


def _preflight_generation_resources(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> Mapping[str, Any]:
    _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    command_env, _ = _compose_environment(manifest)
    identifiers = _project_container_ids(manifest, runner)
    database_count = 0
    oneoff_count = 0
    database_contract: DatabaseRuntimeContract | None = None
    container_rows: dict[str, Mapping[str, Any]] = {}
    for identifier in identifiers:
        row = _inspect_container(identifier, manifest, runner)
        container_rows[identifier] = row
        service, oneoff = _container_semantics(row, manifest)
        if oneoff:
            oneoff_count += 1
        elif service == f"{manifest.role}_db":
            if database_contract is None:
                database_contract = _database_runtime_contract(
                    manifest,
                    runner,
                )
            _validate_database_runtime(
                row,
                manifest,
                database_contract,
            )
            database_count += 1
    if database_count > 1:
        raise FrozenFinalRestoreWorkerError(
            "generation has multiple database containers"
        )
    network_name = f"{manifest.paths.project_name}_{manifest.role}"
    network_ids = [
        value
        for value in runner.run(
            [
                DOCKER,
                "network",
                "ls",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"name=^{network_name}$",
            ],
            timeout=30,
            env=command_env,
        ).splitlines()
        if value
    ]
    if (
        len(network_ids) > 1
        or any(
            CONTAINER_ID_RE.fullmatch(value) is None
            for value in network_ids
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "generation network inventory is invalid"
        )
    if network_ids:
        rows = _load_json_output(
            runner.run(
                [DOCKER, "network", "inspect", network_ids[0]],
                timeout=30,
                env=command_env,
            ),
            label="generation network inspection",
        )
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
            or rows[0].get("Id") != network_ids[0]
        ):
            raise FrozenFinalRestoreWorkerError(
                "generation network inspection is invalid"
            )
        row = rows[0]
        labels = row.get("Labels")
        attached = row.get("Containers")
        if (
            row.get("Name") != network_name
            or row.get("Internal") is not True
            or not isinstance(labels, dict)
            or labels.get("com.docker.compose.project")
            != manifest.paths.project_name
            or labels.get("trading-bot.production.operation-id")
            != manifest.operation_id
            or not isinstance(attached, dict)
            or not set(attached).issubset(set(identifiers))
        ):
            raise FrozenFinalRestoreWorkerError(
                "generation network escaped its exact project"
            )
        _validate_network_runtime(
            row,
            _network_runtime_contract(manifest, runner),
            container_rows,
        )
    volumes = [
        value
        for value in runner.run(
            [
                DOCKER,
                "volume",
                "ls",
                "--quiet",
                "--filter",
                (
                    "label=com.docker.compose.project="
                    f"{manifest.paths.project_name}"
                ),
            ],
            timeout=30,
            env=command_env,
        ).splitlines()
        if value
    ]
    if volumes:
        raise FrozenFinalRestoreWorkerError(
            "final generation must not own named Docker volumes"
        )
    return {
        "container_count": len(identifiers),
        "database_count": database_count,
        "oneoff_count": oneoff_count,
        "network_present": bool(network_ids),
        "named_volume_count": 0,
    }


def _cleanup_oneoffs(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> list[dict[str, Any]]:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=False,
    )
    command_env, _ = _compose_environment(manifest)
    removed: list[dict[str, Any]] = []
    for _attempt in range(20):
        rows: list[tuple[str, Mapping[str, Any], str, bool]] = []
        for identifier in _project_container_ids(manifest, runner):
            row = _inspect_container(identifier, manifest, runner)
            service, oneoff = _container_semantics(row, manifest)
            rows.append((identifier, row, service, oneoff))
        oneoffs = [value for value in rows if value[3]]
        if not oneoffs:
            _recheck_runtime_path_identities(
                manifest,
                boundary,
                require_stores=False,
            )
            return removed
        for identifier, row, service, _oneoff in oneoffs:
            mounts = row.get("Mounts")
            if not isinstance(mounts, list):
                raise FrozenFinalRestoreWorkerError(
                    "one-off mount inventory is invalid"
                )
            anonymous: list[str] = []
            expected_binds = {
                (
                    str(manifest.paths.restore_input_root),
                    "/run/restore-input",
                    False,
                ),
                (
                    str(manifest.paths.uploads),
                    "/run/restore-target/uploads",
                    True,
                ),
                (
                    str(manifest.paths.audit),
                    "/run/restore-target/audit",
                    True,
                ),
            }
            observed_binds: set[tuple[str, str, bool]] = set()
            for mount in mounts:
                if not isinstance(mount, dict):
                    raise FrozenFinalRestoreWorkerError(
                        "one-off mount entry is invalid"
                    )
                if mount.get("Type") == "bind":
                    observed_binds.add(
                        (
                            str(mount.get("Source")),
                            str(mount.get("Destination")),
                            mount.get("RW") is True,
                        )
                    )
                elif (
                    mount.get("Type") == "volume"
                    and mount.get("Destination")
                    == "/var/lib/postgresql/data"
                    and mount.get("RW") is True
                    and isinstance(mount.get("Name"), str)
                    and re.fullmatch(
                        r"[0-9a-f]{64}", str(mount["Name"])
                    )
                ):
                    anonymous.append(str(mount["Name"]))
                else:
                    raise FrozenFinalRestoreWorkerError(
                        "one-off contains a foreign mount"
                    )
            if (
                service != f"{manifest.role}_restore_tool"
                or observed_binds != expected_binds
                or len(anonymous) > 1
            ):
                raise FrozenFinalRestoreWorkerError(
                    "refusing to clean a one-off outside the final generation"
                )
            _validate_restore_oneoff_runtime(
                row,
                manifest,
                runner,
            )
            _recheck_runtime_path_identities(
                manifest,
                boundary,
                require_stores=False,
            )
            runner.run(
                [
                    DOCKER,
                    "rm",
                    "--force",
                    "--volumes",
                    identifier,
                ],
                timeout=60,
                env=command_env,
            )
            _recheck_runtime_path_identities(
                manifest,
                boundary,
                require_stores=False,
            )
            removed.append(
                {
                    "container_id": identifier,
                    "service": service,
                    "anonymous_volumes": anonymous,
                    "project_name": manifest.paths.project_name,
                    "restore_generation_sha256": (
                        manifest.restore_generation_sha256
                    ),
                }
            )
    raise FrozenFinalRestoreWorkerError(
        "generation one-off residue did not converge"
    )


def _database_container(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    *,
    contract: DatabaseRuntimeContract | None = None,
) -> Mapping[str, Any] | None:
    database: list[Mapping[str, Any]] = []
    for identifier in _project_container_ids(manifest, runner):
        row = _inspect_container(identifier, manifest, runner)
        service, oneoff = _container_semantics(row, manifest)
        if not oneoff:
            if service != f"{manifest.role}_db":
                raise FrozenFinalRestoreWorkerError(
                    "generation has an unexpected persistent container"
                )
            if contract is None:
                contract = _database_runtime_contract(manifest, runner)
            _validate_database_runtime(row, manifest, contract)
            database.append(row)
    if len(database) > 1:
        raise FrozenFinalRestoreWorkerError(
            "generation has multiple database containers"
        )
    return database[0] if database else None


def _verify_database_healthy(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> str:
    contract = _database_runtime_contract(manifest, runner)
    deadline = time.monotonic() + 180
    while True:
        row = _database_container(
            manifest,
            runner,
            contract=contract,
        )
        if row is not None:
            state = row.get("State")
            health = (
                state.get("Health")
                if isinstance(state, dict)
                else None
            )
            if (
                isinstance(state, dict)
                and state.get("Status") == "running"
                and isinstance(health, dict)
                and health.get("Status") == "healthy"
                and isinstance(row.get("Id"), str)
                and CONTAINER_ID_RE.fullmatch(str(row["Id"]))
            ):
                return str(row["Id"])
        if time.monotonic() >= deadline:
            raise FrozenFinalRestoreWorkerError(
                "generation database did not become healthy"
            )
        time.sleep(1)


def _start_database(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    *,
    resumed: bool,
) -> str:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    command_env, _ = _compose_environment(manifest)
    service = f"{manifest.role}_db"
    preflight = _preflight_generation_resources(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    if not resumed:
        if (
            preflight["container_count"] != 0
            or preflight["database_count"] != 0
            or preflight["oneoff_count"] != 0
            or preflight["network_present"] is not False
            or preflight["named_volume_count"] != 0
        ):
            raise FrozenFinalRestoreWorkerError(
                "fresh final restore requires zero project residue"
            )
        if _directory_entries(manifest.paths.postgres):
            raise FrozenFinalRestoreWorkerError(
                "fresh final restore requires an empty PostgreSQL directory"
            )
    else:
        _cleanup_oneoffs(manifest, runner)
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
    after_cleanup = _preflight_generation_resources(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    if (
        not resumed
        and (
            after_cleanup["container_count"] != 0
            or after_cleanup["database_count"] != 0
            or after_cleanup["oneoff_count"] != 0
            or after_cleanup["network_present"] is not False
            or after_cleanup["named_volume_count"] != 0
            or _directory_entries(manifest.paths.postgres)
        )
    ):
        raise FrozenFinalRestoreWorkerError(
            "fresh final restore preconditions changed before Compose"
        )
    runner.run(
        [
            *_restore_compose_base(manifest),
            "up",
            "--detach",
            "--no-deps",
            "--no-build",
            "--no-recreate",
            "--pull",
            "never",
            service,
        ],
        timeout=300,
        env=command_env,
    )
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    after_up = _preflight_generation_resources(manifest, runner)
    if after_up != {
        "container_count": 1,
        "database_count": 1,
        "oneoff_count": 0,
        "network_present": True,
        "named_volume_count": 0,
    }:
        raise FrozenFinalRestoreWorkerError(
            "database startup resource closure differs"
        )
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    result = _verify_database_healthy(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    return result


def _compose_oneoff(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    *,
    command: Sequence[str],
    timeout: int,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
) -> str:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    _cleanup_oneoffs(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    command_env, _ = _compose_environment(manifest)
    arguments = [
        *_restore_compose_base(manifest),
        "run",
        "--rm",
        "--no-deps",
        "--pull",
        "never",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "--label",
        (
            "trading-bot.production.restore-generation="
            f"{manifest.restore_generation_sha256}"
        ),
        "-T",
        f"{manifest.role}_restore_tool",
        *command,
    ]
    try:
        result = runner.run(
            arguments,
            timeout=timeout,
            env=command_env,
            stdin=stdin,
        )
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
        return result
    finally:
        _cleanup_oneoffs(manifest, runner)
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )


def _psql(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    sql: str,
    *,
    timeout: int = 300,
) -> str:
    return _compose_oneoff(
        manifest,
        runner,
        command=[
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "--no-psqlrc",
            "-Atqc",
            sql,
        ],
        timeout=timeout,
    )


def _stream_copy(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    sql: str,
) -> StreamDigest:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    _cleanup_oneoffs(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    command_env, _ = _compose_environment(manifest)
    arguments = [
        *_restore_compose_base(manifest),
        "run",
        "--rm",
        "--no-deps",
        "--pull",
        "never",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "--label",
        (
            "trading-bot.production.restore-generation="
            f"{manifest.restore_generation_sha256}"
        ),
        "-T",
        "--env",
        f"PGOPTIONS={DATABASE_FINGERPRINT_PGOPTIONS}",
        "--env",
        f"PGCLIENTENCODING={DATABASE_FINGERPRINT_CLIENT_ENCODING}",
        f"{manifest.role}_restore_tool",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-psqlrc",
        "--quiet",
        "--command",
        sql,
    ]
    try:
        result = runner.stream(
            arguments,
            timeout=1800,
            env=command_env,
        )
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
        return result
    finally:
        _cleanup_oneoffs(manifest, runner)
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )


def _database_state(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> DatabaseState:
    tables = [
        value
        for value in _psql(
            manifest,
            runner,
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' ORDER BY tablename",
        ).splitlines()
        if value
    ]
    if not tables:
        return DatabaseState(
            alembic_revision=None,
            database_fingerprint_sha256=None,
            row_count=0,
            table_count=0,
        )
    revision_rows = [
        value
        for value in _psql(
            manifest,
            runner,
            "SELECT version_num FROM alembic_version",
        ).splitlines()
        if value
    ]
    if len(revision_rows) != 1:
        raise FrozenFinalRestoreWorkerError(
            "restored database revision inventory is invalid"
        )
    try:
        fingerprint, row_count, table_count = _fingerprint_from_streams(
            tables,
            lambda sql: _stream_copy(manifest, runner, sql),
        )
    except ProductionOperationError as exc:
        raise FrozenFinalRestoreWorkerError(
            "restored database fingerprint is invalid"
        ) from exc
    return DatabaseState(
        alembic_revision=revision_rows[0],
        database_fingerprint_sha256=fingerprint,
        row_count=row_count,
        table_count=table_count,
    )


def _database_matches(
    state: DatabaseState,
    expected: DatabaseExpectation,
) -> bool:
    return (
        state.alembic_revision == expected.alembic_revision
        and state.database_fingerprint_sha256
        == expected.database_fingerprint_sha256
        and state.row_count == expected.row_count
        and state.table_count == expected.table_count
    )


@contextmanager
def _held_artifact(binding: ArtifactBinding):  # noqa: ANN202
    descriptor = -1
    parent_descriptor = -1
    stream: BinaryIO | None = None
    try:
        parent_descriptor, name = _open_secure_parent(
            binding.path,
            label="restore artifact",
        )
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != binding.bytes
        ):
            raise FrozenFinalRestoreWorkerError(
                "restore artifact is unavailable or unsafe"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != binding.sha256:
            raise FrozenFinalRestoreWorkerError(
                "restore artifact digest differs"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        stream = os.fdopen(descriptor, "rb", closefd=False)
        yield stream
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise FrozenFinalRestoreWorkerError(
                "restore artifact path changed while being consumed"
            ) from exc
        if any(
            getattr(before, field) != getattr(after, field)
            or getattr(before, field) != getattr(path_after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ):
            raise FrozenFinalRestoreWorkerError(
                "restore artifact changed while being consumed"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        final_digest = hashlib.sha256()
        final_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            final_bytes += len(chunk)
            final_digest.update(chunk)
        if (
            final_bytes != binding.bytes
            or final_digest.hexdigest() != binding.sha256
        ):
            raise FrozenFinalRestoreWorkerError(
                "restore artifact content changed while being consumed"
            )
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "restore artifact is unavailable or unsafe"
        ) from exc
    finally:
        if stream is not None:
            stream.close()
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _restore_postgres(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    *,
    resumed: bool,
) -> Mapping[str, Any]:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    database_container = _start_database(
        manifest,
        runner,
        resumed=resumed,
    )
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    before = _database_state(manifest, runner)
    if not before.public_empty:
        if resumed and _database_matches(before, manifest.source_database):
            removed = _cleanup_oneoffs(manifest, runner)
            _recheck_runtime_path_identities(
                manifest,
                boundary,
                require_stores=True,
            )
            return {
                "database_container_id": database_container,
                "database_fingerprint_sha256": (
                    before.database_fingerprint_sha256
                ),
                "database_row_count": before.row_count,
                "database_table_count": before.table_count,
                "alembic_revision": before.alembic_revision,
                "restore_recovered_after_crash": True,
                "database_adopted": False,
                "single_transaction_restore": True,
                "zero_oneoff_residue": not removed,
            }
        raise FrozenFinalRestoreWorkerError(
            "nonempty final database is never adopted"
        )
    with _held_artifact(
        manifest.artifacts["database-backup"]
    ) as stream:
        _compose_oneoff(
            manifest,
            runner,
            command=[
                "sh",
                "-ec",
                "exec pg_restore --exit-on-error --single-transaction "
                "--no-owner --no-acl --dbname \"$PGDATABASE\"",
            ],
            timeout=3600,
            stdin=stream,
        )
    after = _database_state(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    if not _database_matches(after, manifest.source_database):
        raise FrozenFinalRestoreWorkerError(
            "restored final database differs from the frozen source"
        )
    removed = _cleanup_oneoffs(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    return {
        "database_container_id": database_container,
        "database_fingerprint_sha256": (
            after.database_fingerprint_sha256
        ),
        "database_row_count": after.row_count,
        "database_table_count": after.table_count,
        "alembic_revision": after.alembic_revision,
        "restore_recovered_after_crash": False,
        "database_adopted": False,
        "single_transaction_restore": True,
        "zero_oneoff_residue": not removed,
    }


def _safe_member_path(name: str) -> tuple[str, tuple[str, ...]]:
    candidate = PurePosixPath(name)
    normalized = candidate.as_posix()
    if (
        not name
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "." in candidate.parts
        or normalized != name
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in name
        )
        or any(len(part.encode("utf-8")) > 255 for part in candidate.parts)
        or len(name.encode("utf-8")) > 4096
    ):
        raise FrozenFinalRestoreWorkerError(
            "restore archive contains an unsafe path"
        )
    return normalized, candidate.parts


def _file_sha256(path: Path) -> tuple[str, int]:
    descriptor = -1
    parent_descriptor = -1
    try:
        parent_descriptor, name = _open_secure_parent(
            path,
            label="restored file",
        )
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise FrozenFinalRestoreWorkerError(
                "restored file is unsafe"
            )
        digest = hashlib.sha256()
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            digest.update(chunk)
        return digest.hexdigest(), consumed
    except FrozenFinalRestoreWorkerError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreWorkerError(
            "restored file is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _restore_archive_resumable(
    binding: ArtifactBinding,
    target: Path,
) -> Mapping[str, Any]:
    _ensure_private_directory(target, create=False)
    observed_members: set[str] = set()
    observed_directories: set[str] = set()
    expanded = 0
    try:
        with _held_artifact(binding) as held:
            with tarfile.open(fileobj=held, mode="r|gz") as archive:
                for member in archive:
                    if len(observed_members) >= MAX_TAR_MEMBERS:
                        raise FrozenFinalRestoreWorkerError(
                            "restore archive member count is invalid"
                        )
                    member_key, parts = _safe_member_path(member.name)
                    parent_key = PurePosixPath(*parts[:-1]).as_posix()
                    if parent_key == ".":
                        parent_key = ""
                    if (
                        member_key in observed_members
                        or (
                            parent_key
                            and parent_key not in observed_directories
                        )
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                        or member.isfifo()
                        or not (member.isdir() or member.isreg())
                        or member.uid != 0
                        or member.gid != 0
                        or member.mtime != 0
                        or stat.S_IMODE(member.mode) & 0o6022
                    ):
                        raise FrozenFinalRestoreWorkerError(
                            "restore archive member is not canonical"
                        )
                    observed_members.add(member_key)
                    destination = target.joinpath(*parts)
                    parent = destination.parent
                    try:
                        parent.relative_to(target)
                    except ValueError as exc:
                        raise FrozenFinalRestoreWorkerError(
                            "restore member escaped its target"
                        ) from exc
                    current = target
                    for part in parts[:-1]:
                        current = current / part
                        _ensure_restore_directory(current, create=False)
                    if member.isdir():
                        _ensure_restore_directory(destination, create=True)
                        opened_directory = _open_secure_directory(
                            destination,
                            label="restored archive directory",
                        )
                        if opened_directory is None:
                            raise FrozenFinalRestoreWorkerError(
                                "restored archive directory is unavailable"
                            )
                        directory_descriptor = opened_directory[0]
                        try:
                            os.fchmod(
                                directory_descriptor,
                                stat.S_IMODE(member.mode),
                            )
                            os.fsync(directory_descriptor)
                        finally:
                            os.close(directory_descriptor)
                        observed_directories.add(member_key)
                        continue
                    expanded += member.size
                    if expanded > MAX_ARTIFACT_BYTES:
                        raise FrozenFinalRestoreWorkerError(
                            "restore archive expands beyond its bound"
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise FrozenFinalRestoreWorkerError(
                            "restore archive file is unreadable"
                        )
                    descriptor = -1
                    parent_descriptor = -1
                    try:
                        parent_descriptor, destination_name = (
                            _open_secure_parent(
                                destination,
                                label="restored archive file",
                            )
                        )
                        flags = (
                            os.O_RDWR
                            | os.O_CREAT
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_NOFOLLOW", 0)
                        )
                        descriptor = os.open(
                            destination_name,
                            flags,
                            0o600,
                            dir_fd=parent_descriptor,
                        )
                        before = os.fstat(descriptor)
                        allowed_mode = (
                            {0o600, stat.S_IMODE(member.mode)}
                            if before.st_size == member.size
                            else {0o600}
                        )
                        if (
                            not stat.S_ISREG(before.st_mode)
                            or before.st_nlink != 1
                            or before.st_uid != 0
                            or before.st_size > member.size
                            or stat.S_IMODE(before.st_mode)
                            not in allowed_mode
                        ):
                            raise FrozenFinalRestoreWorkerError(
                                "partial restore file differs"
                            )
                        existing_bytes = before.st_size
                        expected_digest = hashlib.sha256()
                        consumed = 0
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            if consumed + len(chunk) > member.size:
                                raise FrozenFinalRestoreWorkerError(
                                    "restore archive member is oversized"
                                )
                            expected_digest.update(chunk)
                            overlap = max(
                                0,
                                min(
                                    len(chunk),
                                    existing_bytes - consumed,
                                ),
                            )
                            if overlap:
                                observed = os.pread(
                                    descriptor,
                                    overlap,
                                    consumed,
                                )
                                if observed != chunk[:overlap]:
                                    raise FrozenFinalRestoreWorkerError(
                                        "partial restore file content differs"
                                    )
                            remainder = memoryview(chunk)[overlap:]
                            write_offset = consumed + overlap
                            while remainder:
                                written = os.pwrite(
                                    descriptor,
                                    remainder,
                                    write_offset,
                                )
                                if written <= 0:
                                    raise FrozenFinalRestoreWorkerError(
                                        "restore file write made no progress"
                                    )
                                write_offset += written
                                remainder = remainder[written:]
                            consumed += len(chunk)
                        source.close()
                        if consumed != member.size:
                            raise FrozenFinalRestoreWorkerError(
                                "restore archive member is truncated"
                            )
                        os.ftruncate(descriptor, member.size)
                        os.fchmod(
                            descriptor,
                            stat.S_IMODE(member.mode),
                        )
                        os.fsync(descriptor)
                        after = os.fstat(descriptor)
                        visible = os.stat(
                            destination_name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        if (
                            after.st_size != member.size
                            or stat.S_IMODE(after.st_mode)
                            != stat.S_IMODE(member.mode)
                            or (after.st_dev, after.st_ino)
                            != (visible.st_dev, visible.st_ino)
                        ):
                            raise FrozenFinalRestoreWorkerError(
                                "restored file metadata differs"
                            )
                        observed_sha256, observed_bytes = _file_sha256(
                            destination
                        )
                        if (
                            observed_sha256
                            != expected_digest.hexdigest()
                            or observed_bytes != member.size
                        ):
                            raise FrozenFinalRestoreWorkerError(
                                "partial restore file content differs"
                            )
                    finally:
                        source.close()
                        if descriptor >= 0:
                            os.close(descriptor)
                        if parent_descriptor >= 0:
                            os.close(parent_descriptor)
    except FrozenFinalRestoreWorkerError:
        raise
    except (OSError, EOFError, tarfile.TarError, UnicodeError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "restore archive is invalid or unreadable"
        ) from exc
    if not observed_members:
        raise FrozenFinalRestoreWorkerError(
            "restore archive member count is invalid"
        )
    return {
        "member_count": len(observed_members),
        "expanded_bytes": expanded,
        "resume_safe_member_verification": True,
    }


def _fsync_tree_directories(root: Path) -> None:
    _ensure_private_directory(root, create=False)
    directories: list[Path] = []
    for current, names, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        _ensure_restore_directory(current_path, create=False)
        directories.append(current_path)
        for name in names:
            _ensure_restore_directory(current_path / name, create=False)
        for name in files:
            metadata = (current_path / name).stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != 0
            ):
                raise FrozenFinalRestoreWorkerError(
                    "restored tree contains an unsafe entry"
                )
    for directory in reversed(directories):
        opened = _open_secure_directory(
            directory,
            label="restored directory durability",
        )
        if opened is None:
            raise FrozenFinalRestoreWorkerError(
                "restored directory durability check failed"
            )
        descriptor = opened[0]
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise FrozenFinalRestoreWorkerError(
                "restored directory durability check failed"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _tree_digest(path: Path) -> str:
    descriptor = -1
    try:
        opened = _open_secure_directory(
            path,
            label="restored tree",
        )
        if opened is None:
            raise FrozenFinalRestoreWorkerError(
                "restored tree is unavailable"
            )
        descriptor = opened[0]
        return SOURCE._canonical_tree_digest(descriptor)
    except (OSError, SOURCE.SourceSnapshotError) as exc:
        raise FrozenFinalRestoreWorkerError(
            "restored tree attestation failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _restore_files(
    manifest: RoleManifest,
    *,
    resumed: bool,
) -> Mapping[str, Any]:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    evidence: dict[str, Any] = {}
    for kind, target in (
        ("uploads-archive", manifest.paths.uploads),
        ("audit-archive", manifest.paths.audit),
    ):
        if not resumed and _directory_entries(target):
            raise FrozenFinalRestoreWorkerError(
                f"{kind} target is nonempty before final restore"
            )
        binding = manifest.artifacts[kind]
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
        extraction = _restore_archive_resumable(binding, target)
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
        _fsync_tree_directories(target)
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
        observed = _tree_digest(target)
        _recheck_runtime_path_identities(
            manifest,
            boundary,
            require_stores=True,
        )
        if observed != binding.restored_tree_sha256:
            raise FrozenFinalRestoreWorkerError(
                f"{kind} restored tree differs"
            )
        evidence[kind] = {
            **extraction,
            "restored_tree_sha256": observed,
        }
    if _directory_entries(manifest.paths.redis):
        raise FrozenFinalRestoreWorkerError(
            "final Redis directory is not pristine"
        )
    _fsync_tree_directories(manifest.paths.uploads)
    _fsync_tree_directories(manifest.paths.audit)
    _fsync_tree_directories(manifest.paths.redis)
    return {
        "artifacts": evidence,
        "redis_restore_bytes": 0,
        "redis_pristine": True,
        "no_archive_deleted_or_replaced": True,
    }


def _verify_final_state(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> Mapping[str, Any]:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=True,
    )
    database_container = _verify_database_healthy(manifest, runner)
    database = _database_state(manifest, runner)
    if not _database_matches(database, manifest.source_database):
        raise FrozenFinalRestoreWorkerError(
            "final database differs from frozen source"
        )
    file_trees = {
        "uploads": _tree_digest(manifest.paths.uploads),
        "audit": _tree_digest(manifest.paths.audit),
    }
    expected_trees = {
        "uploads": manifest.artifacts[
            "uploads-archive"
        ].restored_tree_sha256,
        "audit": manifest.artifacts[
            "audit-archive"
        ].restored_tree_sha256,
    }
    if file_trees != expected_trees:
        raise FrozenFinalRestoreWorkerError(
            "final file trees differ from frozen source"
        )
    if _directory_entries(manifest.paths.redis):
        raise FrozenFinalRestoreWorkerError(
            "final Redis directory is not pristine"
        )
    removed = _cleanup_oneoffs(manifest, runner)
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=True,
    )
    containers = _project_container_ids(manifest, runner)
    resource_closure = _preflight_generation_resources(manifest, runner)
    if resource_closure != {
        "container_count": 1,
        "database_count": 1,
        "oneoff_count": 0,
        "network_present": True,
        "named_volume_count": 0,
    }:
        raise FrozenFinalRestoreWorkerError(
            "final generation resource closure differs"
        )
    if len(containers) != 1 or containers[0] != database_container:
        raise FrozenFinalRestoreWorkerError(
            "final generation container closure differs"
        )
    return {
        "database": {
            "alembic_revision": database.alembic_revision,
            "database_fingerprint_sha256": (
                database.database_fingerprint_sha256
            ),
            "row_count": database.row_count,
            "table_count": database.table_count,
        },
        "file_trees": file_trees,
        "database_container_id": database_container,
        "project_container_count": 1,
        "oneoff_cleanup": removed,
        "redis_restore_bytes": 0,
        "redis_pristine": True,
        "public_or_private_app_started": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "nginx_state_required": "legacy-frozen",
        "claim_consume_outcome_required": LIVE_LEASE_SUCCESS_OUTCOME,
        "aggregate_three_role_receipt_required": True,
    }


def _initialize_generation(manifest: RoleManifest) -> Mapping[str, Any]:
    boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=False,
    )
    _ensure_private_directory(
        manifest.paths.data_generation_root,
        create=False,
    )
    _ensure_private_directory(
        manifest.paths.restore_input_root,
        create=False,
    )
    if set(_directory_entries(manifest.paths.restore_input_root)) != {
        "database.dump",
        "uploads.tar.gz",
        "audit.tar.gz",
    }:
        raise FrozenFinalRestoreWorkerError(
            "generation restore-input directory is not exact"
        )
    _ensure_directory_chain(
        manifest.paths.role_data_root,
        existing_parent=manifest.paths.data_generation_root,
    )
    for path in (
        manifest.paths.postgres,
        manifest.paths.redis,
        manifest.paths.uploads,
        manifest.paths.audit,
    ):
        _ensure_directory_chain(
            path,
            existing_parent=manifest.paths.data_generation_root,
        )
    if _directory_entries(manifest.paths.redis):
        raise FrozenFinalRestoreWorkerError(
            "new final Redis directory is not pristine"
        )
    _recheck_runtime_path_identities(
        manifest,
        boundary,
        require_stores=False,
    )
    return {
        "data_generation_root": str(
            manifest.paths.data_generation_root
        ),
        "role_data_root": str(manifest.paths.role_data_root),
        "restore_input_root": str(manifest.paths.restore_input_root),
        "stores": {
            name: str(path)
            for name, path in (
                ("postgres", manifest.paths.postgres),
                ("redis", manifest.paths.redis),
                ("uploads", manifest.paths.uploads),
                ("audit", manifest.paths.audit),
            )
        },
        "redis_restore_bytes": 0,
        "redis_pristine": True,
        "rehearsal_namespace_reused": False,
    }


def _verify_inputs(
    manifest: RoleManifest,
    runner: DockerCommandRunner,
) -> Mapping[str, Any]:
    compose = _verify_role_compose(manifest, runner)
    prepare_inputs = _verify_prepare_inputs(manifest)
    _verify_image(manifest, runner)
    artifact_digests = {}
    for kind, binding in manifest.artifacts.items():
        _verify_root_file_identity(
            binding.path,
            label=kind,
            expected_sha256=binding.sha256,
            expected_bytes=binding.bytes,
        )
        artifact_digests[kind] = binding.sha256
    return {
        "compose": compose,
        "prepare_inputs": prepare_inputs,
        "postgres_image_id": manifest.postgres_image_id,
        "postgres_image_content_identity": (
            manifest.postgres_image_content_identity
        ),
        "app_image_id": manifest.app_image_id,
        "app_image_content_identity": (
            manifest.app_image_content_identity
        ),
        "target_migration_revision": manifest.target_migration_revision,
        "artifact_sha256": dict(sorted(artifact_digests.items())),
        "source_role": manifest.source_role,
        "target_transport": manifest.document["target_transport"],
        "snapshot_claim_is_provenance_only": True,
        "fresh_claim_owner_action": LIVE_LEASE_OWNER_ACTION,
        "static_claim_authoritative": False,
    }


def confirmation_phrase(
    manifest: RoleManifest,
    lease: LeaseBinding,
) -> str:
    return (
        "restore-production-shadow-frozen-final:"
        f"{manifest.operation_id}:{manifest.role}:"
        f"{manifest.restore_generation_sha256}:{lease.sha256}:"
        f"{lease.epoch}"
    )


def _action_semantic(
    action: str,
    manifest: RoleManifest,
    runner: DockerCommandRunner,
    *,
    resumed: bool,
) -> Mapping[str, Any]:
    if action == "verify-inputs":
        return _verify_inputs(manifest, runner)
    if action == "initialize-generation":
        return _initialize_generation(manifest)
    if action == "restore-postgres":
        return _restore_postgres(manifest, runner, resumed=resumed)
    if action == "restore-files":
        return _restore_files(manifest, resumed=resumed)
    if action == "verify-final":
        return _verify_final_state(manifest, runner)
    raise FrozenFinalRestoreWorkerError("restore action is invalid")


def _result_document(
    manifest: RoleManifest,
    lease: LeaseBinding,
    final_evidence_sha256: str,
    final_semantic: Mapping[str, Any],
) -> dict[str, Any]:
    document = {
        "schema": RESULT_SCHEMA,
        "status": "frozen-final-shadow-restored",
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "release_tree_sha": manifest.release_tree_sha,
        "controller_manifest_sha256": (
            manifest.controller_manifest_sha256
        ),
        "installer_receipt_sha256": manifest.installer_receipt_sha256,
        "restore_set_sha256": manifest.restore_set_sha256,
        "restore_generation_sha256": (
            manifest.restore_generation_sha256
        ),
        "source_role": manifest.source_role,
        "live_lease_claim_sha256": lease.sha256,
        "live_lease_claim_epoch": lease.epoch,
        "live_lease_claim_nonce": lease.nonce,
        "legacy_frozen_receipt_sha256": lease.receipt_sha256,
        "database": final_semantic["database"],
        "file_trees": final_semantic["file_trees"],
        "redis_restore_bytes": 0,
        "redis_pristine": True,
        "public_or_private_app_started": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "nginx_state": "legacy-frozen",
        "final_evidence_sha256": final_evidence_sha256,
        "claim_consume_outcome": LIVE_LEASE_SUCCESS_OUTCOME,
        "aggregate_three_role_receipt_required": True,
        "claim_consumed_by_worker": False,
    }
    if (
        set(document) != RESULT_FIELDS
        or not isinstance(document["database"], dict)
        or set(document["database"]) != RESULT_DATABASE_FIELDS
        or not isinstance(document["file_trees"], dict)
        or set(document["file_trees"]) != RESULT_FILE_TREE_FIELDS
        or document["database"]["alembic_revision"]
        != manifest.source_database.alembic_revision
        or document["database"]["database_fingerprint_sha256"]
        != manifest.source_database.database_fingerprint_sha256
        or document["database"]["row_count"]
        != manifest.source_database.row_count
        or document["database"]["table_count"]
        != manifest.source_database.table_count
        or document["file_trees"]["uploads"]
        != manifest.artifacts["uploads-archive"].restored_tree_sha256
        or document["file_trees"]["audit"]
        != manifest.artifacts["audit-archive"].restored_tree_sha256
    ):
        raise FrozenFinalRestoreWorkerError(
            "final restore result differs from its exact source closure"
        )
    return document


def execute(
    *,
    role_manifest_path: Path,
    live_lease_claim_path: Path,
    live_lease_claim_sha256: str,
    live_lease_claim_epoch: int,
    legacy_frozen_receipt_path: Path,
    apply: bool = False,
    confirm: str | None = None,
    runner: DockerCommandRunner | None = None,
    authority_verifier: LiveAuthorityVerifier | None = None,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise FrozenFinalRestoreWorkerError(
            "frozen-final restore worker must run as root"
        )
    manifest = load_role_manifest(role_manifest_path)
    lease = load_live_lease(
        manifest=manifest,
        claim_path=live_lease_claim_path,
        claim_sha256=live_lease_claim_sha256,
        claim_epoch=live_lease_claim_epoch,
        receipt_path=legacy_frozen_receipt_path,
    )
    required = confirmation_phrase(manifest, lease)
    base = {
        "schema": RESULT_SCHEMA,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "restore_set_sha256": manifest.restore_set_sha256,
        "restore_generation_sha256": (
            manifest.restore_generation_sha256
        ),
        "installer_receipt_sha256": manifest.installer_receipt_sha256,
        "live_lease_claim_sha256": lease.sha256,
        "live_lease_claim_epoch": lease.epoch,
        "live_lease_claim_nonce": lease.nonce,
        "legacy_frozen_receipt_sha256": lease.receipt_sha256,
        "required_confirmation": required,
        "plan_only_default": True,
        "static_claim_authoritative": False,
        "controller_live_verifier_required": True,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
    }
    if not apply:
        if confirm is not None:
            raise FrozenFinalRestoreWorkerError(
                "--confirm is valid only with apply"
            )
        return {
            **base,
            "status": "planned",
            "runtime_mutated": False,
            "actions": list(ACTIONS),
            "claim_owner_action": LIVE_LEASE_OWNER_ACTION,
            "claim_consume_outcome": LIVE_LEASE_SUCCESS_OUTCOME,
        }
    if confirm != required:
        raise FrozenFinalRestoreWorkerError(
            f"apply requires --confirm {required}"
        )
    if runner is None:
        runner = SubprocessDockerRunner()
    if authority_verifier is None:
        raise FrozenFinalRestoreWorkerError(
            "apply requires a controller-owned live-authority verifier"
        )
    (
        _bootstrap_authority,
        bootstrap_authority_sha256,
        verification_sequence,
    ) = _authority_verification(
        authority_verifier,
        lease,
        f"before:{manifest.role}:journal-bootstrap",
        previous_sequence=0,
    )
    _ensure_private_directory(
        manifest.paths.secret_generation_root,
        create=False,
    )
    bootstrap_boundary = _capture_runtime_path_identities(
        manifest,
        require_stores=False,
    )
    _ensure_private_directory(manifest.paths.journal, create=True)
    _ensure_private_directory(manifest.paths.evidence, create=True)
    _recheck_runtime_path_identities(
        manifest,
        bootstrap_boundary,
        require_stores=False,
    )
    with _worker_lock(manifest):
        execution_boundary = _capture_runtime_path_identities(
            manifest,
            require_stores=False,
        )
        events, completed, active, evidence = _read_events(
            manifest,
            lease,
        )
        for completed_action in completed:
            _validate_orphan_evidence(
                manifest,
                lease,
                action=completed_action,
                journal_digest=evidence[completed_action],
            )
        final_semantic: Mapping[str, Any] | None = None
        completed_readback: Mapping[str, Any] | None = None
        if completed == list(ACTIONS):
            _recheck_runtime_path_identities(
                manifest,
                execution_boundary,
                require_stores=True,
            )
            _before, before_sha256, verification_sequence = (
                _authority_verification(
                    authority_verifier,
                    lease,
                    f"before:{manifest.role}:completed-readback",
                    previous_sequence=verification_sequence,
                )
            )
            current_final = _verify_final_state(manifest, runner)
            _recheck_runtime_path_identities(
                manifest,
                execution_boundary,
                require_stores=True,
            )
            _after, after_sha256, verification_sequence = (
                _authority_verification(
                    authority_verifier,
                    lease,
                    f"after:{manifest.role}:completed-readback",
                    previous_sequence=verification_sequence,
                )
            )
            _validate_orphan_evidence(
                manifest,
                lease,
                action="verify-final",
                journal_digest=evidence["verify-final"],
            )
            recorded = _load_action_evidence(
                manifest,
                lease,
                action="verify-final",
                digest=evidence["verify-final"],
            )["semantic"]
            for field in (
                "database",
                "file_trees",
                "redis_restore_bytes",
                "redis_pristine",
            ):
                if current_final[field] != recorded[field]:
                    raise FrozenFinalRestoreWorkerError(
                        "completed restore readback differs from evidence"
                    )
            final_semantic = current_final
            completed_readback = {
                "authority_before_sha256": before_sha256,
                "authority_after_sha256": after_sha256,
                "final_state_reverified": True,
            }
        for action in ACTIONS[len(completed) :]:
            if active is not None and active != action:
                raise FrozenFinalRestoreWorkerError(
                    "restore journal active action differs"
                )
            attempt = (
                max(
                    (
                        int(event["attempt"])
                        for event in events
                        if event["action"] == action
                    ),
                    default=0,
                )
                + 1
            )
            before, before_sha256, verification_sequence = (
                _authority_verification(
                    authority_verifier,
                    lease,
                    f"before:{manifest.role}:{action}",
                    previous_sequence=verification_sequence,
                )
            )
            stores_required = (
                "initialize-generation" in completed
                or action not in {"verify-inputs", "initialize-generation"}
            )
            _recheck_runtime_path_identities(
                manifest,
                execution_boundary,
                require_stores=stores_required,
            )
            _append_event(
                manifest,
                lease,
                events,
                kind="resumed" if active == action else "started",
                action=action,
                attempt=attempt,
                evidence_sha256=None,
                authority_verification_sha256=before_sha256,
            )
            semantic = dict(
                _action_semantic(
                    action,
                    manifest,
                    runner,
                    resumed=active == action,
                )
            )
            if action == "initialize-generation":
                execution_boundary = _capture_runtime_path_identities(
                    manifest,
                    require_stores=True,
                )
            else:
                _recheck_runtime_path_identities(
                    manifest,
                    execution_boundary,
                    require_stores=stores_required,
                )
            after, after_sha256, verification_sequence = (
                _authority_verification(
                    authority_verifier,
                    lease,
                    f"after:{manifest.role}:{action}",
                    previous_sequence=verification_sequence,
                )
            )
            semantic["authority_before_sha256"] = before_sha256
            semantic["authority_after_sha256"] = after_sha256
            semantic["authority_before_sequence"] = before[
                "verification_sequence"
            ]
            semantic["authority_after_sequence"] = after[
                "verification_sequence"
            ]
            _evidence, evidence_sha256 = _publish_evidence(
                manifest,
                lease,
                action,
                semantic,
            )
            _recheck_runtime_path_identities(
                manifest,
                execution_boundary,
                require_stores=(
                    action == "initialize-generation" or stores_required
                ),
            )
            _append_event(
                manifest,
                lease,
                events,
                kind="completed",
                action=action,
                attempt=attempt,
                evidence_sha256=evidence_sha256,
                authority_verification_sha256=None,
            )
            _recheck_runtime_path_identities(
                manifest,
                execution_boundary,
                require_stores=(
                    action == "initialize-generation" or stores_required
                ),
            )
            completed.append(action)
            evidence[action] = evidence_sha256
            active = None
            if action == "verify-final":
                final_semantic = semantic
        if completed != list(ACTIONS):
            raise FrozenFinalRestoreWorkerError(
                "restore journal did not complete every action"
            )
        if final_semantic is None:
            _validate_orphan_evidence(
                manifest,
                lease,
                action="verify-final",
                journal_digest=evidence["verify-final"],
            )
            final_document = _load_action_evidence(
                manifest,
                lease,
                action="verify-final",
                digest=evidence["verify-final"],
            )
            final_semantic = final_document["semantic"]
        result = _result_document(
            manifest,
            lease,
            evidence["verify-final"],
            final_semantic,
        )
        result_payload = _canonical_json(result) + b"\n"
        result_sha256 = _sha256(result_payload.rstrip(b"\n"))
        result_path = (
            manifest.paths.evidence
            / f"restore-result-{result_sha256}.json"
        )
        if result_path.exists() or result_path.is_symlink():
            if (
                _read_root_file(
                    result_path,
                    label="existing restore result",
                    maximum=MAX_JSON_BYTES,
                )
                != result_payload
            ):
                raise FrozenFinalRestoreWorkerError(
                    "existing restore result differs"
                )
            publication = "reused"
        else:
            boundary = _capture_runtime_path_identities(
                manifest,
                require_stores=True,
            )
            _write_secure_new_file(
                manifest.paths.evidence,
                result_path.name,
                result_payload,
                label="frozen-final restore result",
            )
            _recheck_runtime_path_identities(
                manifest,
                boundary,
                require_stores=True,
            )
            publication = "created"
    return {
        **base,
        "status": "restored",
        "runtime_mutated": True,
        "completed_actions": list(ACTIONS),
        "action_evidence_sha256": dict(evidence),
        "result": result,
        "result_sha256": result_sha256,
        "result_path": str(result_path),
        "result_publication": publication,
        "bootstrap_authority_sha256": bootstrap_authority_sha256,
        "completed_readback": completed_readback,
        "claim_consumed": False,
        "aggregate_three_role_receipt_required": True,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-manifest", type=Path, required=True)
    parser.add_argument("--live-lease-claim", type=Path, required=True)
    parser.add_argument("--live-lease-claim-sha256", required=True)
    parser.add_argument("--live-lease-claim-epoch", type=int, required=True)
    parser.add_argument("--legacy-frozen-receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.apply:
            raise FrozenFinalRestoreWorkerError(
                "standalone apply is disabled; use the controller "
                "orchestrator with a live-authority verifier"
            )
        result = execute(
            role_manifest_path=args.role_manifest,
            live_lease_claim_path=args.live_lease_claim,
            live_lease_claim_sha256=args.live_lease_claim_sha256,
            live_lease_claim_epoch=args.live_lease_claim_epoch,
            legacy_frozen_receipt_path=args.legacy_frozen_receipt,
            apply=False,
            confirm=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except FrozenFinalRestoreWorkerError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "runtime_mutated": False,
                    "current_mutated": False,
                    "legacy_mutated": False,
                    "object_storage_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
