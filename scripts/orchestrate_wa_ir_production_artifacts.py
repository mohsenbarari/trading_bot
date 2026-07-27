#!/usr/bin/env python3
"""Publish and deliver one exact WA-IR production operation through Arvan.

Payload bytes flow only from the controller to a private/versioned Arvan object
and from that exact object version to WA-IR.  SSH carries a bounded descriptor
or a fixed operation command; it never carries a release, image, backup,
runtime file, receiver source, or other payload bytes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping
import zipfile

from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from scripts.receive_wa_ir_production_artifact import (
    ATTESTATION_SCHEMA as RECEIVE_ATTESTATION_SCHEMA,
    DESCRIPTOR_SCHEMA,
    parse_descriptor,
)
from scripts.produce_wa_ir_source_database_attestation import (
    ATTESTATION_SCHEMA as SOURCE_DATABASE_ATTESTATION_SCHEMA,
)
from scripts.wa_ir_production_object_storage_transport import (
    EphemeralPresignedGet,
    PublishedObject,
    _journal_ciphertext_path,
    _load_journal,
    _publication_lock,
    build_client,
    load_secure_credentials,
    presign_exact_get,
    publish_age_encrypted,
)
from scripts.wa_ir_production_operation import (
    ATTESTATION_SCHEMA as OPERATION_ATTESTATION_SCHEMA,
    EXPECTED_ARTIFACTS,
    OperationManifest,
    ProductionOperationError,
    _load_manifest_bytes,
    _materialize_release_bundle,
)
from scripts.wa_ir_production_transport_contract import (
    MAX_PAYLOAD_BYTES,
    PRODUCTION_BUCKET,
    ProductionTransportError,
    SHA256_RE,
    TRANSPORT_SCHEMA,
    validate_object_key_binding,
    validate_operation_id,
)


ORCHESTRATOR_SCHEMA = "wa-ir-production-artifact-orchestrator-v1"
ORCHESTRATOR_JOURNAL_SCHEMA = "wa-ir-production-orchestrator-journal-v1"
BOOTSTRAP_CONTROL_SCHEMA = "wa-ir-production-bootstrap-control-v1"
BOOTSTRAP_ATTESTATION_SCHEMA = "wa-ir-production-bootstrap-attestation-v1"
BOOTSTRAP_ARTIFACT_KIND = "receiver-bootstrap"
BOOTSTRAP_DESTINATION_NAME = "wa-ir-production-agent.pyz"
REMOTE_OPERATIONS_ROOT = Path("/srv/trading-bot/dark-standby/operations")
WA_IR_HOST = "95.38.164.29"
WA_IR_USER = "root"
WA_IR_PORT = 22
DEFAULT_PREFIX = "dark-standby/production-operation"
MAX_CONTROL_BYTES = 256 * 1024
MAX_ATTESTATION_BYTES = 256 * 1024
MAX_BOOTSTRAP_BYTES = 4 * 1024 * 1024
SSH = "/usr/bin/ssh"
PYTHON = "/usr/bin/python3"
_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
_DESTINATION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_ARTIFACT_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_VERSION_RE = re.compile(r"^[\x21-\x7e]{1,1024}$")
_RECEIVE_ATTESTATION_FIELDS = {
    "schema",
    "status",
    "installation_result",
    "operation_id",
    "artifact_kind",
    "destination_name",
    "installed_relative_path",
    "bucket",
    "object_key",
    "version_id",
    "ciphertext_sha256",
    "ciphertext_bytes",
    "plaintext_sha256",
    "plaintext_bytes",
    "installed_mode",
    "presigned_url_persisted",
    "presigned_url_logged",
    "archive_extracted",
    "docker_image_loaded",
    "compose_started",
}
_OPERATION_PLAN_FIELDS = {
    "schema",
    "status",
    "operation_id",
    "release_sha",
    "manifest_sha256",
    "required_confirmation",
    "artifact_count",
    "database_container_started",
    "public_app_started",
    "private_dr_workers_started",
    "writer_started",
    "persistent_resource_cleanup_performed",
    "bounded_ephemeral_oneoff_cleanup_performed",
    "removed_ephemeral_resources",
    "object_storage_mutated",
    "bootstrap_agent_verified",
    "bootstrap_agent_sha256",
    "bootstrap_agent_bytes",
}
_OPERATION_APPLY_FIELDS = _OPERATION_PLAN_FIELDS | {
    "materialized",
    "images",
    "database",
    "presigned_url_persisted",
    "legacy_resources_mutated",
    "completed_phases",
    "operation_state_sha256",
    "cleanup_policy",
    "functional_boundary",
}
_DATABASE_ATTESTATION_FIELDS = {
    "database_ready",
    "source_revision",
    "migration_revision",
    "restored_source_database_fingerprint_sha256",
    "restored_source_database_row_count",
    "restored_source_database_table_count",
    "writer_fence_command_applied",
    "writer_state",
    "database_container",
    "database_container_started",
    "public_app_started",
    "private_dr_workers_started",
    "writer_started",
    "persistent_resource_cleanup_performed",
    "bounded_ephemeral_oneoff_cleanup_performed",
    "removed_ephemeral_resources",
}
_DATABASE_CONTAINER_FIELDS = {
    "container_id",
    "image_id",
    "project",
    "service",
    "volume_name",
    "data_path",
}
_REMOVED_EPHEMERAL_FIELDS = {
    "container_id",
    "service",
    "image_id",
    "anonymous_volume_names",
}
_MATERIALIZED_FIELDS = {
    "release_root",
    "secrets_root",
    "data_root",
    "runtime_env",
    "compose",
    "uploads_tree",
    "audit_tree",
}
_TREE_ATTESTATION_FIELDS = {
    "tree_sha256",
    "directory_count",
    "file_count",
    "expanded_bytes",
}
_COMPLETED_PHASES = [
    "received",
    "materialized",
    "images-loaded",
    "database-started",
    "database-restored",
    "database-migrated",
    "writer-fenced",
    "verified",
]
_SOURCE_DATABASE_ATTESTATION_FIELDS = {
    "schema",
    "status",
    "operation_id",
    "release_sha",
    "observed_at",
    "database_backup_sha256",
    "database_backup_bytes",
    "database_backup_object_key",
    "database_backup_version_id",
    "postgres_image_id",
    "scratch_postgres_system_id",
    "scratch_container_id",
    "recovered_prior_scratch_residue",
    "source_database",
    "restore_single_transaction",
    "scratch_network_mode",
    "source_database_mutated",
    "source_or_current_mounted",
    "scratch_resources_removed",
    "zero_residue",
}
_EXPECTED_CLEANUP_POLICY = (
    "retain persistent operation-owned database/network/volume resources; "
    "remove only exact operation-labeled ephemeral one-shot containers and "
    "their anonymous volumes; never delete Object Storage versions"
)
_EXPECTED_FUNCTIONAL_BOUNDARY = (
    "data-ready and fenced only; private DR services, convergence, routing, "
    "writer lease, and public activation require the separate cutover controller"
)
_ORCHESTRATOR_BASE_JOURNAL_FIELDS = {
    "schema",
    "operation_id",
    "release_sha",
    "manifest_sha256",
    "phase",
    "objects",
    "remote_attestations",
    "presigned_url_persisted",
    "cleanup_policy",
}
_ORCHESTRATOR_TRANSFERRED_JOURNAL_FIELDS = (
    _ORCHESTRATOR_BASE_JOURNAL_FIELDS
    | {"completed_at", "functional_boundary"}
)
_OBJECT_EVIDENCE_FIELDS = {
    "bucket",
    "object_key",
    "version_id",
    "plaintext_sha256",
    "plaintext_bytes",
    "ciphertext_sha256",
    "ciphertext_bytes",
    "metadata",
    "presigned_url_persisted",
}
_REMOTE_RECEIVE_EVIDENCE_FIELDS = {
    "artifact_kind",
    "destination_name",
    "object_key",
    "version_id",
    "plaintext_sha256",
    "plaintext_bytes",
    "installation_result",
}
_REMOTE_BOOTSTRAP_EVIDENCE_FIELDS = {
    "schema",
    "installation_result",
    "operation_id",
    "object_key",
    "version_id",
    "plaintext_sha256",
    "presigned_url_persisted",
}
_TRANSFER_CLEANUP_POLICY = (
    "retain verified publication journals and Object versions; "
    "local ciphertext cleanup is explicit and never deletes remote objects"
)
_TRANSFER_FUNCTIONAL_BOUNDARY = (
    "artifacts received only; no archive extraction, image load, container, "
    "restore, migration, private service, writer, or route action"
)
_BOOTSTRAP_SOURCE_FILES = (
    "scripts/receive_wa_ir_production_artifact.py",
    "scripts/wa_ir_production_transport_contract.py",
    "scripts/wa_ir_production_operation.py",
)
_BOOTSTRAP_DISPATCH = b"""\
from __future__ import annotations
import sys

def main() -> int:
    if len(sys.argv) < 2:
        return 64
    mode = sys.argv[1]
    remaining = sys.argv[2:]
    if mode == "receive":
        from scripts.receive_wa_ir_production_artifact import main as target
        sys.argv = [sys.argv[0]]
        return target()
    if mode == "operation":
        from scripts.wa_ir_production_operation import main as target
        sys.argv = [sys.argv[0], *remaining]
        return target()
    return 64

if __name__ == "__main__":
    raise SystemExit(main())
"""

# The command is fixed code, not generated from the URL or a local payload.
# stdin contains nine newline-delimited control fields. curl fetches every
# payload byte directly from the exact private/versioned Arvan URL.
_NATIVE_BOOTSTRAP_COMMAND = r"""
set -eu
umask 077
test "$(/usr/bin/id -u)" = 0
for executable in /usr/bin/curl /usr/bin/age /usr/bin/sha256sum /usr/bin/install /usr/bin/stat /usr/bin/awk /usr/bin/find /usr/bin/id /usr/bin/mktemp /usr/bin/rm /usr/bin/mkdir /usr/bin/ln /usr/bin/sync; do
  test -f "$executable"
  test ! -L "$executable"
  test "$(/usr/bin/stat -c %u "$executable")" = 0
  test -z "$(/usr/bin/find "$executable" -perm /022 -print -quit)"
done
IFS= read -r schema
IFS= read -r operation_id
IFS= read -r url
IFS= read -r object_key
IFS= read -r version_id
IFS= read -r ciphertext_sha256
IFS= read -r ciphertext_bytes
IFS= read -r plaintext_sha256
IFS= read -r plaintext_bytes
test "$schema" = "wa-ir-production-bootstrap-control-v1"
case "$operation_id" in
  ????????-????-4???-[89ab]???-????????????) ;;
  *) exit 65 ;;
esac
case "$object_key" in
  dark-standby/*/"$operation_id"/receiver-bootstrap/*.age) ;;
  *) exit 65 ;;
esac
test "${#ciphertext_sha256}" = 64
test "${#plaintext_sha256}" = 64
case "$ciphertext_sha256$plaintext_sha256" in
  *[!0-9a-f]*) exit 65 ;;
esac
case "$ciphertext_bytes:$plaintext_bytes" in
  *[!0-9:]*|:|0:*|*:0) exit 65 ;;
esac
case "$url" in
  https://s3.ir-thr-at1.arvanstorage.ir/production-sync-coin/*) ;;
  *) exit 65 ;;
esac
case "$url" in
  *'"'*|*\\*) exit 65 ;;
esac
work="$(/usr/bin/mktemp -d /run/wa-ir-production-bootstrap.XXXXXXXX)"
trap '/usr/bin/rm -rf "$work"' EXIT HUP INT TERM
printf 'url = "%s"\n' "$url" | /usr/bin/curl \
  --config - \
  --fail --silent --show-error \
  --proto '=https' --tlsv1.2 \
  --noproxy '*' --max-redirs 0 \
  --connect-timeout 15 --max-time 900 \
  --header 'Accept-Encoding: identity' \
  --dump-header "$work/headers" \
  --output "$work/payload.age"
test "$(/usr/bin/stat -c %s "$work/payload.age")" = "$ciphertext_bytes"
printf '%s  %s\n' "$ciphertext_sha256" "$work/payload.age" | /usr/bin/sha256sum --check --status -
observed_version="$(/usr/bin/awk 'BEGIN{IGNORECASE=1} /^x-amz-version-id:[[:space:]]*/ {sub(/\r$/,""); sub(/^[^:]*:[[:space:]]*/,""); print; count++} END{if(count!=1) exit 1}' "$work/headers")"
test "$observed_version" = "$version_id"
observed_length="$(/usr/bin/awk 'BEGIN{IGNORECASE=1} /^content-length:[[:space:]]*/ {sub(/\r$/,""); sub(/^[^:]*:[[:space:]]*/,""); print; count++} END{if(count!=1) exit 1}' "$work/headers")"
test "$observed_length" = "$ciphertext_bytes"
observed_encoding="$(/usr/bin/awk 'BEGIN{IGNORECASE=1} /^content-encoding:[[:space:]]*/ {sub(/\r$/,""); sub(/^[^:]*:[[:space:]]*/,""); print; count++} END{if(count>1) exit 1}' "$work/headers")"
test -z "$observed_encoding" -o "$observed_encoding" = identity
observed_status="$(/usr/bin/awk '/^HTTP\// {print $2; count++} END{if(count!=1) exit 1}' "$work/headers")"
test "$observed_status" = 200
/usr/bin/age --decrypt \
  --identity /root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt \
  --output "$work/agent.pyz" \
  "$work/payload.age"
test "$(/usr/bin/stat -c %s "$work/agent.pyz")" = "$plaintext_bytes"
printf '%s  %s\n' "$plaintext_sha256" "$work/agent.pyz" | /usr/bin/sha256sum --check --status -
test -d /srv/trading-bot
test ! -L /srv/trading-bot
test "$(/usr/bin/stat -c %u /srv/trading-bot)" = 0
test -z "$(/usr/bin/find /srv/trading-bot -maxdepth 0 -perm /022 -print -quit)"
parent=/srv/trading-bot
for component in dark-standby operations "$operation_id" bootstrap; do
  parent="$parent/$component"
  if test -e "$parent"; then
    test -d "$parent"
    test ! -L "$parent"
    test "$(/usr/bin/stat -c %u:%g:%a "$parent")" = 0:0:700
  else
    /usr/bin/mkdir -m 0700 "$parent"
  fi
done
destination="$parent/wa-ir-production-agent.pyz"
temporary="$parent/.wa-ir-production-agent.pyz.materializing"
result=created
if test -e "$temporary" || test -L "$temporary"; then
  test -f "$temporary"
  test ! -L "$temporary"
  test "$(/usr/bin/stat -c %u:%g:%a "$temporary")" = "0:0:700"
  if test -e "$destination" || test -L "$destination"; then
    test -f "$destination"
    test ! -L "$destination"
    test "$(/usr/bin/stat -c %u:%g:%a:%s "$destination")" = "0:0:700:$plaintext_bytes"
    printf '%s  %s\n' "$plaintext_sha256" "$destination" | /usr/bin/sha256sum --check --status -
  else
    test "$(/usr/bin/stat -c %h "$temporary")" = 1
  fi
  /usr/bin/rm -f "$temporary"
  /usr/bin/sync -f "$parent"
fi
if test -e "$destination"; then
  test -f "$destination"
  test ! -L "$destination"
  test "$(/usr/bin/stat -c %u:%g:%a:%s:%h "$destination")" = "0:0:700:$plaintext_bytes:1"
  printf '%s  %s\n' "$plaintext_sha256" "$destination" | /usr/bin/sha256sum --check --status -
  result=already-present
else
  /usr/bin/install -o root -g root -m 0700 "$work/agent.pyz" "$temporary"
  test "$(/usr/bin/stat -c %u:%g:%a:%s:%h "$temporary")" = "0:0:700:$plaintext_bytes:1"
  printf '%s  %s\n' "$plaintext_sha256" "$temporary" | /usr/bin/sha256sum --check --status -
  /usr/bin/sync -f "$temporary"
  /usr/bin/ln "$temporary" "$destination"
  /usr/bin/sync -f "$parent"
  /usr/bin/rm -f "$temporary"
  /usr/bin/sync -f "$parent"
  test "$(/usr/bin/stat -c %u:%g:%a:%s:%h "$destination")" = "0:0:700:$plaintext_bytes:1"
  printf '%s  %s\n' "$plaintext_sha256" "$destination" | /usr/bin/sha256sum --check --status -
fi
printf '%s\n%s\n%s\n%s\n%s\n%s\n' \
  wa-ir-production-bootstrap-attestation-v1 "$result" "$operation_id" \
  "$object_key" "$version_id" "$plaintext_sha256"
""".strip()


class ProductionOrchestratorError(RuntimeError):
    """A redacted fail-closed controller/orchestration error."""


@dataclass(frozen=True)
class LocalArtifact:
    kind: str
    destination_name: str
    source: Path
    max_bytes: int = MAX_PAYLOAD_BYTES


@dataclass(frozen=True)
class RemoteAttestation:
    artifact_kind: str
    destination_name: str
    object_key: str
    version_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    installation_result: str

    def evidence(self) -> Mapping[str, Any]:
        return {
            "artifact_kind": self.artifact_kind,
            "destination_name": self.destination_name,
            "object_key": self.object_key,
            "version_id": self.version_id,
            "plaintext_sha256": self.plaintext_sha256,
            "plaintext_bytes": self.plaintext_bytes,
            "installation_result": self.installation_result,
        }


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _hash_regular(path: Path, *, maximum: int) -> tuple[str, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
            or not 1 <= before.st_size <= maximum
        ):
            raise ProductionOrchestratorError("local production artifact is unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ProductionOrchestratorError("local production artifact is oversized")
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ProductionOrchestratorError("local production artifact changed while hashing")
        return digest.hexdigest(), size
    except OSError as exc:
        raise ProductionOrchestratorError(
            "local production artifact is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_public_source(path: Path, *, maximum: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= maximum
        ):
            raise ProductionOrchestratorError("bootstrap source is unsafe")
        payload = b""
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
        if (
            len(payload) > maximum
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ProductionOrchestratorError("bootstrap source changed while reading")
        return payload
    except OSError as exc:
        raise ProductionOrchestratorError(
            "bootstrap source is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_secure_directory(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOrchestratorError(
            "controller journal directory is unavailable"
        ) from exc
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ProductionOrchestratorError("controller journal directory is unsafe")


def _require_private_file(path: Path, *, label: str, maximum: int = 1024 * 1024) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
        parent = path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOrchestratorError(f"{label} is unavailable or unsafe") from exc
    if (
        not path.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
        or not 1 <= metadata.st_size <= maximum
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise ProductionOrchestratorError(f"{label} is unavailable or unsafe")


def build_bootstrap_agent(
    repository_root: Path,
    output: Path,
) -> tuple[str, int]:
    """Build one deterministic, stdlib-only receiver/operation zipapp."""

    _require_secure_directory(output.parent)
    if output.exists() or output.is_symlink():
        raise ProductionOrchestratorError("bootstrap agent output already exists")
    sources: list[tuple[str, bytes]] = [
        ("__main__.py", _BOOTSTRAP_DISPATCH),
        ("scripts/__init__.py", b""),
    ]
    for relative in _BOOTSTRAP_SOURCE_FILES:
        source = repository_root / relative
        try:
            payload = _read_public_source(source, maximum=2 * 1024 * 1024)
        except ProductionOrchestratorError as exc:
            raise ProductionOrchestratorError(
                "bootstrap agent source is unavailable or unsafe"
            ) from exc
        sources.append((relative, payload))
    try:
        with zipfile.ZipFile(
            output,
            mode="x",
            compression=zipfile.ZIP_STORED,
            allowZip64=False,
        ) as archive:
            for name, payload in sources:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o600 & 0xFFFF) << 16
                archive.writestr(info, payload)
        output.chmod(0o600)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        try:
            output.unlink()
        except OSError:
            pass
        raise ProductionOrchestratorError("bootstrap agent could not be built") from exc
    return _hash_regular(output, maximum=MAX_BOOTSTRAP_BYTES)


def _build_bound_bootstrap_agent(
    manifest: OperationManifest,
    release_bundle: Path,
    *,
    work_directory: Path,
    output: Path,
) -> tuple[str, int]:
    """Build only from the manifest-bound clean detached release bundle."""

    _require_secure_directory(work_directory)
    source_root = work_directory / "bootstrap-source"
    if not source_root.exists() and not source_root.is_symlink():
        source_root.mkdir(mode=0o700)
    try:
        _materialize_release_bundle(
            release_bundle,
            source_root,
            manifest=manifest,
            required_uid=os.geteuid(),
        )
    except ProductionOperationError as exc:
        raise ProductionOrchestratorError(
            "bootstrap source is not the exact clean detached release"
        ) from exc
    if output.exists() or output.is_symlink():
        candidate = work_directory / ".wa-ir-production-agent.candidate.pyz"
        if candidate.exists() or candidate.is_symlink():
            raise ProductionOrchestratorError(
                "bootstrap verification candidate already exists"
            )
        try:
            expected = build_bootstrap_agent(source_root, candidate)
            observed = _hash_regular(output, maximum=MAX_BOOTSTRAP_BYTES)
            if observed != expected:
                raise ProductionOrchestratorError(
                    "existing bootstrap agent differs from the exact release"
                )
        finally:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
    else:
        observed = build_bootstrap_agent(source_root, output)
    if observed != (manifest.bootstrap_sha256, manifest.bootstrap_bytes):
        raise ProductionOrchestratorError(
            "bootstrap executable identity differs from the operation manifest"
        )
    return observed


def _descriptor_document(
    *,
    operation_id: str,
    artifact: LocalArtifact,
    published: PublishedObject,
    presigned: EphemeralPresignedGet,
) -> dict[str, Any]:
    if (
        not _ARTIFACT_KIND_RE.fullmatch(artifact.kind)
        or not _DESTINATION_RE.fullmatch(artifact.destination_name)
        or published.metadata.get("operation-id") != operation_id
        or published.metadata.get("artifact-kind") != artifact.kind
        or presigned.object_key != published.object_key
        or presigned.version_id != published.version_id
    ):
        raise ProductionOrchestratorError("published artifact binding differs")
    return {
        "schema": DESCRIPTOR_SCHEMA,
        "operation_id": operation_id,
        "artifact_kind": artifact.kind,
        "destination_name": artifact.destination_name,
        "bucket": published.bucket,
        "object_key": published.object_key,
        "version_id": published.version_id,
        "url": presigned.reveal_for_control_channel(),
        "ciphertext_sha256": published.ciphertext_sha256,
        "ciphertext_bytes": published.ciphertext_bytes,
        "plaintext_sha256": published.plaintext_sha256,
        "plaintext_bytes": published.plaintext_bytes,
    }


def _verify_source_database_attestation(
    path: Path,
    *,
    manifest: OperationManifest,
) -> Mapping[str, Any]:
    try:
        payload = read_secure_bytes(
            path,
            label="WA-IR source database attestation",
            max_size=64 * 1024,
        )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (OSError, SecureFileError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionOrchestratorError(
            "source database attestation is unavailable or invalid"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != _SOURCE_DATABASE_ATTESTATION_FIELDS
        or document.get("schema") != SOURCE_DATABASE_ATTESTATION_SCHEMA
        or document.get("status") != "source-backup-database-attested"
        or document.get("operation_id") != manifest.operation_id
        or document.get("release_sha") != manifest.release_sha
        or document.get("database_backup_sha256")
        != manifest.artifacts["database-backup"].sha256
        or document.get("database_backup_bytes")
        != manifest.artifacts["database-backup"].bytes
        or not isinstance(document.get("database_backup_object_key"), str)
        or not re.fullmatch(
            r"dark-standby/[a-z0-9._/-]{8,1024}",
            document["database_backup_object_key"],
        )
        or ".." in document["database_backup_object_key"].split("/")
        or not isinstance(document.get("database_backup_version_id"), str)
        or not _VERSION_RE.fullmatch(document["database_backup_version_id"])
        or any(
            character.isspace()
            for character in document["database_backup_version_id"]
        )
        or document.get("postgres_image_id")
        != next(
            image.image_id
            for image in manifest.images
            if image.role == "postgres"
        )
        or not isinstance(document.get("scratch_postgres_system_id"), str)
        or not re.fullmatch(
            r"[0-9]{10,20}",
            document["scratch_postgres_system_id"],
        )
        or not isinstance(document.get("scratch_container_id"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", document["scratch_container_id"])
        or not isinstance(
            document.get("recovered_prior_scratch_residue"),
            bool,
        )
        or document.get("source_database") != manifest.source_database
        or document.get("restore_single_transaction") is not True
        or document.get("scratch_network_mode") != "none"
        or document.get("source_database_mutated") is not False
        or document.get("source_or_current_mounted") is not False
        or document.get("scratch_resources_removed") is not True
        or document.get("zero_residue") is not True
        or _attestation_contains_sensitive_transport(payload)
    ):
        raise ProductionOrchestratorError(
            "source database attestation binding differs"
        )
    try:
        observed = datetime.fromisoformat(
            str(document.get("observed_at", "")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ProductionOrchestratorError(
            "source database attestation timestamp is invalid"
        ) from exc
    now = datetime.now(timezone.utc)
    if (
        observed.tzinfo is None
        or observed > now + timedelta(minutes=5)
        or now - observed > timedelta(hours=24)
    ):
        raise ProductionOrchestratorError(
            "source database attestation is stale or future-dated"
        )
    return document


def build_receive_descriptor(
    *,
    operation_id: str,
    artifact: LocalArtifact,
    published: PublishedObject,
    presigned: EphemeralPresignedGet,
    now: datetime | None = None,
) -> bytes:
    """Build and independently parse one full-SigV4 exact-version descriptor."""

    document = _descriptor_document(
        operation_id=operation_id,
        artifact=artifact,
        published=published,
        presigned=presigned,
    )
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_CONTROL_BYTES:
        raise ProductionOrchestratorError("receiver descriptor is oversized")
    try:
        parse_descriptor(payload, now=now)
    except Exception as exc:
        raise ProductionOrchestratorError(
            "presigned URL or receiver descriptor failed full validation"
        ) from exc
    return payload


def _ssh_arguments(
    ssh_identity: Path,
    *,
    remote_command: str,
    allow_fixed_multiline: bool = False,
) -> list[str]:
    if (
        ("\n" in remote_command or "\r" in remote_command)
        and not allow_fixed_multiline
    ):
        raise ProductionOrchestratorError("remote command must be one fixed argv value")
    return [
        SSH,
        "-T",
        "-p",
        str(WA_IR_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "LogLevel=ERROR",
        f"{WA_IR_USER}@{WA_IR_HOST}",
        remote_command,
    ]


def _remote_agent_path(operation_id: str) -> Path:
    try:
        canonical = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise ProductionOrchestratorError("remote agent operation id is invalid") from exc
    return (
        REMOTE_OPERATIONS_ROOT
        / canonical
        / "bootstrap"
        / BOOTSTRAP_DESTINATION_NAME
    )


def _run_ssh(
    arguments: list[str],
    control: bytes,
    *,
    timeout: int,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> bytes:
    if (
        not 1 <= len(control) <= MAX_CONTROL_BYTES
        or any(control.startswith(prefix) for prefix in (b"\x1f\x8b", b"PK\x03\x04"))
    ):
        raise ProductionOrchestratorError("SSH control input is invalid or payload-like")
    try:
        result = runner(
            arguments,
            input=control,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=_SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionOrchestratorError("WA-IR SSH control channel failed") from exc
    if (
        len(result.stdout) > MAX_ATTESTATION_BYTES
        or len(result.stderr) > MAX_ATTESTATION_BYTES
    ):
        raise ProductionOrchestratorError("WA-IR control output exceeded its bound")
    if result.returncode != 0:
        raise ProductionOrchestratorError("WA-IR control command failed closed")
    return bytes(result.stdout)


def _parse_receive_attestation(
    payload: bytes,
    *,
    descriptor_payload: bytes,
) -> RemoteAttestation:
    try:
        descriptor = parse_descriptor(descriptor_payload)
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except Exception as exc:
        raise ProductionOrchestratorError("WA-IR receive attestation is invalid") from exc
    if (
        not isinstance(document, dict)
        or set(document) != _RECEIVE_ATTESTATION_FIELDS
        or document.get("schema") != RECEIVE_ATTESTATION_SCHEMA
        or document.get("status") != "installed"
        or document.get("installation_result") not in {"created", "already-present"}
        or document.get("operation_id") != descriptor.operation_id
        or document.get("artifact_kind") != descriptor.artifact_kind
        or document.get("destination_name") != descriptor.destination_name
        or document.get("installed_relative_path")
        != (
            f"{descriptor.operation_id}/incoming/"
            f"{descriptor.destination_name}"
        )
        or document.get("bucket") != descriptor.bucket
        or document.get("object_key") != descriptor.object_key
        or document.get("version_id") != descriptor.version_id
        or document.get("ciphertext_sha256") != descriptor.ciphertext_sha256
        or document.get("ciphertext_bytes") != descriptor.ciphertext_bytes
        or document.get("plaintext_sha256") != descriptor.plaintext_sha256
        or document.get("plaintext_bytes") != descriptor.plaintext_bytes
        or document.get("installed_mode") != "0600"
        or document.get("presigned_url_persisted") is not False
        or document.get("presigned_url_logged") is not False
        or document.get("archive_extracted") is not False
        or document.get("docker_image_loaded") is not False
        or document.get("compose_started") is not False
    ):
        raise ProductionOrchestratorError("WA-IR receive attestation binding differs")
    if descriptor.url.encode("utf-8") in payload:
        raise ProductionOrchestratorError("WA-IR attestation leaked its presigned URL")
    return RemoteAttestation(
        artifact_kind=descriptor.artifact_kind,
        destination_name=descriptor.destination_name,
        object_key=descriptor.object_key,
        version_id=descriptor.version_id,
        plaintext_sha256=descriptor.plaintext_sha256,
        plaintext_bytes=descriptor.plaintext_bytes,
        installation_result=str(document["installation_result"]),
    )


def deliver_received_artifact(
    descriptor_payload: bytes,
    *,
    ssh_identity: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> RemoteAttestation:
    # Parse before opening SSH; this also rejects incomplete or malformed SigV4.
    descriptor = parse_descriptor(descriptor_payload)
    command = f"{PYTHON} {_remote_agent_path(descriptor.operation_id)} receive"
    output = _run_ssh(
        _ssh_arguments(ssh_identity, remote_command=command),
        descriptor_payload,
        timeout=1800,
        runner=runner,
    )
    return _parse_receive_attestation(
        output,
        descriptor_payload=descriptor_payload,
    )


def _bootstrap_control(
    *,
    operation_id: str,
    published: PublishedObject,
    presigned: EphemeralPresignedGet,
) -> bytes:
    try:
        operation_id = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise ProductionOrchestratorError("bootstrap operation id is invalid") from exc
    values = (
        BOOTSTRAP_CONTROL_SCHEMA,
        operation_id,
        presigned.reveal_for_control_channel(),
        published.object_key,
        published.version_id,
        published.ciphertext_sha256,
        str(published.ciphertext_bytes),
        published.plaintext_sha256,
        str(published.plaintext_bytes),
    )
    if (
        published.metadata.get("artifact-kind") != BOOTSTRAP_ARTIFACT_KIND
        or presigned.object_key != published.object_key
        or presigned.version_id != published.version_id
        or any("\n" in value or "\r" in value for value in values)
    ):
        raise ProductionOrchestratorError("bootstrap artifact binding is invalid")
    # Reuse the receiver's strict URL parser by constructing a normal descriptor.
    artifact = LocalArtifact(
        BOOTSTRAP_ARTIFACT_KIND,
        BOOTSTRAP_DESTINATION_NAME,
        Path("/nonexistent"),
        MAX_BOOTSTRAP_BYTES,
    )
    build_receive_descriptor(
        operation_id=operation_id,
        artifact=artifact,
        published=published,
        presigned=presigned,
    )
    payload = ("\n".join(values) + "\n").encode("utf-8")
    if len(payload) > MAX_CONTROL_BYTES:
        raise ProductionOrchestratorError("bootstrap control descriptor is oversized")
    return payload


def bootstrap_remote_agent(
    *,
    operation_id: str,
    published: PublishedObject,
    presigned: EphemeralPresignedGet,
    ssh_identity: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    control = _bootstrap_control(
        operation_id=operation_id,
        published=published,
        presigned=presigned,
    )
    remote_command = f"/bin/sh -c {shlex.quote(_NATIVE_BOOTSTRAP_COMMAND)}"
    output = _run_ssh(
        _ssh_arguments(
            ssh_identity,
            remote_command=remote_command,
            allow_fixed_multiline=True,
        ),
        control,
        timeout=1800,
        runner=runner,
    )
    try:
        fields = output.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ProductionOrchestratorError("bootstrap attestation is not UTF-8") from exc
    if (
        len(fields) != 6
        or fields[0] != BOOTSTRAP_ATTESTATION_SCHEMA
        or fields[1] not in {"created", "already-present"}
        or fields[2] != operation_id
        or fields[3] != published.object_key
        or fields[4] != published.version_id
        or fields[5] != published.plaintext_sha256
    ):
        raise ProductionOrchestratorError("bootstrap attestation binding differs")
    if presigned.reveal_for_control_channel() in output.decode("utf-8"):
        raise ProductionOrchestratorError("bootstrap attestation leaked its URL")
    return {
        "schema": BOOTSTRAP_ATTESTATION_SCHEMA,
        "installation_result": fields[1],
        "operation_id": operation_id,
        "object_key": published.object_key,
        "version_id": published.version_id,
        "plaintext_sha256": published.plaintext_sha256,
        "presigned_url_persisted": False,
    }


def publish_one(
    artifact: LocalArtifact,
    *,
    operation_id: str,
    recipient_file: Path,
    prefix: str,
    client: Any,
    journal_path: Path,
    release_sha: str,
) -> PublishedObject:
    if (
        not _ARTIFACT_KIND_RE.fullmatch(artifact.kind)
        or not _DESTINATION_RE.fullmatch(artifact.destination_name)
        or not 1 <= artifact.max_bytes <= MAX_PAYLOAD_BYTES
    ):
        raise ProductionOrchestratorError("local artifact specification is invalid")
    try:
        return publish_age_encrypted(
            artifact.source,
            recipient_file=recipient_file,
            bucket=PRODUCTION_BUCKET,
            prefix=prefix,
            operation_id=operation_id,
            artifact_kind=artifact.kind,
            client=client,
            journal_path=journal_path,
            metadata={
                "destination-name": artifact.destination_name,
                "release-sha": release_sha,
            },
            max_bytes=artifact.max_bytes,
        )
    except ProductionTransportError as exc:
        raise ProductionOrchestratorError("artifact publication failed closed") from exc


def _write_orchestrator_journal(
    path: Path,
    state: Mapping[str, Any],
    *,
    create: bool,
) -> None:
    payload = (
        json.dumps(dict(state), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if _attestation_contains_sensitive_transport(payload):
        raise ProductionOrchestratorError("orchestrator journal must not contain URLs")
    try:
        if create:
            write_secure_new_bytes(
                path,
                payload,
                label="WA-IR production orchestrator journal",
                max_size=1024 * 1024,
            )
        else:
            write_secure_atomic_bytes(
                path,
                payload,
                label="WA-IR production orchestrator journal",
                max_size=1024 * 1024,
            )
    except SecureFileError as exc:
        raise ProductionOrchestratorError(
            "orchestrator journal could not be persisted"
        ) from exc


def _expected_destination_name(artifact_kind: str) -> str:
    if artifact_kind == BOOTSTRAP_ARTIFACT_KIND:
        return BOOTSTRAP_DESTINATION_NAME
    if artifact_kind == "operation-manifest":
        return "operation-manifest.json"
    try:
        return EXPECTED_ARTIFACTS[artifact_kind][0]
    except KeyError as exc:
        raise ProductionOrchestratorError(
            "orchestrator journal artifact scope is invalid"
        ) from exc


def _validate_object_evidence(
    evidence: Any,
    *,
    artifact_kind: str,
    operation_id: str,
    release_sha: str,
) -> None:
    if not isinstance(evidence, dict) or set(evidence) != _OBJECT_EVIDENCE_FIELDS:
        raise ProductionOrchestratorError(
            "orchestrator journal object evidence is invalid"
        )
    metadata = evidence.get("metadata")
    expected_metadata_fields = {
        "destination-name",
        "release-sha",
        "transport-schema",
        "operation-id",
        "artifact-kind",
        "plaintext-sha256",
        "ciphertext-sha256",
    }
    version_id = evidence.get("version_id")
    plaintext_bytes = evidence.get("plaintext_bytes")
    ciphertext_bytes = evidence.get("ciphertext_bytes")
    if (
        evidence.get("bucket") != PRODUCTION_BUCKET
        or not isinstance(evidence.get("object_key"), str)
        or not isinstance(version_id, str)
        or not _VERSION_RE.fullmatch(version_id)
        or not isinstance(evidence.get("plaintext_sha256"), str)
        or not SHA256_RE.fullmatch(evidence["plaintext_sha256"])
        or type(plaintext_bytes) is not int
        or not 1 <= plaintext_bytes <= MAX_PAYLOAD_BYTES
        or not isinstance(evidence.get("ciphertext_sha256"), str)
        or not SHA256_RE.fullmatch(evidence["ciphertext_sha256"])
        or type(ciphertext_bytes) is not int
        or not 1 <= ciphertext_bytes <= MAX_PAYLOAD_BYTES + 1024 * 1024
        or not isinstance(metadata, dict)
        or set(metadata) != expected_metadata_fields
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        )
        or metadata.get("destination-name")
        != _expected_destination_name(artifact_kind)
        or metadata.get("release-sha") != release_sha
        or metadata.get("transport-schema") != TRANSPORT_SCHEMA
        or metadata.get("operation-id") != operation_id
        or metadata.get("artifact-kind") != artifact_kind
        or metadata.get("plaintext-sha256") != evidence["plaintext_sha256"]
        or metadata.get("ciphertext-sha256") != evidence["ciphertext_sha256"]
        or evidence.get("presigned_url_persisted") is not False
    ):
        raise ProductionOrchestratorError(
            "orchestrator journal object evidence binding is invalid"
        )
    try:
        validate_object_key_binding(
            evidence["object_key"],
            operation_id=operation_id,
            artifact_kind=artifact_kind,
            ciphertext_sha256=evidence["ciphertext_sha256"],
        )
    except ProductionTransportError as exc:
        raise ProductionOrchestratorError(
            "orchestrator journal object key binding is invalid"
        ) from exc


def _validate_remote_evidence(
    evidence: Any,
    *,
    artifact_kind: str,
    operation_id: str,
    object_evidence: Mapping[str, Any],
) -> None:
    if artifact_kind == BOOTSTRAP_ARTIFACT_KIND:
        if (
            not isinstance(evidence, dict)
            or set(evidence) != _REMOTE_BOOTSTRAP_EVIDENCE_FIELDS
            or evidence.get("schema") != BOOTSTRAP_ATTESTATION_SCHEMA
            or evidence.get("installation_result")
            not in {"created", "already-present"}
            or evidence.get("operation_id") != operation_id
            or evidence.get("object_key") != object_evidence["object_key"]
            or evidence.get("version_id") != object_evidence["version_id"]
            or evidence.get("plaintext_sha256")
            != object_evidence["plaintext_sha256"]
            or evidence.get("presigned_url_persisted") is not False
        ):
            raise ProductionOrchestratorError(
                "orchestrator journal bootstrap attestation binding is invalid"
            )
        return
    if (
        not isinstance(evidence, dict)
        or set(evidence) != _REMOTE_RECEIVE_EVIDENCE_FIELDS
        or evidence.get("artifact_kind") != artifact_kind
        or evidence.get("destination_name")
        != _expected_destination_name(artifact_kind)
        or evidence.get("object_key") != object_evidence["object_key"]
        or evidence.get("version_id") != object_evidence["version_id"]
        or evidence.get("plaintext_sha256")
        != object_evidence["plaintext_sha256"]
        or evidence.get("plaintext_bytes") != object_evidence["plaintext_bytes"]
        or evidence.get("installation_result") not in {"created", "already-present"}
    ):
        raise ProductionOrchestratorError(
            "orchestrator journal receive attestation binding is invalid"
        )


def _validate_completed_at(value: Any) -> None:
    if not isinstance(value, str) or not 20 <= len(value) <= 64:
        raise ProductionOrchestratorError(
            "orchestrator journal completion time is invalid"
        )
    try:
        observed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProductionOrchestratorError(
            "orchestrator journal completion time is invalid"
        ) from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ProductionOrchestratorError(
            "orchestrator journal completion time is not timezone-aware"
        )


def _load_orchestrator_journal(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    try:
        payload = read_secure_bytes(
            path,
            label="WA-IR production orchestrator journal",
            max_size=1024 * 1024,
        )
        if _attestation_contains_sensitive_transport(payload):
            raise ProductionOrchestratorError(
                "orchestrator journal contains a forbidden URL"
            )
        state = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except ProductionOrchestratorError:
        raise
    except (OSError, SecureFileError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionOrchestratorError(
            "orchestrator journal is unavailable or invalid"
        ) from exc
    phase = state.get("phase") if isinstance(state, dict) else None
    expected_fields = (
        _ORCHESTRATOR_TRANSFERRED_JOURNAL_FIELDS
        if phase == "transferred"
        else _ORCHESTRATOR_BASE_JOURNAL_FIELDS
    )
    if (
        not isinstance(state, dict)
        or set(state) != expected_fields
        or state.get("schema") != ORCHESTRATOR_JOURNAL_SCHEMA
        or state.get("operation_id") != operation_id
        or state.get("release_sha") != release_sha
        or state.get("manifest_sha256") != manifest_sha256
        or state.get("phase") not in {"publishing", "transferred"}
        or not isinstance(state.get("objects"), dict)
        or not isinstance(state.get("remote_attestations"), dict)
        or state.get("presigned_url_persisted") is not False
        or state.get("cleanup_policy") != _TRANSFER_CLEANUP_POLICY
    ):
        raise ProductionOrchestratorError(
            "orchestrator journal binding or phase is invalid"
        )
    allowed_kinds = set(EXPECTED_ARTIFACTS) | {
        BOOTSTRAP_ARTIFACT_KIND,
        "operation-manifest",
    }
    if (
        set(state["objects"]) - allowed_kinds
        or set(state["remote_attestations"]) - allowed_kinds
        or set(state["remote_attestations"]) - set(state["objects"])
    ):
        raise ProductionOrchestratorError(
            "orchestrator journal artifact scope is invalid"
        )
    for artifact_kind, evidence in state["objects"].items():
        _validate_object_evidence(
            evidence,
            artifact_kind=artifact_kind,
            operation_id=operation_id,
            release_sha=release_sha,
        )
    for artifact_kind, evidence in state["remote_attestations"].items():
        _validate_remote_evidence(
            evidence,
            artifact_kind=artifact_kind,
            operation_id=operation_id,
            object_evidence=state["objects"][artifact_kind],
        )
    if phase == "transferred":
        if (
            set(state["objects"]) != allowed_kinds
            or set(state["remote_attestations"]) != allowed_kinds
            or state.get("functional_boundary") != _TRANSFER_FUNCTIONAL_BOUNDARY
        ):
            raise ProductionOrchestratorError(
                "transferred orchestrator journal is incomplete"
            )
        _validate_completed_at(state.get("completed_at"))
    return state


def transfer_operation(
    manifest_path: Path,
    artifact_directory: Path,
    *,
    source_database_attestation: Path,
    recipient_file: Path,
    credentials_file: Path,
    journal_directory: Path,
    ssh_identity: Path,
    prefix: str = DEFAULT_PREFIX,
    ttl_seconds: int = 300,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    """Publish, bootstrap, and receive every artifact in one exact operation."""

    _require_secure_directory(journal_directory)
    _require_private_file(ssh_identity, label="WA-IR SSH identity")
    try:
        manifest_payload = read_secure_bytes(
            manifest_path,
            label="WA-IR operation manifest",
            max_size=256 * 1024,
        )
        manifest = _load_manifest_bytes(manifest_payload)
    except (OSError, SecureFileError, ProductionOperationError) as exc:
        raise ProductionOrchestratorError("operation manifest is unavailable or invalid") from exc
    _verify_source_database_attestation(
        source_database_attestation,
        manifest=manifest,
    )
    credentials = load_secure_credentials(credentials_file)
    client = build_client(credentials)
    operation_id = manifest.operation_id
    orchestrator_journal = journal_directory / "orchestrator.json"
    journal_exists = orchestrator_journal.exists() or orchestrator_journal.is_symlink()
    artifacts: list[LocalArtifact] = []
    for kind, (destination_name, _format) in EXPECTED_ARTIFACTS.items():
        artifacts.append(
            LocalArtifact(kind, destination_name, artifact_directory / destination_name)
        )
    artifacts.append(
        LocalArtifact(
            "operation-manifest",
            "operation-manifest.json",
            manifest_path,
            256 * 1024,
        )
    )
    # Validate all signed payload identities before staging executable source.
    for artifact in artifacts:
        digest, size = _hash_regular(artifact.source, maximum=artifact.max_bytes)
        expected = manifest.artifacts.get(artifact.kind)
        if expected is not None and (digest, size) != (expected.sha256, expected.bytes):
            raise ProductionOrchestratorError(
                f"local {artifact.kind} identity differs from operation manifest"
            )
    bootstrap_path = journal_directory / "wa-ir-production-agent.pyz"
    _build_bound_bootstrap_agent(
        manifest,
        artifact_directory / EXPECTED_ARTIFACTS["release-archive"][0],
        work_directory=journal_directory,
        output=bootstrap_path,
    )
    artifacts.insert(
        0,
        LocalArtifact(
            BOOTSTRAP_ARTIFACT_KIND,
            BOOTSTRAP_DESTINATION_NAME,
            bootstrap_path,
            MAX_BOOTSTRAP_BYTES,
        ),
    )

    if journal_exists:
        state = _load_orchestrator_journal(
            orchestrator_journal,
            operation_id=operation_id,
            release_sha=manifest.release_sha,
            manifest_sha256=manifest.canonical_sha256,
        )
        if state["phase"] == "transferred":
            return {
                "schema": ORCHESTRATOR_SCHEMA,
                "status": "already-transferred",
                "operation_id": operation_id,
                "release_sha": manifest.release_sha,
                "object_count": len(state["objects"]),
                "remote_attestation_count": len(state["remote_attestations"]),
                "orchestrator_journal": str(orchestrator_journal),
                "presigned_url_persisted": False,
                "payload_bytes_over_ssh": False,
                "compose_started": False,
            }
    else:
        state = {
            "schema": ORCHESTRATOR_JOURNAL_SCHEMA,
            "operation_id": operation_id,
            "release_sha": manifest.release_sha,
            "manifest_sha256": manifest.canonical_sha256,
            "phase": "publishing",
            "objects": {},
            "remote_attestations": {},
            "presigned_url_persisted": False,
            "cleanup_policy": _TRANSFER_CLEANUP_POLICY,
        }
        _write_orchestrator_journal(orchestrator_journal, state, create=True)
    for artifact in artifacts:
        publication_journal = journal_directory / f"publish-{artifact.kind}.json"
        published = publish_one(
            artifact,
            operation_id=operation_id,
            recipient_file=recipient_file,
            prefix=prefix,
            client=client,
            journal_path=publication_journal,
            release_sha=manifest.release_sha,
        )
        published_evidence = published.evidence()
        prior_object = state["objects"].get(artifact.kind)
        if prior_object is not None and prior_object != published_evidence:
            raise ProductionOrchestratorError(
                "resumed publication evidence differs from orchestrator journal"
            )
        if prior_object is None:
            state["objects"][artifact.kind] = published_evidence
            _write_orchestrator_journal(orchestrator_journal, state, create=False)
        if artifact.kind in state["remote_attestations"]:
            continue
        presigned = presign_exact_get(
            client,
            published,
            ttl_seconds=ttl_seconds,
        )
        if artifact.kind == BOOTSTRAP_ARTIFACT_KIND:
            remote = bootstrap_remote_agent(
                operation_id=operation_id,
                published=published,
                presigned=presigned,
                ssh_identity=ssh_identity,
                runner=runner,
            )
        else:
            descriptor = build_receive_descriptor(
                operation_id=operation_id,
                artifact=artifact,
                published=published,
                presigned=presigned,
            )
            remote = deliver_received_artifact(
                descriptor,
                ssh_identity=ssh_identity,
                runner=runner,
            ).evidence()
        prior_remote = state["remote_attestations"].get(artifact.kind)
        if prior_remote is not None and prior_remote != remote:
            raise ProductionOrchestratorError(
                "resumed remote attestation differs from orchestrator journal"
            )
        state["remote_attestations"][artifact.kind] = remote
        _write_orchestrator_journal(orchestrator_journal, state, create=False)
    state["phase"] = "transferred"
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    state["functional_boundary"] = _TRANSFER_FUNCTIONAL_BOUNDARY
    _write_orchestrator_journal(orchestrator_journal, state, create=False)
    return {
        "schema": ORCHESTRATOR_SCHEMA,
        "status": "transferred",
        "operation_id": operation_id,
        "release_sha": manifest.release_sha,
        "object_count": len(state["objects"]),
        "remote_attestation_count": len(state["remote_attestations"]),
        "orchestrator_journal": str(orchestrator_journal),
        "presigned_url_persisted": False,
        "payload_bytes_over_ssh": False,
        "compose_started": False,
    }


def _validate_removed_ephemeral_resources(
    value: Any,
    *,
    manifest: OperationManifest,
) -> bool:
    if not isinstance(value, list) or len(value) > 256:
        return False
    identifiers: set[str] = set()
    allowed_services = set(manifest.services.values()) - {
        manifest.services["database"],
    }
    images = {image.role: image.image_id for image in manifest.images}
    for item in value:
        if not isinstance(item, dict) or set(item) != _REMOVED_EPHEMERAL_FIELDS:
            return False
        identifier = item.get("container_id")
        service = item.get("service")
        volumes = item.get("anonymous_volume_names")
        expected_image = (
            images["postgres"]
            if service == manifest.services["restore"]
            else images["app"]
        ) if service in allowed_services else None
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[0-9a-f]{64}", identifier)
            or identifier in identifiers
            or service not in allowed_services
            or item.get("image_id") != expected_image
            or not isinstance(volumes, list)
            or volumes != sorted(set(volumes))
            or len(volumes) > 1
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[0-9a-f]{64}", name)
                for name in volumes
            )
            or (
                service == manifest.services["restore"]
                and len(volumes) != 1
            )
            or (
                service != manifest.services["restore"]
                and volumes != []
            )
        ):
            return False
        identifiers.add(identifier)
    return True


def _validate_materialized_attestation(
    value: Any,
    *,
    operation_id: str,
) -> bool:
    if not isinstance(value, dict) or set(value) != _MATERIALIZED_FIELDS:
        return False
    root = REMOTE_OPERATIONS_ROOT / operation_id
    expected_paths = {
        "release_root": str(root / "release"),
        "secrets_root": str(root / "secrets"),
        "data_root": str(root / "data"),
        "runtime_env": str(root / "secrets" / "webapp-ir" / "runtime.env.role"),
        "compose": str(root / "rendered" / "webapp-ir" / "docker-compose.yml"),
    }
    if any(value.get(key) != expected for key, expected in expected_paths.items()):
        return False
    for key in ("uploads_tree", "audit_tree"):
        tree = value.get(key)
        if (
            not isinstance(tree, dict)
            or set(tree) != _TREE_ATTESTATION_FIELDS
            or not isinstance(tree.get("tree_sha256"), str)
            or not SHA256_RE.fullmatch(tree["tree_sha256"])
            or any(
                type(tree.get(field)) is not int or tree[field] < 0
                for field in ("directory_count", "file_count", "expanded_bytes")
            )
        ):
            return False
    return True


def _attestation_contains_sensitive_transport(payload: bytes) -> bool:
    lowered = payload.lower()
    return any(
        marker in lowered
        for marker in (
            b"https://",
            b"http://",
            b"x-amz-",
            b"aws4_request",
            b"signature=",
            b"credential=",
        )
    )


def run_remote_operation(
    *,
    manifest: OperationManifest,
    ssh_identity: Path,
    apply: bool,
    confirm: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Mapping[str, Any]:
    _require_private_file(ssh_identity, label="WA-IR SSH identity")
    try:
        canonical = validate_operation_id(manifest.operation_id)
    except ProductionTransportError as exc:
        raise ProductionOrchestratorError("operation id is invalid") from exc
    command = (
        f"{PYTHON} {_remote_agent_path(canonical)} "
        f"operation --operation-id {canonical}"
    )
    if apply:
        required = f"prepare-wa-ir:{canonical}:{manifest.release_sha}"
        if confirm != required:
            raise ProductionOrchestratorError("remote operation confirmation is invalid")
        command += f" --apply --confirm {confirm}"
    elif confirm is not None:
        raise ProductionOrchestratorError("confirmation is valid only with apply")
    output = _run_ssh(
        _ssh_arguments(ssh_identity, remote_command=command),
        b"{}\n",
        timeout=7200 if apply else 900,
        runner=runner,
    )
    if _attestation_contains_sensitive_transport(output):
        raise ProductionOrchestratorError(
            "remote operation attestation contains transport material"
        )
    try:
        document = json.loads(
            output.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionOrchestratorError("remote operation attestation is invalid") from exc
    expected_status = "wa-ir-shadow-data-ready-fenced" if apply else "planned"
    expected_fields = _OPERATION_APPLY_FIELDS if apply else _OPERATION_PLAN_FIELDS
    if (
        not isinstance(document, dict)
        or set(document) != expected_fields
        or document.get("schema") != OPERATION_ATTESTATION_SCHEMA
        or document.get("operation_id") != canonical
        or document.get("release_sha") != manifest.release_sha
        or document.get("manifest_sha256") != manifest.canonical_sha256
        or document.get("status") != expected_status
        or document.get("required_confirmation")
        != (
            None
            if apply
            else f"prepare-wa-ir:{canonical}:{manifest.release_sha}"
        )
        or document.get("artifact_count") != len(EXPECTED_ARTIFACTS)
        or document.get("bootstrap_agent_verified") is not True
        or document.get("bootstrap_agent_sha256") != manifest.bootstrap_sha256
        or document.get("bootstrap_agent_bytes") != manifest.bootstrap_bytes
        or document.get("database_container_started") is not apply
        or document.get("public_app_started") is not False
        or document.get("private_dr_workers_started") is not False
        or document.get("writer_started") is not False
        or document.get("object_storage_mutated") is not False
        or document.get("persistent_resource_cleanup_performed") is not False
        or not isinstance(
            document.get("bounded_ephemeral_oneoff_cleanup_performed"),
            bool,
        )
        or not isinstance(document.get("removed_ephemeral_resources"), list)
        or not _validate_removed_ephemeral_resources(
            document.get("removed_ephemeral_resources"),
            manifest=manifest,
        )
        or document.get("bounded_ephemeral_oneoff_cleanup_performed")
        is not bool(document.get("removed_ephemeral_resources"))
    ):
        raise ProductionOrchestratorError("remote operation attestation differs")
    if not apply and (
        document["bounded_ephemeral_oneoff_cleanup_performed"] is not False
        or document["removed_ephemeral_resources"] != []
    ):
        raise ProductionOrchestratorError("remote plan cleanup attestation differs")
    if apply:
        database = document.get("database")
        writer_state = database.get("writer_state") if isinstance(database, dict) else None
        database_container = (
            database.get("database_container")
            if isinstance(database, dict)
            else None
        )
        expected_postgres_image = next(
            image.image_id
            for image in manifest.images
            if image.role == "postgres"
        )
        expected_volume = f"{manifest.project_name}_webapp_ir_postgres"
        expected_data_path = str(
            REMOTE_OPERATIONS_ROOT
            / canonical
            / "data"
            / "webapp-ir"
            / "postgres"
        )
        expected_images = [
            {
                "role": image.role,
                "image_id": image.image_id,
                "source": "object-storage-archive",
            }
            for image in manifest.images
        ]
        if (
            not isinstance(database, dict)
            or set(database) != _DATABASE_ATTESTATION_FIELDS
            or database.get("database_ready") is not True
            or database.get("source_revision")
            != manifest.source_database["alembic_revision"]
            or database.get("migration_revision")
            != manifest.expected_migration_revision
            or database.get("restored_source_database_fingerprint_sha256")
            != manifest.source_database["database_fingerprint_sha256"]
            or database.get("restored_source_database_row_count")
            != manifest.source_database["row_count"]
            or database.get("restored_source_database_table_count")
            != manifest.source_database["table_count"]
            or database.get("database_container_started") is not True
            or database.get("public_app_started") is not False
            or database.get("private_dr_workers_started") is not False
            or database.get("writer_started") is not False
            or database.get("writer_fence_command_applied") is not True
            or database.get("persistent_resource_cleanup_performed") is not False
            or database.get("bounded_ephemeral_oneoff_cleanup_performed")
            is not document["bounded_ephemeral_oneoff_cleanup_performed"]
            or database.get("removed_ephemeral_resources")
            != document["removed_ephemeral_resources"]
            or not _validate_removed_ephemeral_resources(
                database.get("removed_ephemeral_resources"),
                manifest=manifest,
            )
            or not isinstance(database_container, dict)
            or set(database_container) != _DATABASE_CONTAINER_FIELDS
            or not isinstance(database_container.get("container_id"), str)
            or not re.fullmatch(
                r"[0-9a-f]{12,64}",
                database_container["container_id"],
            )
            or database_container.get("image_id") != expected_postgres_image
            or database_container.get("project") != manifest.project_name
            or database_container.get("service")
            != manifest.services["database"]
            or database_container.get("volume_name") != expected_volume
            or database_container.get("data_path") != expected_data_path
            or not _validate_materialized_attestation(
                document.get("materialized"),
                operation_id=canonical,
            )
            or document.get("images") != expected_images
            or document.get("presigned_url_persisted") is not False
            or document.get("legacy_resources_mutated") is not False
            or document.get("completed_phases") != _COMPLETED_PHASES
            or not isinstance(document.get("operation_state_sha256"), str)
            or not SHA256_RE.fullmatch(document["operation_state_sha256"])
            or document.get("cleanup_policy") != _EXPECTED_CLEANUP_POLICY
            or document.get("functional_boundary")
            != _EXPECTED_FUNCTIONAL_BOUNDARY
            or writer_state
            != {
                "active_site": None,
                "writer_epoch": 1,
                "control_state": "fenced",
                "witness_lease_id": None,
            }
        ):
            raise ProductionOrchestratorError("remote writer fencing attestation differs")
    return document


def finalize_local_ciphertexts(
    journal_directory: Path,
    *,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
) -> Mapping[str, Any]:
    """Remove only verified local ciphertext caches; retain all evidence/objects."""

    _require_secure_directory(journal_directory)
    try:
        operation_id = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise ProductionOrchestratorError("operation id is invalid") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha) or not SHA256_RE.fullmatch(
        manifest_sha256
    ):
        raise ProductionOrchestratorError("finalization identity is invalid")
    state = _load_orchestrator_journal(
        journal_directory / "orchestrator.json",
        operation_id=operation_id,
        release_sha=release_sha,
        manifest_sha256=manifest_sha256,
    )
    if state["phase"] != "transferred":
        raise ProductionOrchestratorError(
            "local ciphertext cleanup requires a transferred operation"
        )
    expected_kinds = set(EXPECTED_ARTIFACTS) | {
        BOOTSTRAP_ARTIFACT_KIND,
        "operation-manifest",
    }
    if (
        set(state["objects"]) != expected_kinds
        or set(state["remote_attestations"]) != expected_kinds
    ):
        raise ProductionOrchestratorError(
            "local ciphertext cleanup requires every exact attestation"
        )
    removed: list[str] = []
    absent: list[str] = []
    for kind in sorted(expected_kinds):
        journal = journal_directory / f"publish-{kind}.json"
        with _publication_lock(journal):
            publication = _load_journal(journal)
            if (
                publication.get("phase") != "verified"
                or publication.get("operation_id") != operation_id
                or publication.get("artifact_kind") != kind
                or publication.get("version_id")
                != state["objects"][kind].get("version_id")
                or publication.get("object_key")
                != state["objects"][kind].get("object_key")
            ):
                raise ProductionOrchestratorError(
                    "publication journal is not exactly verified for cleanup"
                )
            ciphertext = _journal_ciphertext_path(journal)
            try:
                metadata = ciphertext.stat(follow_symlinks=False)
            except FileNotFoundError:
                absent.append(kind)
                continue
            except OSError as exc:
                raise ProductionOrchestratorError(
                    "local ciphertext cache is unsafe"
                ) from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size != publication.get("ciphertext_bytes")
                or _hash_regular(ciphertext, maximum=MAX_PAYLOAD_BYTES + 1024 * 1024)[0]
                != publication.get("ciphertext_sha256")
            ):
                raise ProductionOrchestratorError(
                    "local ciphertext cache identity differs"
                )
            try:
                ciphertext.unlink()
                directory_fd = os.open(
                    journal_directory,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                raise ProductionOrchestratorError(
                    "local ciphertext cache cleanup failed"
                ) from exc
            removed.append(kind)
    return {
        "schema": ORCHESTRATOR_SCHEMA,
        "status": "local-ciphertexts-finalized",
        "operation_id": operation_id,
        "removed": removed,
        "already_absent": absent,
        "publication_journals_retained": True,
        "object_storage_objects_deleted": False,
        "remote_operation_resources_deleted": False,
    }


def _error_payload(message: str) -> Mapping[str, str]:
    return {
        "status": "blocked",
        "error": message,
        "error_class": "ProductionOrchestratorError",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    plan_parser = subparsers.add_parser("validate-local")
    transfer_parser = subparsers.add_parser("transfer")
    prepare_parser = subparsers.add_parser("prepare")
    finalize_parser = subparsers.add_parser("finalize-local")
    for selected in (plan_parser, transfer_parser):
        selected.add_argument("--manifest", type=Path, required=True)
        selected.add_argument("--artifact-directory", type=Path, required=True)
        selected.add_argument(
            "--source-database-attestation",
            type=Path,
            required=True,
        )
    transfer_parser.add_argument("--recipient-file", type=Path, required=True)
    transfer_parser.add_argument("--credentials-file", type=Path, required=True)
    transfer_parser.add_argument("--journal-directory", type=Path, required=True)
    transfer_parser.add_argument("--ssh-identity", type=Path, required=True)
    transfer_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    transfer_parser.add_argument("--ttl-seconds", type=int, default=300)
    transfer_parser.add_argument("--apply", action="store_true")
    transfer_parser.add_argument("--confirm")
    prepare_parser.add_argument("--operation-id", required=True)
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--ssh-identity", type=Path, required=True)
    prepare_parser.add_argument("--apply", action="store_true")
    prepare_parser.add_argument("--confirm")
    finalize_parser.add_argument("--journal-directory", type=Path, required=True)
    finalize_parser.add_argument("--operation-id", required=True)
    finalize_parser.add_argument("--release-sha", required=True)
    finalize_parser.add_argument("--manifest-sha256", required=True)
    finalize_parser.add_argument("--apply", action="store_true")
    finalize_parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ProductionOrchestratorError("production orchestrator must run as root")
        if args.action == "validate-local":
            manifest_payload = read_secure_bytes(
                args.manifest,
                label="WA-IR operation manifest",
                max_size=256 * 1024,
            )
            manifest = _load_manifest_bytes(manifest_payload)
            _verify_source_database_attestation(
                args.source_database_attestation,
                manifest=manifest,
            )
            for kind, (name, _format) in EXPECTED_ARTIFACTS.items():
                expected = manifest.artifacts[kind]
                if _hash_regular(
                    args.artifact_directory / name,
                    maximum=MAX_PAYLOAD_BYTES,
                ) != (expected.sha256, expected.bytes):
                    raise ProductionOrchestratorError(
                        f"local {kind} identity differs"
                    )
            with tempfile.TemporaryDirectory(
                prefix="wa-ir-bound-bootstrap-"
            ) as raw:
                work_directory = Path(raw)
                work_directory.chmod(0o700)
                _build_bound_bootstrap_agent(
                    manifest,
                    args.artifact_directory
                    / EXPECTED_ARTIFACTS["release-archive"][0],
                    work_directory=work_directory,
                    output=work_directory / BOOTSTRAP_DESTINATION_NAME,
                )
            result: Mapping[str, Any] = {
                "schema": ORCHESTRATOR_SCHEMA,
                "status": "validated-local",
                "operation_id": manifest.operation_id,
                "release_sha": manifest.release_sha,
                "network_io": False,
            }
        elif args.action == "transfer":
            required = f"transfer-wa-ir:{args.confirm or ''}"
            manifest_payload = read_secure_bytes(
                args.manifest,
                label="WA-IR operation manifest",
                max_size=256 * 1024,
            )
            manifest = _load_manifest_bytes(manifest_payload)
            expected_confirmation = (
                f"transfer-wa-ir:{manifest.operation_id}:{manifest.release_sha}"
            )
            if not args.apply or args.confirm != expected_confirmation:
                raise ProductionOrchestratorError(
                    f"live transfer requires --apply --confirm {expected_confirmation}"
                )
            result = transfer_operation(
                args.manifest,
                args.artifact_directory,
                source_database_attestation=args.source_database_attestation,
                recipient_file=args.recipient_file,
                credentials_file=args.credentials_file,
                journal_directory=args.journal_directory,
                ssh_identity=args.ssh_identity,
                prefix=args.prefix,
                ttl_seconds=args.ttl_seconds,
            )
        elif args.action == "prepare":
            manifest_payload = read_secure_bytes(
                args.manifest,
                label="WA-IR operation manifest",
                max_size=256 * 1024,
            )
            manifest = _load_manifest_bytes(manifest_payload)
            if manifest.operation_id != args.operation_id:
                raise ProductionOrchestratorError(
                    "remote operation id differs from local manifest"
                )
            result = run_remote_operation(
                manifest=manifest,
                ssh_identity=args.ssh_identity,
                apply=args.apply,
                confirm=args.confirm,
            )
        else:
            expected_confirmation = f"finalize-local:{args.operation_id}"
            if not args.apply or args.confirm != expected_confirmation:
                raise ProductionOrchestratorError(
                    f"local cleanup requires --apply --confirm {expected_confirmation}"
                )
            result = finalize_local_ciphertexts(
                args.journal_directory,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                manifest_sha256=args.manifest_sha256,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        OSError,
        SecureFileError,
        ProductionOperationError,
        ProductionOrchestratorError,
        ProductionTransportError,
    ) as exc:
        print(json.dumps(_error_payload(str(exc)), sort_keys=True, separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                _error_payload("production artifact orchestration failed closed"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
