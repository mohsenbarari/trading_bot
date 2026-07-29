#!/usr/bin/env python3
"""Restore a verified Object Storage snapshot into a fenced WA-IR candidate.

This is deliberately a host-side restore primitive, not a promotion command.
It accepts only the receipt emitted after the generic snapshot consumer has
downloaded, age-decrypted, and verified an immutable Object Storage snapshot.
The application, direct sync worker, migrations, Nginx, and public routing are
outside this command's surface.

Every restore uses new, generation-qualified Docker volumes backed by the
configured standby data mount.  The previous known-good candidate is retained
for rollback and is never overwritten or deleted by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = (
    REPO_ROOT / "deploy/production/docker-compose.webapp-ir-snapshot-standby-2c08.yml"
)
SCHEMA_VERSION = "gold-trade-snapshot-restore-receipt-v1"
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GENERATION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VOLUME_LABEL = "com.goldtrade.webapp-ir.snapshot"
VOLUME_PREFIX = "trading_bot_wa_ir_"
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024


class RestoreError(RuntimeError):
    """A restore precondition failed before an unsafe change was made."""


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    byte_count: int
    format: str


@dataclass(frozen=True)
class SnapshotReceipt:
    snapshot_id: str
    source_site: str
    destination_site: str
    source_generation: str
    release_sha: str
    alembic_revision: str
    source_db_snapshot_started_at: str
    source_capture_completed_at: str
    published_at: str
    ready_at: str
    source_db_snapshot_started_at_value: datetime
    source_capture_completed_at_value: datetime
    published_at_value: datetime
    ready_at_value: datetime
    staged_candidate_directory: Path
    database: Artifact
    uploads: Artifact
    raw: dict[str, Any]
    receipt_sha256: str


@dataclass(frozen=True)
class Candidate:
    generation: str
    db_volume: str
    uploads_volume: str
    db_container: str
    compose_project: str
    root: Path
    db_path: Path
    uploads_path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now_value() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc_timestamp(value: str, *, label: str) -> datetime:
    """Parse a mandatory RFC3339 UTC timestamp without accepting local time."""

    if not value.endswith("Z"):
        raise RestoreError(f"{label} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RestoreError(f"{label} is not a valid RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RestoreError(f"{label} must use UTC")
    return parsed.astimezone(timezone.utc)


def require_snapshot_maximum_age(values: Mapping[str, str]) -> int:
    raw = require_config(values, "WA_IR_SNAPSHOT_MAX_AGE_SECONDS")
    try:
        maximum_age = int(raw, 10)
    except ValueError as exc:
        raise RestoreError("WA_IR_SNAPSHOT_MAX_AGE_SECONDS must be an integer") from exc
    if not 15 <= maximum_age <= 30:
        raise RestoreError("WA_IR_SNAPSHOT_MAX_AGE_SECONDS must be between 15 and 30")
    return maximum_age


def receipt_snapshot_age_seconds(receipt: SnapshotReceipt, *, now: datetime | None = None) -> float:
    current = now or utc_now_value()
    age = (current - receipt.source_db_snapshot_started_at_value).total_seconds()
    if age < 0:
        raise RestoreError("snapshot database-start timestamp is in the future")
    return age


def require_receipt_freshness(
    receipt: SnapshotReceipt,
    *,
    maximum_age_seconds: int,
    now: datetime | None = None,
) -> float:
    age = receipt_snapshot_age_seconds(receipt, now=now)
    if age > maximum_age_seconds:
        raise RestoreError("snapshot is older than WA_IR_SNAPSHOT_MAX_AGE_SECONDS from database snapshot start")
    return age


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def require_secure_regular_file(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RestoreError(f"{label} does not exist") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise RestoreError(f"{label} must be a regular non-symlink file")
    if metadata.st_uid != 0:
        raise RestoreError(f"{label} must be root-owned")
    if metadata.st_mode & 0o077:
        raise RestoreError(f"{label} must not be group- or world-readable")


def parse_env_file(path: Path, *, label: str) -> dict[str, str]:
    require_secure_regular_file(path, label=label)
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RestoreError(f"{label} line {number} is not KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise RestoreError(f"{label} line {number} has an invalid key")
        if key in values:
            raise RestoreError(f"{label} line {number} repeats {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def require_text(mapping: Mapping[str, Any], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RestoreError(f"{label} must contain non-empty {key}")
    return value.strip()


def artifact_from_payload(
    payload: Mapping[str, Any],
    *,
    label: str,
    workspace_root: Path,
) -> Artifact:
    raw_path = require_text(payload, "path", label=label)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise RestoreError(f"{label}.path must be absolute")
    require_secure_regular_file(candidate, label=label)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise RestoreError(f"{label}.path must stay under the snapshot workspace") from exc
    expected_sha = require_text(payload, "sha256", label=label).lower()
    if not SHA256_RE.fullmatch(expected_sha):
        raise RestoreError(f"{label}.sha256 must be lowercase SHA-256")
    raw_size = payload.get("bytes")
    if not isinstance(raw_size, int) or raw_size < 1:
        raise RestoreError(f"{label}.bytes must be a positive integer")
    artifact_format = require_text(payload, "format", label=label)
    actual_sha, actual_size = sha256_file(resolved)
    if actual_sha != expected_sha or actual_size != raw_size:
        raise RestoreError(f"{label} hash or size does not match its verified receipt")
    return Artifact(
        path=resolved,
        sha256=actual_sha,
        byte_count=actual_size,
        format=artifact_format,
    )


def load_receipt(path: Path, *, workspace_root: Path) -> SnapshotReceipt:
    require_secure_regular_file(path, label="snapshot-ready receipt")
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise RestoreError("snapshot-ready receipt is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RestoreError("snapshot-ready receipt must be a JSON object")
    embedded_receipt_sha = require_text(payload, "receipt_sha256", label="snapshot-ready receipt").lower()
    if not SHA256_RE.fullmatch(embedded_receipt_sha):
        raise RestoreError("snapshot-ready receipt receipt_sha256 is invalid")
    canonical_payload = dict(payload)
    canonical_payload.pop("receipt_sha256", None)
    canonical_receipt_sha = hashlib.sha256(
        json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    if canonical_receipt_sha != embedded_receipt_sha:
        raise RestoreError("snapshot-ready receipt canonical hash does not match")
    if payload.get("status") != "ready":
        raise RestoreError("snapshot-ready receipt status must be ready")
    if payload.get("schema") != "gold-trade-snapshot-ready-v1":
        raise RestoreError("snapshot-ready receipt schema is unsupported")
    receipt_path = path.resolve(strict=True)
    staged_candidate_directory = require_absolute_directory(
        str(receipt_path.parent), label="snapshot-ready candidate directory"
    )
    try:
        staged_candidate_directory.relative_to(workspace_root)
    except ValueError as exc:
        raise RestoreError("snapshot-ready candidate directory escaped the snapshot workspace") from exc
    if staged_candidate_directory == workspace_root:
        raise RestoreError("snapshot-ready receipt must be inside an immutable candidate directory")
    declared_candidate_directory = Path(
        require_text(payload, "candidate_directory", label="snapshot-ready receipt")
    )
    if not declared_candidate_directory.is_absolute():
        raise RestoreError("snapshot-ready receipt candidate_directory must be absolute")
    if declared_candidate_directory.resolve(strict=True) != staged_candidate_directory:
        raise RestoreError("snapshot-ready receipt candidate_directory does not bind the receipt location")
    source_site = require_text(payload, "source_site", label="snapshot-ready receipt")
    destination_site = require_text(payload, "destination_site", label="snapshot-ready receipt")
    if source_site != "webapp_fi" or destination_site != "webapp_ir":
        raise RestoreError("snapshot-ready receipt has an unexpected site direction")
    source_generation = require_text(payload, "source_generation", label="snapshot-ready receipt").lower()
    if not GENERATION_RE.fullmatch(source_generation):
        raise RestoreError("snapshot-ready receipt source_generation is not a safe generation")
    source_db_snapshot_started_at = require_text(
        payload, "source_db_snapshot_started_at", label="snapshot-ready receipt"
    )
    source_capture_completed_at = require_text(
        payload, "source_capture_completed_at", label="snapshot-ready receipt"
    )
    published_at = require_text(payload, "published_at", label="snapshot-ready receipt")
    ready_at = require_text(payload, "ready_at", label="snapshot-ready receipt")
    source_db_snapshot_started_at_value = parse_utc_timestamp(
        source_db_snapshot_started_at, label="snapshot-ready receipt source_db_snapshot_started_at"
    )
    source_capture_completed_at_value = parse_utc_timestamp(
        source_capture_completed_at, label="snapshot-ready receipt source_capture_completed_at"
    )
    published_at_value = parse_utc_timestamp(published_at, label="snapshot-ready receipt published_at")
    ready_at_value = parse_utc_timestamp(ready_at, label="snapshot-ready receipt ready_at")
    if source_capture_completed_at_value < source_db_snapshot_started_at_value:
        raise RestoreError("snapshot-ready receipt capture completion precedes database snapshot start")
    if published_at_value < source_capture_completed_at_value:
        raise RestoreError("snapshot-ready receipt publication precedes source capture completion")
    if ready_at_value < published_at_value:
        raise RestoreError("snapshot-ready receipt readiness precedes publication")
    source_database_capture = payload.get("source_database_capture")
    if not isinstance(source_database_capture, dict):
        raise RestoreError("snapshot-ready receipt must describe its source database capture")
    if source_database_capture.get("client_mode") != "short_lived_read_only":
        raise RestoreError("snapshot-ready receipt source database capture is not read-only")
    lifetime = source_database_capture.get("client_lifetime_seconds")
    if isinstance(lifetime, bool) or not isinstance(lifetime, int) or not 1 <= lifetime <= 300:
        raise RestoreError("snapshot-ready receipt source database capture lifetime is invalid")
    source_volume_capture = payload.get("source_volume_capture")
    if not isinstance(source_volume_capture, dict) or source_volume_capture.get("mode") != "read_only_no_mutation":
        raise RestoreError("snapshot-ready receipt source uploads capture is not read-only")
    release_sha = require_text(payload, "release_sha", label="snapshot-ready receipt").lower()
    revision = require_text(payload, "alembic_revision", label="snapshot-ready receipt").lower()
    if not RELEASE_RE.fullmatch(release_sha):
        raise RestoreError("snapshot-ready receipt release_sha is invalid")
    if not ALEMBIC_RE.fullmatch(revision):
        raise RestoreError("snapshot-ready receipt alembic_revision is invalid")
    snapshot_id = require_text(payload, "snapshot_id", label="snapshot-ready receipt").lower()
    if not GENERATION_RE.fullmatch(snapshot_id):
        raise RestoreError("snapshot-ready receipt snapshot_id is not a safe generation")
    database_payload = payload.get("database")
    uploads_payload = payload.get("uploads")
    if not isinstance(database_payload, dict) or not isinstance(uploads_payload, dict):
        raise RestoreError("snapshot-ready receipt must include database and uploads artifacts")
    database_payload = dict(database_payload)
    uploads_payload = dict(uploads_payload)
    database_payload["path"] = require_text(payload, "database_dump_path", label="snapshot-ready receipt")
    uploads_payload["path"] = require_text(payload, "uploads_archive_path", label="snapshot-ready receipt")
    database = artifact_from_payload(database_payload, label="database artifact", workspace_root=workspace_root)
    uploads = artifact_from_payload(uploads_payload, label="uploads artifact", workspace_root=workspace_root)
    if database.format != "pg_dump_custom":
        raise RestoreError("database artifact must use pg_dump_custom format")
    if uploads.format != "tar_gz_uploads_root":
        raise RestoreError("uploads artifact must use tar_gz_uploads_root format")
    with database.path.open("rb") as database_handle:
        database_magic = database_handle.read(5)
    if database_magic != b"PGDMP":
        raise RestoreError("database artifact is not a PostgreSQL custom dump")
    return SnapshotReceipt(
        snapshot_id=snapshot_id,
        source_site=source_site,
        destination_site=destination_site,
        source_generation=source_generation,
        release_sha=release_sha,
        alembic_revision=revision,
        source_db_snapshot_started_at=source_db_snapshot_started_at,
        source_capture_completed_at=source_capture_completed_at,
        published_at=published_at,
        ready_at=ready_at,
        source_db_snapshot_started_at_value=source_db_snapshot_started_at_value,
        source_capture_completed_at_value=source_capture_completed_at_value,
        published_at_value=published_at_value,
        ready_at_value=ready_at_value,
        staged_candidate_directory=staged_candidate_directory,
        database=database,
        uploads=uploads,
        raw=payload,
        receipt_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def validate_upload_archive(path: Path, *, max_uncompressed_bytes: int) -> tuple[int, int]:
    member_count = 0
    total_bytes = 0
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise RestoreError("uploads artifact is not a readable gzip tar archive") from exc
    with archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not name.parts:
                raise RestoreError("uploads archive contains an unsafe member path")
            if name.parts[0] != "uploads":
                raise RestoreError("uploads archive must be rooted at uploads/")
            if not (member.isdir() or member.isreg()):
                raise RestoreError("uploads archive may not contain links or special files")
            if member.isreg():
                member_count += 1
                total_bytes += member.size
                if total_bytes > max_uncompressed_bytes:
                    raise RestoreError("uploads archive exceeds the configured uncompressed size limit")
    return member_count, total_bytes


def extract_upload_archive(path: Path, destination: Path) -> tuple[int, int]:
    """Extract a prevalidated uploads/ archive without tarfile.extractall()."""

    member_count = 0
    total_bytes = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            target_parts = relative.parts[1:]
            if not target_parts:
                continue
            target = destination.joinpath(*target_parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, member.mode & 0o777)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RestoreError("uploads archive member could not be opened")
            with source, target.open("xb") as handle:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
            os.chmod(target, member.mode & 0o777)
            member_count += 1
            total_bytes += member.size
    return member_count, total_bytes


def require_absolute_directory(path_value: str, *, label: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        raise RestoreError(f"{label} must be an absolute path")
    if path.is_symlink() or not path.is_dir():
        raise RestoreError(f"{label} must be an existing non-symlink directory")
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise RestoreError(f"{label} must be root-only")
    return path.resolve(strict=True)


def require_config(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise RestoreError(f"standby env is missing {key}")
    return value


def build_candidate(data_root: Path, generation: str) -> Candidate:
    if not GENERATION_RE.fullmatch(generation):
        raise RestoreError("generation must be lowercase alphanumeric with internal hyphens")
    root = data_root / "candidates" / generation
    return Candidate(
        generation=generation,
        db_volume=f"{VOLUME_PREFIX}pg_{generation}",
        uploads_volume=f"{VOLUME_PREFIX}uploads_{generation}",
        db_container=f"trading_bot_wa_ir_snapshot_db_{generation}",
        compose_project=f"trading_bot_wa_ir_snapshot_{generation}",
        root=root,
        db_path=root / "postgresql",
        uploads_path=root / "uploads",
    )


class DockerRunner:
    def __init__(self, *, execute: bool, environment: Mapping[str, str]) -> None:
        self.execute = execute
        self.environment = dict(environment)
        self.commands: list[list[str]] = []

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int = 180,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> subprocess.CompletedProcess[str] | None:
        command = [str(item) for item in arguments]
        self.commands.append(command)
        if not self.execute:
            return None
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=self.environment,
            timeout=timeout,
            check=False,
        )
        if result.returncode not in allowed_returncodes:
            # Docker/Compose stderr can contain expanded configuration values.
            # Keep only the operation and exit code in machine-readable evidence.
            raise RestoreError(f"Docker command failed ({command[0]} {command[1] if len(command) > 1 else ''}, exit {result.returncode})")
        return result


def docker_volume_absent(runner: DockerRunner, name: str) -> bool:
    result = runner.run(
        ["docker", "volume", "inspect", name],
        allowed_returncodes=frozenset({0, 1}),
    )
    return result is None or result.returncode == 1


def docker_container_absent(runner: DockerRunner, name: str) -> bool:
    result = runner.run(
        ["docker", "container", "inspect", name],
        allowed_returncodes=frozenset({0, 1}),
    )
    return result is None or result.returncode == 1


def create_bound_volume(runner: DockerRunner, *, name: str, device: Path, generation: str) -> None:
    runner.run(
        [
            "docker",
            "volume",
            "create",
            "--driver",
            "local",
            "--label",
            f"{VOLUME_LABEL}=true",
            "--label",
            f"com.goldtrade.webapp-ir.generation={generation}",
            "--opt",
            "type=none",
            "--opt",
            f"device={device}",
            "--opt",
            "o=bind",
            name,
        ]
    )


def wait_for_database(runner: DockerRunner, *, container: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = runner.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            timeout=15,
            allowed_returncodes=frozenset({0, 1}),
        )
        if result is not None and result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        time.sleep(2)
    raise RestoreError("candidate PostgreSQL container did not become healthy")


def restore_database(runner: DockerRunner, *, candidate: Candidate, dump: Path) -> tuple[str, int]:
    container_path = "/tmp/webapp-ir-snapshot.dump"
    runner.run(["docker", "cp", str(dump), f"{candidate.db_container}:{container_path}"], timeout=300)
    try:
        runner.run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                candidate.db_container,
                "sh",
                "-ec",
                "pg_restore --exit-on-error --clean --if-exists --no-owner --no-privileges "
                '-U "$POSTGRES_USER" -d "$POSTGRES_DB" ' + container_path,
            ],
            timeout=900,
        )
        revision_result = runner.run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                candidate.db_container,
                "sh",
                "-ec",
                'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
                '"SELECT version_num FROM alembic_version LIMIT 1;"',
            ],
            timeout=60,
        )
        table_result = runner.run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                candidate.db_container,
                "sh",
                "-ec",
                'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
                '"SELECT count(*) FROM information_schema.tables WHERE table_schema = \'public\';"',
            ],
            timeout=60,
        )
    finally:
        runner.run(
            ["docker", "exec", candidate.db_container, "sh", "-ec", f"rm -f {container_path}"],
            allowed_returncodes=frozenset({0, 1}),
        )
    if revision_result is None or table_result is None:
        return "", 0
    revision = revision_result.stdout.strip()
    try:
        table_count = int(table_result.stdout.strip())
    except ValueError as exc:
        raise RestoreError("candidate database table-count probe returned invalid output") from exc
    return revision, table_count


def write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def canonical_payload_sha256(payload: Mapping[str, Any], *, omit: str) -> str:
    canonical = dict(payload)
    canonical.pop(omit, None)
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a new root-only marker without replacing a prior marker."""

    if path.exists() or path.is_symlink():
        raise RestoreError(f"refusing to overwrite existing {path.name}")
    encoded = (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RestoreError(f"refusing to overwrite existing {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def build_restore_marker(*, receipt: SnapshotReceipt, candidate: Candidate) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "status": "restored_verified",
        "source_site": receipt.source_site,
        "destination_site": receipt.destination_site,
        "source_generation": receipt.source_generation,
        "snapshot_id": receipt.snapshot_id,
        "release_sha": receipt.release_sha,
        "alembic_revision": receipt.alembic_revision,
        "source_db_snapshot_started_at": receipt.source_db_snapshot_started_at,
        "source_capture_completed_at": receipt.source_capture_completed_at,
        "published_at": receipt.published_at,
        "ready_at": receipt.ready_at,
        "ready_receipt_sha256": require_text(
            receipt.raw, "receipt_sha256", label="snapshot-ready receipt"
        ).lower(),
        "ready_receipt_file_sha256": receipt.receipt_sha256,
        "candidate": {
            "generation": candidate.generation,
            "db_volume": candidate.db_volume,
            "uploads_volume": candidate.uploads_volume,
            "db_container": candidate.db_container,
            "compose_project": candidate.compose_project,
        },
        "restored_at": utc_now(),
        "active_pointer_state": "active",
    }
    marker["receipt_sha256"] = canonical_payload_sha256(marker, omit="receipt_sha256")
    return marker


def mark_previous_transport_candidate_inactive(
    previous: Mapping[str, Any] | None,
    *,
    workspace_root: Path,
) -> str:
    """Mark only a fully bound prior restore marker inactive after pointer swap.

    Any malformed or unexpected state remains untouched.  The transport then
    preserves it instead of treating it as disposable.
    """

    if not previous:
        return "none"
    transport = previous.get("snapshot_transport")
    if not isinstance(transport, Mapping):
        return "retained_unknown"
    raw_directory = transport.get("candidate_directory")
    if not isinstance(raw_directory, str) or not raw_directory:
        return "retained_unknown"
    try:
        directory = require_absolute_directory(raw_directory, label="previous snapshot transport candidate directory")
        directory.relative_to(workspace_root)
    except (RestoreError, ValueError):
        return "retained_unknown"
    marker_path = directory / "snapshot-restore.json"
    try:
        require_secure_regular_file(marker_path, label="previous snapshot restore marker")
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, RestoreError):
        return "retained_unknown"
    if not isinstance(payload, dict):
        return "retained_unknown"
    embedded_sha = payload.get("receipt_sha256")
    if not isinstance(embedded_sha, str) or not SHA256_RE.fullmatch(embedded_sha.lower()):
        return "retained_unknown"
    if canonical_payload_sha256(payload, omit="receipt_sha256") != embedded_sha.lower():
        return "retained_unknown"
    expected = {
        "schema": SCHEMA_VERSION,
        "status": "restored_verified",
        "source_site": previous.get("source_site"),
        "destination_site": previous.get("destination_site"),
        "source_generation": previous.get("source_generation"),
        "snapshot_id": previous.get("snapshot_id"),
        "release_sha": previous.get("release_sha"),
        "alembic_revision": previous.get("alembic_revision"),
        "source_db_snapshot_started_at": previous.get("source_db_snapshot_started_at"),
        "source_capture_completed_at": previous.get("source_capture_completed_at"),
        "ready_receipt_sha256": transport.get("ready_receipt_sha256"),
        "active_pointer_state": "active",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return "retained_unknown"
    payload["active_pointer_state"] = "inactive"
    payload["deactivated_at"] = utc_now()
    payload["receipt_sha256"] = canonical_payload_sha256(payload, omit="receipt_sha256")
    write_atomic_json(marker_path, payload)
    return "inactive"


def previous_candidate(state_root: Path) -> dict[str, Any] | None:
    pointer = state_root / "active-snapshot.json"
    if not pointer.exists():
        return None
    require_secure_regular_file(pointer, label="active snapshot pointer")
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RestoreError("active snapshot pointer is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RestoreError("active snapshot pointer must be an object")
    return payload


def retire_previous_candidate(runner: DockerRunner, previous: Mapping[str, Any] | None) -> None:
    if not previous:
        return
    candidate = previous.get("candidate")
    if not isinstance(candidate, Mapping):
        return
    container = candidate.get("db_container")
    if not isinstance(container, str) or not container.startswith("trading_bot_wa_ir_snapshot_db_"):
        return
    result = runner.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.goldtrade.webapp-ir.snapshot" }}',
            container,
        ],
        allowed_returncodes=frozenset({0, 1}),
    )
    if result is not None and result.returncode == 0 and result.stdout.strip() == "true":
        runner.run(["docker", "stop", "--time", "30", container], allowed_returncodes=frozenset({0, 1}))


def candidate_payload(
    *,
    receipt: SnapshotReceipt,
    candidate: Candidate,
    table_count: int,
    upload_members: int,
    upload_bytes: int,
    maximum_snapshot_age_seconds: int,
    source_db_snapshot_age_seconds: float,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "created_at": utc_now(),
        "release_sha": receipt.release_sha,
        "alembic_revision": receipt.alembic_revision,
        "source_site": receipt.source_site,
        "destination_site": receipt.destination_site,
        "source_generation": receipt.source_generation,
        "snapshot_id": receipt.snapshot_id,
        "ready_receipt_sha256": require_text(
            receipt.raw, "receipt_sha256", label="snapshot-ready receipt"
        ).lower(),
        "ready_receipt_file_sha256": receipt.receipt_sha256,
        "source_db_snapshot_started_at": receipt.source_db_snapshot_started_at,
        "source_capture_completed_at": receipt.source_capture_completed_at,
        "published_at": receipt.published_at,
        "ready_at": receipt.ready_at,
        "freshness": {
            "maximum_snapshot_age_seconds": maximum_snapshot_age_seconds,
            "source_db_snapshot_age_seconds": round(source_db_snapshot_age_seconds, 3),
            "measured_from": "source_db_snapshot_started_at",
        },
        "source_database_capture": dict(receipt.raw["source_database_capture"]),
        "source_volume_capture": dict(receipt.raw["source_volume_capture"]),
        "candidate": {
            "generation": candidate.generation,
            "db_volume": candidate.db_volume,
            "uploads_volume": candidate.uploads_volume,
            "db_container": candidate.db_container,
            "compose_project": candidate.compose_project,
        },
        "database": {
            "sha256": receipt.database.sha256,
            "bytes": receipt.database.byte_count,
            "format": receipt.database.format,
            "table_count": table_count,
        },
        "uploads": {
            "sha256": receipt.uploads.sha256,
            "bytes": receipt.uploads.byte_count,
            "format": receipt.uploads.format,
            "member_count": upload_members,
            "uncompressed_bytes": upload_bytes,
        },
        "object_storage": {
            "manifest": dict(receipt.raw.get("manifest") or {}),
            "database": {
                key: receipt.raw["database"].get(key)
                for key in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes")
            },
            "uploads": {
                key: receipt.raw["uploads"].get(key)
                for key in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes")
            },
        },
        "snapshot_transport": {
            "candidate_directory": str(receipt.staged_candidate_directory),
            "ready_receipt_sha256": require_text(
                receipt.raw, "receipt_sha256", label="snapshot-ready receipt"
            ).lower(),
            "restore_marker_path": str(receipt.staged_candidate_directory / "snapshot-restore.json"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standby-env", required=True, help="root-only standby env based on the 2c08 template")
    parser.add_argument("--receipt", required=True, help="verified snapshot-ready.json from the age/S3 consumer")
    parser.add_argument("--compose-file", default=str(DEFAULT_COMPOSE_FILE))
    parser.add_argument("--generation", help="optional safe generation; defaults to snapshot_id")
    parser.add_argument("--apply", action="store_true", help="create and restore a new candidate; default is validation-only")
    parser.add_argument("--keep-previous-running", action="store_true", help="do not stop the prior labelled candidate DB")
    parser.add_argument("--database-timeout-seconds", type=int, default=120)
    parser.add_argument("--max-upload-bytes", type=int, default=DEFAULT_MAX_UPLOAD_BYTES)
    parser.add_argument("--json", action="store_true")
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    standby_env_path = Path(args.standby_env)
    values = parse_env_file(standby_env_path, label="standby env")
    release_sha = require_config(values, "RELEASE_SHA").lower()
    expected_revision = require_config(values, "EXPECTED_ALEMBIC_REVISION").lower()
    if not RELEASE_RE.fullmatch(release_sha):
        raise RestoreError("standby env RELEASE_SHA is invalid")
    if not ALEMBIC_RE.fullmatch(expected_revision):
        raise RestoreError("standby env EXPECTED_ALEMBIC_REVISION is invalid")
    maximum_snapshot_age_seconds = require_snapshot_maximum_age(values)
    data_root = require_absolute_directory(
        require_config(values, "WA_IR_STANDBY_DATA_ROOT"), label="WA_IR_STANDBY_DATA_ROOT"
    )
    workspace_root = require_absolute_directory(
        require_config(values, "WA_IR_SNAPSHOT_WORK_ROOT"), label="WA_IR_SNAPSHOT_WORK_ROOT"
    )
    state_root = require_absolute_directory(
        require_config(values, "WA_IR_SNAPSHOT_STATE_ROOT"), label="WA_IR_SNAPSHOT_STATE_ROOT"
    )
    for location, label in ((workspace_root, "WA_IR_SNAPSHOT_WORK_ROOT"), (state_root, "WA_IR_SNAPSHOT_STATE_ROOT")):
        try:
            location.relative_to(data_root)
        except ValueError as exc:
            raise RestoreError(f"{label} must be below WA_IR_STANDBY_DATA_ROOT") from exc
    if args.max_upload_bytes < 1:
        raise RestoreError("max-upload-bytes must be positive")
    receipt = load_receipt(Path(args.receipt), workspace_root=workspace_root)
    if receipt.release_sha != release_sha:
        raise RestoreError("receipt release_sha does not match the pinned standby release")
    if receipt.alembic_revision != expected_revision:
        raise RestoreError("receipt alembic revision does not match the pinned production schema")
    initial_snapshot_age_seconds = require_receipt_freshness(
        receipt, maximum_age_seconds=maximum_snapshot_age_seconds
    )
    upload_members, upload_bytes = validate_upload_archive(
        receipt.uploads.path, max_uncompressed_bytes=args.max_upload_bytes
    )
    generation = (args.generation or receipt.snapshot_id).lower()
    candidate = build_candidate(data_root, generation)
    if candidate.root.exists():
        raise RestoreError("candidate generation already exists; choose a new immutable generation")
    database_env = parse_env_file(
        Path(require_config(values, "WA_IR_STANDBY_DATABASE_ENV_FILE")),
        label="standby database env",
    )
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        if not database_env.get(key):
            raise RestoreError(f"standby database env is missing {key}")
    postgres_image = require_config(values, "WA_IR_POSTGRES_IMAGE")
    compose_file = Path(args.compose_file).resolve()
    if compose_file != DEFAULT_COMPOSE_FILE.resolve():
        raise RestoreError("only the pinned WA-IR snapshot compose file is accepted")
    if not compose_file.is_file():
        raise RestoreError("snapshot compose file is missing")
    if args.database_timeout_seconds < 15:
        raise RestoreError("database-timeout-seconds must be at least 15")
    compose_environment = dict(os.environ)
    compose_environment.update(values)
    compose_environment.update(database_env)
    compose_environment.update(
        {
            "COMPOSE_PROJECT_NAME": candidate.compose_project,
            "WA_IR_SNAPSHOT_DB_CONTAINER": candidate.db_container,
            "WA_IR_CANDIDATE_DB_VOLUME": candidate.db_volume,
            "RELEASE_SHA": release_sha,
        }
    )
    runner = DockerRunner(execute=bool(args.apply), environment=compose_environment)
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "planned" if not args.apply else "running",
        "created_at": utc_now(),
        "release_sha": release_sha,
        "alembic_revision": expected_revision,
        "snapshot_id": receipt.snapshot_id,
        "ready_receipt_sha256": require_text(
            receipt.raw, "receipt_sha256", label="snapshot-ready receipt"
        ).lower(),
        "ready_receipt_file_sha256": receipt.receipt_sha256,
        "source_db_snapshot_started_at": receipt.source_db_snapshot_started_at,
        "source_capture_completed_at": receipt.source_capture_completed_at,
        "maximum_snapshot_age_seconds": maximum_snapshot_age_seconds,
        "source_db_snapshot_age_seconds": round(initial_snapshot_age_seconds, 3),
        "freshness_measured_from": "source_db_snapshot_started_at",
        "candidate": {
            "generation": candidate.generation,
            "db_volume": candidate.db_volume,
            "uploads_volume": candidate.uploads_volume,
            "db_container": candidate.db_container,
            "compose_project": candidate.compose_project,
        },
        "validated_upload_members": upload_members,
        "validated_upload_uncompressed_bytes": upload_bytes,
        "app_started": False,
        "direct_sync_started": False,
        "migration_started": False,
        "public_routing_changed": False,
    }
    if not args.apply:
        return plan

    if not docker_volume_absent(runner, candidate.db_volume) or not docker_volume_absent(runner, candidate.uploads_volume):
        raise RestoreError("candidate volume already exists")
    if not docker_container_absent(runner, candidate.db_container):
        raise RestoreError("candidate database container already exists")
    previous = previous_candidate(state_root)
    candidate.root.mkdir(parents=True, mode=0o700)
    candidate.db_path.mkdir(mode=0o700)
    candidate.uploads_path.mkdir(mode=0o700)
    create_bound_volume(runner, name=candidate.db_volume, device=candidate.db_path, generation=candidate.generation)
    create_bound_volume(runner, name=candidate.uploads_volume, device=candidate.uploads_path, generation=candidate.generation)
    extracted_members, extracted_bytes = extract_upload_archive(receipt.uploads.path, candidate.uploads_path)
    if (extracted_members, extracted_bytes) != (upload_members, upload_bytes):
        raise RestoreError("uploads extraction did not match its prevalidated manifest")
    runner.run(["docker", "image", "inspect", postgres_image])
    runner.run(["docker", "compose", "version"])
    runner.run(
        [
            "docker",
            "compose",
            "--project-name",
            candidate.compose_project,
            "-f",
            str(compose_file),
            "config",
            "--quiet",
        ]
    )
    runner.run(
        [
            "docker",
            "compose",
            "--project-name",
            candidate.compose_project,
            "-f",
            str(compose_file),
            "up",
            "--detach",
            "--pull",
            "never",
            "snapshot_db",
        ],
        timeout=180,
    )
    wait_for_database(runner, container=candidate.db_container, timeout_seconds=args.database_timeout_seconds)
    restored_revision, table_count = restore_database(runner, candidate=candidate, dump=receipt.database.path)
    if restored_revision != expected_revision:
        raise RestoreError("restored Alembic revision does not match the pinned production schema")
    evidence = candidate_payload(
        receipt=receipt,
        candidate=candidate,
        table_count=table_count,
        upload_members=extracted_members,
        upload_bytes=extracted_bytes,
        maximum_snapshot_age_seconds=maximum_snapshot_age_seconds,
        source_db_snapshot_age_seconds=require_receipt_freshness(
            receipt, maximum_age_seconds=maximum_snapshot_age_seconds
        ),
    )
    evidence["status"] = "ready"
    evidence["completed_at"] = utc_now()
    write_atomic_json(state_root / f"restore-{candidate.generation}.json", evidence)
    # A large restore can consume the entire freshness budget.  Never advance
    # the active pointer unless the actual DB snapshot start is still in-bound.
    active_snapshot_age_seconds = require_receipt_freshness(
        receipt, maximum_age_seconds=maximum_snapshot_age_seconds
    )
    evidence["freshness"]["source_db_snapshot_age_seconds"] = round(active_snapshot_age_seconds, 3)
    evidence["completed_at"] = utc_now()
    write_atomic_json(state_root / "active-snapshot.json", evidence)
    marker = build_restore_marker(receipt=receipt, candidate=candidate)
    marker_path = receipt.staged_candidate_directory / "snapshot-restore.json"
    try:
        write_new_json(marker_path, marker)
    except (OSError, RestoreError) as exc:
        # The candidate is active and verified; a missing local retention marker
        # causes the transport to retain it as unknown rather than delete it.
        evidence["snapshot_transport"]["restore_marker_status"] = "unwritten_retained_unknown"
        evidence["snapshot_transport"]["restore_marker_error"] = str(exc)
    else:
        evidence["snapshot_transport"]["restore_marker_status"] = "active"
        evidence["previous_transport_marker_state"] = mark_previous_transport_candidate_inactive(
            previous, workspace_root=workspace_root
        )
    if not args.keep_previous_running:
        retire_previous_candidate(runner, previous)
    evidence["commands"] = [[item for item in command] for command in runner.commands]
    return evidence


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = execute(args)
    except (RestoreError, OSError, subprocess.TimeoutExpired) as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": str(exc),
            "app_started": False,
            "direct_sync_started": False,
            "migration_started": False,
            "public_routing_changed": False,
        }
        exit_code = 1
    else:
        exit_code = 0
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    else:
        print(f"WA-IR snapshot restore: {payload['status']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
