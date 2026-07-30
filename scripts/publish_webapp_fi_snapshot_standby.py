#!/usr/bin/env python3
"""Capture and publish one fenced WebApp-FI snapshot for WA-IR.

This is a source-host orchestration wrapper for the production 2c08 standby
path.  It creates the PostgreSQL, uploads, and required audit artifacts with
the existing local read-only capture tool, then invokes the local immutable
Object Storage publisher.  It deliberately has no SSH, SCP, rsync, peer HTTP,
or WA-IR command path.  The publisher is the only component that contacts
Object Storage.

Every invocation takes a non-blocking root-only lock.  It never stops a
container, changes application data, starts a remote command, or deletes
local artifacts or Object Storage objects.  Failed local artifacts are left in
place for explicit operator review; there is intentionally no retention or
cleanup policy in this wrapper.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.standby_snapshot_capacity import (
    SnapshotCapacityError,
    age_ciphertext_reservation_bytes,
    manifest_workspace_reservation_bytes,
    require_capacity,
)

DEFAULT_CAPTURE_SCRIPT = REPO_ROOT / "scripts/create_webapp_fi_snapshot_artifacts.py"
DEFAULT_TRANSPORT_SCRIPT = REPO_ROOT / "scripts/manage_webapp_ir_snapshot.py"

SCHEMA_VERSION = "webapp_fi_snapshot_publication_v1"
TRANSPORT_SCHEMA = "gold-trade-snapshot-transport-v1"
SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir"
MAXIMUM_SOURCE_DB_CLIENT_LIFETIME_SECONDS = 300

ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
GENERATION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")


class SourceSnapshotPublishError(RuntimeError):
    """Raised when the local publication contract is not satisfied."""


@dataclass(frozen=True)
class TransportSettings:
    config_path: Path
    workspace: Path
    maximum_snapshot_age_seconds: int
    maximum_database_bytes: int
    maximum_uploads_bytes: int
    maximum_audit_bytes: int
    minimum_free_bytes: int


@dataclass(frozen=True)
class SourceConfig:
    source_env: Path
    data_root: Path
    state_root: Path
    capture_env_file: Path
    transport: TransportSettings
    release_sha: str
    alembic_revision: str
    db_container: str
    app_container: str
    capture_attempts: int


def _require_absolute(value: str, *, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise SourceSnapshotPublishError(f"{field} must be an absolute path")
    return path


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    """Reject symlinked or writable ancestors before trusting a root-only path."""

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise SourceSnapshotPublishError(f"{field} ancestor does not exist") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SourceSnapshotPublishError(f"{field} has an unsafe ancestor")
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise SourceSnapshotPublishError(f"{field} ancestor is not root-controlled")


def require_root_only_file(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SourceSnapshotPublishError(f"{field} must be an absolute path")
    _require_safe_ancestors(path.parent, field=field)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SourceSnapshotPublishError(f"{field} does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SourceSnapshotPublishError(f"{field} must be a regular non-symlink file")
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SourceSnapshotPublishError(f"{field} must be root-only")
    return path.resolve(strict=True)


def require_root_only_directory(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SourceSnapshotPublishError(f"{field} must be an absolute path")
    _require_safe_ancestors(path.parent, field=field)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SourceSnapshotPublishError(f"{field} does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SourceSnapshotPublishError(f"{field} must be an existing non-symlink directory")
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SourceSnapshotPublishError(f"{field} must be root-only")
    return path.resolve(strict=True)


def require_tool_file(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SourceSnapshotPublishError(f"{field} must be an absolute path")
    _require_safe_ancestors(path.parent, field=field)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SourceSnapshotPublishError(f"{field} does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SourceSnapshotPublishError(f"{field} must be a regular non-symlink file")
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise SourceSnapshotPublishError(f"{field} must be root-owned and not group/world writable")
    return path.resolve(strict=True)


def require_child_path(path: Path, parent: Path, *, field: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(parent)
    except (FileNotFoundError, ValueError) as exc:
        raise SourceSnapshotPublishError(f"{field} must remain below the configured data root") from exc
    return resolved


def parse_root_only_env(path: Path) -> dict[str, str]:
    source_env = require_root_only_file(path, field="source snapshot env")
    values: dict[str, str] = {}
    try:
        lines = source_env.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SourceSnapshotPublishError("source snapshot env cannot be read") from exc
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SourceSnapshotPublishError(f"source snapshot env line {number} is not KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise SourceSnapshotPublishError(f"source snapshot env line {number} has an invalid key")
        if key in values:
            raise SourceSnapshotPublishError(f"source snapshot env line {number} repeats {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def require_config(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise SourceSnapshotPublishError(f"source snapshot env is missing {key}")
    return value


def require_int(value: str, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise SourceSnapshotPublishError(f"{field} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise SourceSnapshotPublishError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def require_json_int(value: object, *, field: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SourceSnapshotPublishError(f"source transport {field} must be an integer >= {minimum}")
    return value


def require_transport_settings(path: Path, *, data_root: Path) -> TransportSettings:
    config_path = require_root_only_file(path, field="source transport config")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceSnapshotPublishError("source transport config is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema") != TRANSPORT_SCHEMA:
        raise SourceSnapshotPublishError("source transport config schema is unsupported")
    maximum_age = raw.get("maximum_snapshot_age_seconds")
    if isinstance(maximum_age, bool) or not isinstance(maximum_age, int) or not 15 <= maximum_age <= 30:
        raise SourceSnapshotPublishError("source transport maximum_snapshot_age_seconds must be between 15 and 30")
    maximum_database = require_json_int(raw.get("maximum_database_bytes"), field="maximum_database_bytes", minimum=1)
    maximum_uploads = require_json_int(raw.get("maximum_uploads_bytes"), field="maximum_uploads_bytes", minimum=1)
    maximum_audit = require_json_int(raw.get("maximum_audit_bytes"), field="maximum_audit_bytes", minimum=1)
    minimum_free = require_json_int(raw.get("minimum_free_bytes"), field="minimum_free_bytes", minimum=0)
    if raw.get("local_artifact_retention", "preserve") != "preserve":
        raise SourceSnapshotPublishError("source transport local_artifact_retention must be preserve")
    age_recipient = raw.get("age_recipient")
    if not isinstance(age_recipient, str) or not AGE_RECIPIENT_RE.fullmatch(age_recipient):
        raise SourceSnapshotPublishError("source transport must contain the WA-IR age recipient")
    if raw.get("age_identity_file") is not None:
        raise SourceSnapshotPublishError("source transport must not contain a destination age identity")
    if raw.get("signing_source_site") != SOURCE_SITE:
        raise SourceSnapshotPublishError("source transport signing_source_site must be webapp_fi")
    if raw.get("source_signing_public_key_base64") is not None:
        raise SourceSnapshotPublishError("source transport must not contain a destination verification key")
    private_key_value = raw.get("source_signing_private_key_file")
    if not isinstance(private_key_value, str):
        raise SourceSnapshotPublishError("source transport must contain a WebApp-FI signing key path")
    private_key = require_root_only_file(
        _require_absolute(private_key_value, field="source_signing_private_key_file"),
        field="source signing private key",
    )
    if private_key.stat().st_size != 32:
        raise SourceSnapshotPublishError("source signing private key must contain exactly 32 raw Ed25519 bytes")
    credentials_value = raw.get("credentials_file")
    if not isinstance(credentials_value, str):
        raise SourceSnapshotPublishError("source transport must contain a credentials_file")
    require_root_only_file(
        _require_absolute(credentials_value, field="credentials_file"),
        field="source Object Storage credentials",
    )
    workspace_value = raw.get("workspace")
    if not isinstance(workspace_value, str):
        raise SourceSnapshotPublishError("source transport must contain a workspace")
    workspace = require_root_only_directory(
        _require_absolute(workspace_value, field="workspace"), field="source transport workspace"
    )
    require_child_path(workspace, data_root, field="source transport workspace")
    age_binary = raw.get("age_binary", "/usr/bin/age")
    if not isinstance(age_binary, str) or not os.path.isabs(age_binary) or not os.access(age_binary, os.X_OK):
        raise SourceSnapshotPublishError("source transport age_binary must be an executable absolute path")
    return TransportSettings(
        config_path=config_path,
        workspace=workspace,
        maximum_snapshot_age_seconds=maximum_age,
        maximum_database_bytes=maximum_database,
        maximum_uploads_bytes=maximum_uploads,
        maximum_audit_bytes=maximum_audit,
        minimum_free_bytes=minimum_free,
    )


def load_source_config(path: Path) -> SourceConfig:
    source_env = require_root_only_file(path, field="source snapshot env")
    values = parse_root_only_env(source_env)
    data_root = require_root_only_directory(
        _require_absolute(require_config(values, "WA_FI_SNAPSHOT_DATA_ROOT"), field="WA_FI_SNAPSHOT_DATA_ROOT"),
        field="WA_FI_SNAPSHOT_DATA_ROOT",
    )
    state_root = require_root_only_directory(
        _require_absolute(require_config(values, "WA_FI_SNAPSHOT_STATE_ROOT"), field="WA_FI_SNAPSHOT_STATE_ROOT"),
        field="WA_FI_SNAPSHOT_STATE_ROOT",
    )
    require_child_path(state_root, data_root, field="WA_FI_SNAPSHOT_STATE_ROOT")
    capture_env_file = require_root_only_file(
        _require_absolute(require_config(values, "WA_FI_SNAPSHOT_CAPTURE_ENV_FILE"), field="WA_FI_SNAPSHOT_CAPTURE_ENV_FILE"),
        field="WA_FI_SNAPSHOT_CAPTURE_ENV_FILE",
    )
    transport = require_transport_settings(
        _require_absolute(require_config(values, "WA_FI_SNAPSHOT_TRANSPORT_CONFIG"), field="WA_FI_SNAPSHOT_TRANSPORT_CONFIG"),
        data_root=data_root,
    )
    configured_maximum_age = require_int(
        require_config(values, "WA_FI_SNAPSHOT_MAX_AGE_SECONDS"),
        field="WA_FI_SNAPSHOT_MAX_AGE_SECONDS",
        minimum=15,
        maximum=30,
    )
    if configured_maximum_age != transport.maximum_snapshot_age_seconds:
        raise SourceSnapshotPublishError("source timer and transport snapshot freshness bounds must match")
    release_sha = require_config(values, "RELEASE_SHA").lower()
    if not RELEASE_RE.fullmatch(release_sha):
        raise SourceSnapshotPublishError("source snapshot RELEASE_SHA is invalid")
    alembic_revision = require_config(values, "EXPECTED_ALEMBIC_REVISION").lower()
    if not ALEMBIC_RE.fullmatch(alembic_revision):
        raise SourceSnapshotPublishError("source snapshot EXPECTED_ALEMBIC_REVISION is invalid")
    db_container = require_config(values, "WA_FI_SNAPSHOT_DB_CONTAINER")
    app_container = require_config(values, "WA_FI_SNAPSHOT_APP_CONTAINER")
    if not CONTAINER_RE.fullmatch(db_container) or not CONTAINER_RE.fullmatch(app_container):
        raise SourceSnapshotPublishError("source snapshot Docker container name is invalid")
    attempts = require_int(
        values.get("WA_FI_SNAPSHOT_CAPTURE_ATTEMPTS", "1"),
        field="WA_FI_SNAPSHOT_CAPTURE_ATTEMPTS",
        minimum=1,
        maximum=5,
    )
    return SourceConfig(
        source_env=source_env,
        data_root=data_root,
        state_root=state_root,
        capture_env_file=capture_env_file,
        transport=transport,
        release_sha=release_sha,
        alembic_revision=alembic_revision,
        db_container=db_container,
        app_container=app_container,
        capture_attempts=attempts,
    )


def default_generation() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("snapshot-%Y%m%dt%H%M%Sz")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def require_generation(value: str) -> str:
    generation = value.strip().lower()
    if not GENERATION_RE.fullmatch(generation):
        raise SourceSnapshotPublishError("snapshot generation is invalid")
    return generation


def source_capacity_requirement(config: SourceConfig) -> int:
    """Reserve source archives, concurrent ciphertexts, and one read-back."""

    plaintext_sizes = (
        config.transport.maximum_database_bytes,
        config.transport.maximum_uploads_bytes,
        config.transport.maximum_audit_bytes,
    )
    ciphertext_sizes = [age_ciphertext_reservation_bytes(size) for size in plaintext_sizes]
    return (
        sum(plaintext_sizes)
        + sum(ciphertext_sizes)
        + max(ciphertext_sizes)
        + manifest_workspace_reservation_bytes()
    )


def require_source_capacity(config: SourceConfig) -> dict[str, Any]:
    try:
        return require_capacity(
            config.data_root,
            required_new_bytes=source_capacity_requirement(config),
            minimum_free_bytes=config.transport.minimum_free_bytes,
            label="WebApp-FI snapshot source data root",
        )
    except SnapshotCapacityError as exc:
        raise SourceSnapshotPublishError(str(exc)) from exc


def child_environment() -> dict[str, str]:
    """Pass no interactive-shell secrets to either local child process."""

    allowed = ("PATH", "LANG", "LC_ALL", "TZ")
    environment = {key: value for key in allowed if (value := os.environ.get(key))}
    environment.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    environment.setdefault("TZ", "UTC")
    return environment


def parse_json_output(result: subprocess.CompletedProcess[str], *, label: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise SourceSnapshotPublishError(f"{label} failed with exit {result.returncode}")
    for line in reversed(result.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise SourceSnapshotPublishError(f"{label} did not return JSON")


def run_json_command(arguments: Sequence[str], *, label: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(item) for item in arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=child_environment(),
            timeout=1200,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceSnapshotPublishError(f"{label} could not start") from exc
    return parse_json_output(result, label=label)


@contextlib.contextmanager
def publication_lock(state_root: Path) -> Iterator[None]:
    lock_path = state_root / "source-publish.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(lock_path), flags, 0o600)
    except OSError as exc:
        raise SourceSnapshotPublishError("cannot safely open source snapshot publication lock") from exc
    try:
        current = lock_path.lstat()
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise SourceSnapshotPublishError("source snapshot publication lock is not root-only")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceSnapshotPublishError("another WebApp-FI snapshot publication is already active") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _result_string(payload: Mapping[str, Any], field: str, *, label: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise SourceSnapshotPublishError(f"{label} omitted {field}")
    return value


def _artifact_path(
    payload: Mapping[str, Any], *, field: str, expected: Path, artifact_root: Path
) -> Path:
    descriptor = payload.get(field)
    if not isinstance(descriptor, Mapping):
        raise SourceSnapshotPublishError(f"source capture omitted {field}")
    value = descriptor.get("path")
    if not isinstance(value, str):
        raise SourceSnapshotPublishError(f"source capture omitted {field}.path")
    path = require_root_only_file(_require_absolute(value, field=f"{field}.path"), field=f"{field} artifact")
    if path != expected or require_child_path(path, artifact_root, field=f"{field} artifact") != path:
        raise SourceSnapshotPublishError(f"source capture returned an unexpected {field} path")
    return path


def validate_capture_result(
    payload: Mapping[str, Any], *, config: SourceConfig, generation: str
) -> dict[str, Any]:
    if payload.get("status") != "ready":
        raise SourceSnapshotPublishError("source artifact capture did not reach ready state")
    for field, expected in (
        ("source_site", SOURCE_SITE),
        ("destination_site", DESTINATION_SITE),
        ("snapshot_id", generation),
        ("release_sha", config.release_sha),
        ("alembic_revision", config.alembic_revision),
    ):
        if payload.get(field) != expected:
            raise SourceSnapshotPublishError(f"source artifact capture returned an unexpected {field}")
    if payload.get("audit_included") is not True:
        raise SourceSnapshotPublishError("source artifact capture must include audit_trail")
    artifact_dir = require_root_only_directory(
        _require_absolute(_result_string(payload, "artifact_dir", label="source artifact capture"), field="artifact_dir"),
        field="source artifact directory",
    )
    expected_directory = (config.data_root / "snapshots" / generation).resolve(strict=True)
    if artifact_dir != expected_directory:
        raise SourceSnapshotPublishError("source artifact capture returned an unexpected artifact directory")
    manifest_path = require_root_only_file(
        _require_absolute(_result_string(payload, "manifest_path", label="source artifact capture"), field="manifest_path"),
        field="source artifact manifest",
    )
    if manifest_path != artifact_dir / "snapshot-artifacts.json":
        raise SourceSnapshotPublishError("source artifact capture returned an unexpected manifest path")
    database = _artifact_path(
        payload, field="database", expected=artifact_dir / "database.dump", artifact_root=artifact_dir
    )
    uploads = _artifact_path(
        payload, field="uploads", expected=artifact_dir / "uploads.tar.gz", artifact_root=artifact_dir
    )
    audit = _artifact_path(
        payload, field="audit", expected=artifact_dir / "audit.tar.gz", artifact_root=artifact_dir
    )
    started_at = _result_string(payload, "source_db_snapshot_started_at", label="source artifact capture")
    completed_at = _result_string(payload, "source_capture_completed_at", label="source artifact capture")
    database_capture = payload.get("source_database_capture")
    if not isinstance(database_capture, Mapping) or database_capture.get("client_mode") != "short_lived_read_only":
        raise SourceSnapshotPublishError("source artifact capture database mode is invalid")
    lifetime = database_capture.get("client_lifetime_seconds")
    if isinstance(lifetime, bool) or not isinstance(lifetime, int) or not 1 <= lifetime <= MAXIMUM_SOURCE_DB_CLIENT_LIFETIME_SECONDS:
        raise SourceSnapshotPublishError("source artifact capture database client lifetime is invalid")
    volume_capture = payload.get("source_volume_capture")
    if not isinstance(volume_capture, Mapping) or volume_capture.get("mode") != "read_only_no_mutation":
        raise SourceSnapshotPublishError("source artifact capture volume mode is invalid")
    return {
        "artifact_directory": artifact_dir,
        "manifest_path": manifest_path,
        "database": database,
        "uploads": uploads,
        "audit": audit,
        "source_db_snapshot_started_at": started_at,
        "source_capture_completed_at": completed_at,
        "source_db_client_lifetime_seconds": lifetime,
    }


def validate_publish_result(
    payload: Mapping[str, Any], *, config: SourceConfig, generation: str, capture: Mapping[str, Any]
) -> None:
    if payload.get("status") != "published":
        raise SourceSnapshotPublishError("immutable source snapshot publication did not reach published state")
    for field, expected in (
        ("source_site", SOURCE_SITE),
        ("destination_site", DESTINATION_SITE),
        ("source_generation", generation),
        ("release_sha", config.release_sha),
        ("alembic_revision", config.alembic_revision),
        ("source_db_snapshot_started_at", capture["source_db_snapshot_started_at"]),
        ("source_capture_completed_at", capture["source_capture_completed_at"]),
    ):
        if payload.get(field) != expected:
            raise SourceSnapshotPublishError(f"immutable source snapshot publication returned an unexpected {field}")
    for field in ("snapshot_id", "published_at"):
        _result_string(payload, field, label="immutable source snapshot publication")
    if payload.get("source_database_capture") != {
        "client_mode": "short_lived_read_only",
        "client_lifetime_seconds": capture["source_db_client_lifetime_seconds"],
    }:
        raise SourceSnapshotPublishError("immutable source snapshot publication database capture proof differs")
    if payload.get("source_volume_capture") != {"mode": "read_only_no_mutation"}:
        raise SourceSnapshotPublishError("immutable source snapshot publication volume capture proof differs")
    for field in ("database", "uploads", "audit", "manifest"):
        descriptor = payload.get(field)
        if not isinstance(descriptor, Mapping):
            raise SourceSnapshotPublishError(f"immutable source snapshot publication omitted {field}")
        for required in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes"):
            value = descriptor.get(required)
            if required == "ciphertext_bytes":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise SourceSnapshotPublishError(
                        f"immutable source snapshot publication {field} descriptor is invalid"
                    )
            elif not isinstance(value, str) or not value:
                raise SourceSnapshotPublishError(f"immutable source snapshot publication {field} descriptor is invalid")


def ensure_receipt_directory(state_root: Path) -> Path:
    directory = state_root / "published"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    return require_root_only_directory(directory, field="source publication receipt directory")


def write_new_receipt(directory: Path, *, generation: str, payload: Mapping[str, Any]) -> Path:
    target = directory / f"{generation}.json"
    temporary = directory / f".{generation}.{uuid.uuid4().hex}.tmp"
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(temporary), flags, 0o600)
    except OSError as exc:
        raise SourceSnapshotPublishError("cannot create source publication receipt") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise SourceSnapshotPublishError("refusing to overwrite an existing source publication receipt") from exc
        finally:
            temporary.unlink(missing_ok=True)
        directory_descriptor = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return require_root_only_file(target, field="source publication receipt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", required=True)
    parser.add_argument("--capture-script", default=str(DEFAULT_CAPTURE_SCRIPT))
    parser.add_argument("--transport-script", default=str(DEFAULT_TRANSPORT_SCRIPT))
    parser.add_argument("--capture-python", default=sys.executable)
    parser.add_argument("--transport-python", default=sys.executable)
    parser.add_argument("--generation", default=None)
    parser.add_argument("--timer-interval-seconds", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    source_env = _require_absolute(args.source_env, field="source-env")
    config = load_source_config(source_env)
    if not 15 <= args.timer_interval_seconds <= config.transport.maximum_snapshot_age_seconds:
        raise SourceSnapshotPublishError("timer-interval-seconds must be between 15 and the snapshot freshness bound")
    capture_script = require_tool_file(_require_absolute(args.capture_script, field="capture-script"), field="capture-script")
    transport_script = require_tool_file(
        _require_absolute(args.transport_script, field="transport-script"), field="transport-script"
    )
    for executable, field in ((args.capture_python, "capture-python"), (args.transport_python, "transport-python")):
        if not os.path.isabs(executable) or not os.access(executable, os.X_OK):
            raise SourceSnapshotPublishError(f"{field} must be an executable absolute path")
    generation = require_generation(args.generation or default_generation())
    planned_artifact_directory = config.data_root / "snapshots" / generation
    if planned_artifact_directory.exists():
        raise SourceSnapshotPublishError("refusing to reuse an existing source snapshot generation")
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "planned" if not args.apply else "running",
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "generation": generation,
        "release_sha": config.release_sha,
        "alembic_revision": config.alembic_revision,
        "timer_interval_seconds": args.timer_interval_seconds,
        "maximum_snapshot_age_seconds": config.transport.maximum_snapshot_age_seconds,
        "capacity_requirement_bytes": source_capacity_requirement(config),
        "local_artifact_retention": "preserve",
        "artifact_directory": str(planned_artifact_directory),
        "object_storage_transport": "private_versioned_age_only",
        "direct_fi_to_ir_transfer": False,
        "remote_execution": "none",
        "services_stopped": False,
        "source_data_mutated": False,
        "automatic_deletion": False,
    }
    if not args.apply:
        return plan
    with publication_lock(config.state_root):
        capacity = require_source_capacity(config)
        capture = run_json_command(
            [
                args.capture_python,
                str(capture_script),
                "--output-root",
                str(config.data_root),
                "--release-sha",
                config.release_sha,
                "--alembic-revision",
                config.alembic_revision,
                "--generation",
                generation,
                "--db-container",
                config.db_container,
                "--app-container",
                config.app_container,
                "--db-capture-env",
                str(config.capture_env_file),
                "--include-audit",
                "--attempts",
                str(config.capture_attempts),
                "--max-database-bytes",
                str(config.transport.maximum_database_bytes),
                "--max-upload-bytes",
                str(config.transport.maximum_uploads_bytes),
                "--max-audit-bytes",
                str(config.transport.maximum_audit_bytes),
                "--minimum-free-bytes",
                str(config.transport.minimum_free_bytes),
                "--apply",
                "--json",
            ],
            label="local source artifact capture",
        )
        validated_capture = validate_capture_result(capture, config=config, generation=generation)
        published = run_json_command(
            [
                args.transport_python,
                str(transport_script),
                "publish",
                "--config",
                str(config.transport.config_path),
                "--database-dump",
                str(validated_capture["database"]),
                "--uploads-archive",
                str(validated_capture["uploads"]),
                "--audit-archive",
                str(validated_capture["audit"]),
                "--source-site",
                SOURCE_SITE,
                "--destination-site",
                DESTINATION_SITE,
                "--generation",
                generation,
                "--release-sha",
                config.release_sha,
                "--alembic-revision",
                config.alembic_revision,
                "--source-db-snapshot-started-at",
                str(validated_capture["source_db_snapshot_started_at"]),
                "--source-capture-completed-at",
                str(validated_capture["source_capture_completed_at"]),
                "--source-db-client-mode",
                "short_lived_read_only",
                "--source-db-client-lifetime-seconds",
                str(validated_capture["source_db_client_lifetime_seconds"]),
                "--source-volume-capture-mode",
                "read_only_no_mutation",
            ],
            label="local immutable Object Storage publisher",
        )
        validate_publish_result(published, config=config, generation=generation, capture=validated_capture)
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "status": "published",
            "source_site": SOURCE_SITE,
            "destination_site": DESTINATION_SITE,
            "generation": generation,
            "release_sha": config.release_sha,
            "alembic_revision": config.alembic_revision,
            "capture": {
                "artifact_directory": str(validated_capture["artifact_directory"]),
                "source_db_snapshot_started_at": validated_capture["source_db_snapshot_started_at"],
                "source_capture_completed_at": validated_capture["source_capture_completed_at"],
                "source_db_client_lifetime_seconds": validated_capture["source_db_client_lifetime_seconds"],
            },
            "capacity_preflight": capacity,
            "transport": published,
            "object_storage_transport": "private_versioned_age_only",
            "direct_fi_to_ir_transfer": False,
            "remote_execution": "none",
            "services_stopped": False,
            "source_data_mutated": False,
            "automatic_deletion": False,
        }
        receipt_path = write_new_receipt(ensure_receipt_directory(config.state_root), generation=generation, payload=receipt)
    return {
        **plan,
        "status": "published",
        "capture": receipt["capture"],
        "capacity_preflight": capacity,
        "transport": published,
        "receipt_path": str(receipt_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = execute(args)
    except (SourceSnapshotPublishError, OSError) as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": str(exc),
            "direct_fi_to_ir_transfer": False,
            "remote_execution": "none",
            "services_stopped": False,
            "source_data_mutated": False,
            "automatic_deletion": False,
        }
        exit_code = 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    else:
        print(f"WebApp-FI snapshot publication: {payload['status']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
