#!/usr/bin/env python3
"""Attest an unchanged native Witness for one production-shadow operation.

The producer is deliberately local and read-only apart from three create-only,
root-only JSON outputs.  It never activates the staged Git release and never
reads runtime environment files, database state, or private keys.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import stat
import sys
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import (
    ec,
    ed25519,
    ed448,
    rsa,
)
from cryptography.x509.oid import ExtendedKeyUsageOID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_new_bytes,
)
from scripts.production_shadow_global_docker_inventory_agent import (  # noqa: E402
    BoundedCommandError,
    BoundedCommandResult,
    _bounded_command,
)


PUBLIC_INPUT_SCHEMA = "production-shadow-witness-public-prepare-input-v1"
HEALTH_ATTESTATION_SCHEMA = (
    "production-shadow-witness-health-attestation-v1"
)
STAGE_OPERATION_SCHEMA = (
    "production-shadow-witness-stage-operation-manifest-v1"
)
SUMMARY_SCHEMA = "production-shadow-witness-public-stage-summary-v1"
CONTROLLER_BINDING_SCHEMA = (
    "production-shadow-role-image-stage-binding-v1"
)

STAGED_RELEASE_PREFIX = Path("/srv/trading-bot-three-site/releases")
ACTIVE_RELEASE_ROOT = Path("/opt/trading-bot-witness/active/release")
CA_CERTIFICATE_PATH = Path("/etc/trading-bot-witness/tls/ca.crt")
SYSTEMD_COMMAND = (
    "/usr/bin/systemctl",
    "is-active",
    "writer-witness.service",
)
GIT_EXECUTABLE = "/usr/bin/git"
LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_HTTP_PORT = 8011
LOOPBACK_TLS_PORT = 443
HEALTH_TIMEOUT_SECONDS = 3.0
COMMAND_TIMEOUT_SECONDS = 5.0
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_HTTP_BODY_BYTES = 1024
MAX_PUBLIC_FILE_BYTES = 1024 * 1024
MAX_HEALTH_AGE_SECONDS = 5 * 60
MAX_HEALTH_FUTURE_SKEW_SECONDS = 30
MIN_CERTIFICATE_REMAINING_SECONDS = 24 * 60 * 60

HEALTH_EXPECTATIONS = {
    "/health/live": b'{"status":"alive","service":"writer-witness"}',
    "/health/ready": b'{"status":"ready"}',
}
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# This is the exact source-to-native-release inventory implemented by
# scripts/build_writer_witness_release.sh.  Equality against the active native
# manifest proves that a new repository release does not change the running
# Witness payload, without executing any code from the candidate checkout.
_SAME_PATH_SOURCES = (
    "writer_witness_app.py",
    "scripts/smoke_writer_witness_client.py",
    "scripts/run_writer_witness_clock_jump_probe.py",
    "scripts/provision_writer_witness_host.sh",
    "scripts/hold_writer_witness_package_locks.py",
    "scripts/verify_writer_witness_host_toolchain.py",
    "scripts/verify_writer_witness_release.py",
    "scripts/verify_writer_witness_runtime.py",
    "scripts/verify_writer_witness_runtime_provenance.py",
    "scripts/verify_writer_witness_process_maps.py",
    "scripts/verify_writer_witness_wheelhouse.py",
    "scripts/verify_writer_witness_nftables.py",
    "scripts/render_writer_witness_credentials.py",
    "core/__init__.py",
    "core/enums.py",
    "core/offer_identity.py",
    "core/registration_identity.py",
    "core/canonical_json.py",
    "core/human_approval.py",
    "core/runtime_sites.py",
    "core/secure_file_io.py",
    "core/writer_lease_clock.py",
    "core/writer_witness_auth.py",
    "core/writer_witness_contract.py",
    "core/writer_witness_control.py",
    "models/database.py",
    "models/webapp_writer_state.py",
)
_WITNESS_DEPLOY_SOURCES = (
    "001_initial.sql",
    "002_failover_operation_ledger.sql",
    "003_human_approval_relay.sql",
    "requirements.txt",
    "requirements.lock",
    "python-runtime.json",
    "nftables-policy.json",
    "wheelhouse.sha256",
    "nginx.conf.template",
    "writer-witness-activation.py",
    "writer-witness-activation-recovery.service",
    "writer-witness-activation-watchdog.sh",
    "writer-witness-activation-watchdog.service",
    "writer-witness-activation-watchdog.timer",
    "writer-witness.service",
    "writer-witness-backup.sh",
    "writer-witness-offsite-backup.sh",
    "writer-witness-s3-put.py",
    "writer-witness-rotate-hmac.py",
    "writer-witness-live-restore.sh",
    "writer-witness-matrix-campaign.py",
    "writer-witness-matrix-host-faults.sh",
    "writer-witness-matrix-host-fault-state.py",
    "writer-witness-state-manifest.sh",
    "writer-witness-restore-drill.sh",
    "writer-witness-backup.service",
    "writer-witness-backup.timer",
    "writer-witness-offsite-backup.service",
    "writer-witness-offsite-backup.timer",
)
SOURCE_TO_NATIVE_RELEASE = {
    **{path: path for path in _SAME_PATH_SOURCES},
    **{
        f"deploy/writer-witness/{name}": (
            f"deploy/writer-witness/{name}"
        )
        for name in _WITNESS_DEPLOY_SOURCES
    },
    "deploy/writer-witness/models-package-init.py": "models/__init__.py",
}

PUBLIC_INPUT_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "release_manifest_sha256",
        "health_attestation_sha256",
        "health_attested_at_epoch",
        "ca_sha256",
        "server_cert_sha256",
        "native_release_reused",
        "current_mutated",
        "service_mutated",
        "legacy_secret_material_copied",
    }
)
HEALTH_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "release_manifest_sha256",
        "observed_at_epoch",
        "systemd",
        "loopback_http",
        "loopback_tls",
    }
)
STAGE_OPERATION_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "candidate_release_root",
        "active_native_release_root",
        "release_manifest_sha256",
        "release_subset_entries",
        "health_attestation_sha256",
        "stage_attestation_sha256",
        "health_attested_at_epoch",
        "native_release_reused",
        "current_mutated",
        "service_mutated",
        "legacy_secret_material_copied",
        "runtime_image_ids",
    }
)
CONTROLLER_BINDING_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "role",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
        "runtime_image_ids",
    }
)

HEALTH_ATTESTATION_NAME = "witness-health-attestation.json"
PUBLIC_INPUT_NAME = "witness-public-prepare-input.json"
STAGE_OPERATION_NAME = "witness-stage-operation-manifest.json"
CONTROLLER_BINDING_NAME = "witness-controller-stage-binding.json"


class WitnessPublicStageError(RuntimeError):
    """Witness public stage evidence cannot be proven safe."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class HttpObservation:
    status_code: int
    content_type: str
    body: bytes


@dataclass(frozen=True)
class TlsObservation:
    peer_certificate_der: bytes
    protocol: str
    cipher: str


CommandRunner = Callable[[tuple[str, ...], float], CommandResult]
HttpProbe = Callable[[str, float], HttpObservation]
TlsProbe = Callable[[bytes, str, float], TlsObservation]
Checkpoint = Callable[[str], None]


def _noop_checkpoint(_phase: str) -> None:
    return


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WitnessPublicStageError(
                f"duplicate JSON key is forbidden: {key}"
            )
        result[key] = value
    return result


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _canonical_operation_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise WitnessPublicStageError(
            "operation id must be a canonical UUIDv4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise WitnessPublicStageError(
            "operation id must be a canonical UUIDv4"
        )
    return value


def _release_sha(value: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise WitnessPublicStageError(
            "release SHA must be 40 lowercase hexadecimal characters"
        )
    return value


def _assert_sha256(value: str, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise WitnessPublicStageError(f"{label} is not a SHA-256 digest")
    if value == "0" * 64:
        raise WitnessPublicStageError(f"{label} must not be zero")
    return value


def _assert_trusted_directory(
    path: Path,
    *,
    required_uid: int,
    required_gid: int,
    exact_mode: int | None,
    label: str,
) -> os.stat_result:
    try:
        metadata = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise WitnessPublicStageError(f"{label} is unavailable") from exc
    if (
        resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or metadata.st_gid != required_gid
        or (
            exact_mode is not None
            and stat.S_IMODE(metadata.st_mode) != exact_mode
        )
        or (
            exact_mode is None
            and stat.S_IMODE(metadata.st_mode) & 0o022
        )
    ):
        raise WitnessPublicStageError(
            f"{label} is not a trusted real directory"
        )
    return metadata


def _read_stable_public_file(
    path: Path,
    *,
    label: str,
    required_uid: int,
    required_gid: int,
    allowed_modes: frozenset[int],
    maximum_bytes: int = MAX_PUBLIC_FILE_BYTES,
) -> tuple[bytes, tuple[int, ...]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WitnessPublicStageError(
            f"cannot safely open {label}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != required_uid
            or before.st_gid != required_gid
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            raise WitnessPublicStageError(
                f"{label} metadata is unsafe"
            )
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(65536, maximum_bytes + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        if not payload or len(payload) > maximum_bytes:
            raise WitnessPublicStageError(
                f"{label} size is unsafe"
            )
        after = os.fstat(descriptor)
        if _metadata_identity(before) != _metadata_identity(after):
            raise WitnessPublicStageError(
                f"{label} changed while it was read"
            )
        return bytes(payload), _metadata_identity(after)
    finally:
        os.close(descriptor)


def _parse_native_release_manifest(raw: bytes) -> dict[str, str]:
    try:
        decoded = raw.decode("utf-8")
        document = json.loads(decoded, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessPublicStageError(
            "active native release manifest is invalid JSON"
        ) from exc
    if not isinstance(document, dict) or not document:
        raise WitnessPublicStageError(
            "active native release manifest must be a non-empty object"
        )
    result: dict[str, str] = {}
    for relative, digest in document.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise WitnessPublicStageError(
                "active native release manifest entries are invalid"
            )
        pure = PurePosixPath(relative)
        if (
            not relative
            or relative.startswith("/")
            or "\\" in relative
            or pure.as_posix() != relative
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise WitnessPublicStageError(
                "active native release manifest path is unsafe"
            )
        _assert_sha256(digest, label=f"native release entry {relative}")
        result[relative] = digest
    expected_raw = (
        json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")
    if raw != expected_raw:
        raise WitnessPublicStageError(
            "active native release manifest is not in canonical builder form"
        )
    return result


def _default_command_runner(
    argv: tuple[str, ...],
    timeout: float,
) -> CommandResult:
    try:
        completed = _bounded_command(
            argv,
            env={
                "GIT_NO_REPLACE_OBJECTS": "1",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
            },
            timeout=timeout,
            stdout_limit=MAX_COMMAND_OUTPUT_BYTES,
            stderr_limit=MAX_COMMAND_OUTPUT_BYTES,
        )
    except BoundedCommandError as exc:
        raise WitnessPublicStageError(
            "fixed local command did not complete safely"
        ) from exc
    return CommandResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _run_exact(
    runner: CommandRunner,
    argv: tuple[str, ...],
    *,
    expected_returncode: int = 0,
) -> CommandResult:
    result = runner(argv, COMMAND_TIMEOUT_SECONDS)
    if (
        not isinstance(result, CommandResult)
        or result.argv != argv
        or isinstance(result.returncode, bool)
        or result.returncode != expected_returncode
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(result.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise WitnessPublicStageError(
            "fixed local command result is invalid"
        )
    return result


def _git_command(root: Path, *arguments: str) -> tuple[str, ...]:
    return (
        GIT_EXECUTABLE,
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-C",
        str(root),
        *arguments,
    )


def _parse_git_index(raw: bytes) -> dict[str, int]:
    result: dict[str, int] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            prefix, raw_path = record.split(b"\t", 1)
            raw_mode, raw_object, raw_stage = prefix.split(b" ", 2)
            relative = raw_path.decode("utf-8")
            mode = int(raw_mode, 8)
            object_id = raw_object.decode("ascii")
            stage = int(raw_stage, 10)
        except (UnicodeDecodeError, ValueError) as exc:
            raise WitnessPublicStageError(
                "Git index output is invalid"
            ) from exc
        pure = PurePosixPath(relative)
        if (
            stage != 0
            or mode not in {0o100644, 0o100755}
            or SHA40_RE.fullmatch(object_id) is None
            or relative.startswith("/")
            or "\\" in relative
            or pure.as_posix() != relative
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in result
        ):
            raise WitnessPublicStageError(
                "Git index contains an unsafe entry"
            )
        result[relative] = mode
    if not result:
        raise WitnessPublicStageError("Git index is empty")
    return result


def _scan_candidate_tree(
    root: Path,
    *,
    required_uid: int,
    required_gid: int,
) -> dict[str, tuple[int, ...]]:
    files: dict[str, tuple[int, ...]] = {}
    try:
        for current_raw, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_raw)
            relative_current = current.relative_to(root)
            if relative_current == Path(".") and ".git" in directory_names:
                git_path = current / ".git"
                git_metadata = git_path.lstat()
                if (
                    not stat.S_ISDIR(git_metadata.st_mode)
                    or git_metadata.st_uid != required_uid
                    or git_metadata.st_gid != required_gid
                    or stat.S_IMODE(git_metadata.st_mode) & 0o022
                ):
                    raise WitnessPublicStageError(
                        "candidate Git metadata directory is unsafe"
                    )
                directory_names.remove(".git")
            for name in tuple(directory_names):
                path = current / name
                metadata = path.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != required_uid
                    or metadata.st_gid != required_gid
                    or stat.S_IMODE(metadata.st_mode) & 0o022
                ):
                    raise WitnessPublicStageError(
                        "candidate release contains an unsafe directory"
                    )
            for name in file_names:
                path = current / name
                relative = path.relative_to(root).as_posix()
                metadata = path.lstat()
                if relative == ".git":
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != required_uid
                        or metadata.st_gid != required_gid
                        or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o644}
                        or metadata.st_nlink != 1
                    ):
                        raise WitnessPublicStageError(
                            "candidate Git metadata file is unsafe"
                        )
                    continue
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != required_uid
                    or metadata.st_gid != required_gid
                    or stat.S_IMODE(metadata.st_mode) not in {0o644, 0o755}
                    or metadata.st_nlink != 1
                ):
                    raise WitnessPublicStageError(
                        f"candidate release entry is unsafe: {relative}"
                    )
                files[relative] = _metadata_identity(metadata)
    except OSError as exc:
        raise WitnessPublicStageError(
            "candidate release tree cannot be safely scanned"
        ) from exc
    return files


def _verify_git_release(
    root: Path,
    *,
    release_sha: str,
    runner: CommandRunner,
    required_uid: int,
    required_gid: int,
) -> tuple[str, dict[str, tuple[int, ...]], tuple[int, ...]]:
    root_before = _assert_trusted_directory(
        root,
        required_uid=required_uid,
        required_gid=required_gid,
        exact_mode=None,
        label="candidate release root",
    )
    files_before = _scan_candidate_tree(
        root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    top_level = _run_exact(
        runner,
        _git_command(root, "rev-parse", "--show-toplevel"),
    )
    if (
        top_level.stderr
        or top_level.stdout != (str(root) + "\n").encode("utf-8")
    ):
        raise WitnessPublicStageError(
            "candidate release is not the exact Git top-level"
        )
    head = _run_exact(
        runner,
        _git_command(root, "rev-parse", "--verify", "HEAD^{commit}"),
    )
    if head.stderr or head.stdout != (release_sha + "\n").encode("ascii"):
        raise WitnessPublicStageError(
            "candidate release Git HEAD differs"
        )
    tree = _run_exact(
        runner,
        _git_command(root, "rev-parse", "--verify", "HEAD^{tree}"),
    )
    try:
        tree_sha = tree.stdout.rstrip(b"\n").decode("ascii")
    except UnicodeDecodeError as exc:
        raise WitnessPublicStageError(
            "candidate release tree identity is invalid"
        ) from exc
    if (
        tree.stderr
        or tree.stdout != (tree_sha + "\n").encode("ascii")
        or SHA40_RE.fullmatch(tree_sha) is None
        or tree_sha == "0" * 40
    ):
        raise WitnessPublicStageError(
            "candidate release tree identity is invalid"
        )
    detached = _run_exact(
        runner,
        _git_command(root, "symbolic-ref", "-q", "HEAD"),
        expected_returncode=1,
    )
    if detached.stdout or detached.stderr:
        raise WitnessPublicStageError(
            "candidate release must have a detached Git HEAD"
        )
    clean = _run_exact(
        runner,
        _git_command(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
    )
    if clean.stdout or clean.stderr:
        raise WitnessPublicStageError(
            "candidate release Git worktree is not clean"
        )
    index = _run_exact(
        runner,
        _git_command(root, "ls-files", "--stage", "-z"),
    )
    if index.stderr:
        raise WitnessPublicStageError("candidate Git index emitted errors")
    index_entries = _parse_git_index(index.stdout)
    if set(index_entries) != set(files_before):
        raise WitnessPublicStageError(
            "candidate filesystem differs from the Git index"
        )
    for relative, git_mode in index_entries.items():
        file_mode = stat.S_IMODE(files_before[relative][2])
        expected_mode = 0o755 if git_mode == 0o100755 else 0o644
        if file_mode != expected_mode:
            raise WitnessPublicStageError(
                f"candidate file mode differs from Git: {relative}"
            )
    return tree_sha, files_before, _metadata_identity(root_before)


def _sha256_candidate_file(
    path: Path,
    *,
    relative: str,
    expected_metadata: tuple[int, ...],
    required_uid: int,
    required_gid: int,
) -> str:
    mode = stat.S_IMODE(expected_metadata[2])
    payload, observed_metadata = _read_stable_public_file(
        path,
        label=f"candidate release file {relative}",
        required_uid=required_uid,
        required_gid=required_gid,
        allowed_modes=frozenset({mode}),
        maximum_bytes=64 * 1024 * 1024,
    )
    if observed_metadata != expected_metadata:
        raise WitnessPublicStageError(
            f"candidate release file changed: {relative}"
        )
    return hashlib.sha256(payload).hexdigest()


def _resolve_active_release(
    active_root: Path,
    *,
    required_uid: int,
    required_gid: int,
) -> tuple[Path, tuple[int, ...]]:
    try:
        pointer = active_root.lstat()
        resolved = active_root.resolve(strict=True)
        resolved_metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise WitnessPublicStageError(
            "active native release pointer is unavailable"
        ) from exc
    if (
        pointer.st_uid != required_uid
        or pointer.st_gid != required_gid
        or not (
            stat.S_ISLNK(pointer.st_mode)
            or stat.S_ISDIR(pointer.st_mode)
        )
        or not stat.S_ISDIR(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != required_uid
        or resolved_metadata.st_gid != required_gid
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o022
    ):
        raise WitnessPublicStageError(
            "active native release pointer is unsafe"
        )
    return resolved, _metadata_identity(pointer)


def _load_active_manifest(
    active_root: Path,
    *,
    required_uid: int,
    required_gid: int,
) -> tuple[Path, bytes, dict[str, str], tuple[int, ...]]:
    resolved, pointer_identity = _resolve_active_release(
        active_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    raw, manifest_identity = _read_stable_public_file(
        resolved / "release-manifest.json",
        label="active native release manifest",
        required_uid=required_uid,
        required_gid=required_gid,
        allowed_modes=frozenset({0o644}),
    )
    manifest = _parse_native_release_manifest(raw)
    resolved_after, pointer_after = _resolve_active_release(
        active_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    if resolved_after != resolved or pointer_after != pointer_identity:
        raise WitnessPublicStageError(
            "active native release pointer changed during attestation"
        )
    return resolved, raw, manifest, manifest_identity


def _assert_native_subset_unchanged(
    candidate_root: Path,
    *,
    candidate_files: Mapping[str, tuple[int, ...]],
    active_manifest: Mapping[str, str],
    required_uid: int,
    required_gid: int,
) -> None:
    expected_targets = set(SOURCE_TO_NATIVE_RELEASE.values())
    if set(active_manifest) != expected_targets:
        raise WitnessPublicStageError(
            "active native release manifest differs from the reviewed subset"
        )
    if not set(SOURCE_TO_NATIVE_RELEASE).issubset(candidate_files):
        raise WitnessPublicStageError(
            "candidate release is missing a reviewed Witness source"
        )
    observed_targets: dict[str, str] = {}
    for source, target in sorted(SOURCE_TO_NATIVE_RELEASE.items()):
        digest = _sha256_candidate_file(
            candidate_root / source,
            relative=source,
            expected_metadata=candidate_files[source],
            required_uid=required_uid,
            required_gid=required_gid,
        )
        if target in observed_targets:
            raise WitnessPublicStageError(
                "reviewed Witness source mapping has a duplicate target"
            )
        observed_targets[target] = digest
    if observed_targets != dict(active_manifest):
        raise WitnessPublicStageError(
            "candidate Witness subset differs from the active native release"
        )


def _default_http_probe(path: str, timeout: float) -> HttpObservation:
    connection = http.client.HTTPConnection(
        LOOPBACK_HOST,
        LOOPBACK_HTTP_PORT,
        timeout=timeout,
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json",
                "Connection": "close",
                "Host": f"{LOOPBACK_HOST}:{LOOPBACK_HTTP_PORT}",
            },
        )
        response = connection.getresponse()
        body = response.read(MAX_HTTP_BODY_BYTES + 1)
        return HttpObservation(
            status_code=response.status,
            content_type=response.getheader("Content-Type", ""),
            body=body,
        )
    except (OSError, http.client.HTTPException, socket.timeout) as exc:
        raise WitnessPublicStageError(
            f"loopback health probe failed: {path}"
        ) from exc
    finally:
        connection.close()


def _default_tls_probe(
    ca_pem: bytes,
    server_name: str,
    timeout: float,
) -> TlsObservation:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cadata=ca_pem.decode("ascii"))
        with socket.create_connection(
            (LOOPBACK_HOST, LOOPBACK_TLS_PORT),
            timeout=timeout,
        ) as raw_socket:
            with context.wrap_socket(
                raw_socket,
                server_hostname=server_name,
            ) as tls_socket:
                peer = tls_socket.getpeercert(binary_form=True)
                protocol = tls_socket.version() or ""
                cipher_tuple = tls_socket.cipher()
                cipher = cipher_tuple[0] if cipher_tuple else ""
    except (OSError, UnicodeDecodeError, ssl.SSLError) as exc:
        raise WitnessPublicStageError(
            "loopback TLS certificate probe failed"
        ) from exc
    if not peer:
        raise WitnessPublicStageError(
            "loopback TLS peer certificate is missing"
        )
    return TlsObservation(
        peer_certificate_der=peer,
        protocol=protocol,
        cipher=cipher,
    )


def _certificate_times(
    certificate: x509.Certificate,
) -> tuple[datetime, datetime]:
    if hasattr(certificate, "not_valid_before_utc"):
        not_before = certificate.not_valid_before_utc
        not_after = certificate.not_valid_after_utc
    else:
        not_before = certificate.not_valid_before.replace(
            tzinfo=timezone.utc
        )
        not_after = certificate.not_valid_after.replace(
            tzinfo=timezone.utc
        )
    return not_before, not_after


def _public_key_is_strong(key: Any) -> bool:
    if isinstance(key, rsa.RSAPublicKey):
        return key.key_size >= 3072
    if isinstance(key, ec.EllipticCurvePublicKey):
        return key.key_size >= 256
    return isinstance(
        key,
        (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey),
    )


def _validate_tls_observation(
    ca_pem: bytes,
    observation: TlsObservation,
    *,
    server_name: str,
    now: datetime,
) -> tuple[str, str]:
    if (
        not isinstance(observation.peer_certificate_der, bytes)
        or not observation.peer_certificate_der
        or len(observation.peer_certificate_der) > MAX_PUBLIC_FILE_BYTES
        or not isinstance(observation.protocol, str)
        or observation.protocol not in {"TLSv1.2", "TLSv1.3"}
        or not isinstance(observation.cipher, str)
        or not observation.cipher
        or len(observation.cipher) > 128
    ):
        raise WitnessPublicStageError(
            "loopback TLS observation is invalid"
        )
    private_markers = (
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"-----BEGIN EC PRIVATE KEY-----",
        b"-----BEGIN OPENSSH PRIVATE KEY-----",
    )
    if (
        any(marker in ca_pem for marker in private_markers)
        or ca_pem.count(b"-----BEGIN CERTIFICATE-----") != 1
        or ca_pem.count(b"-----END CERTIFICATE-----") != 1
    ):
        raise WitnessPublicStageError(
            "Witness CA input is not one public certificate"
        )
    try:
        ca = x509.load_pem_x509_certificate(ca_pem)
        server = x509.load_der_x509_certificate(
            observation.peer_certificate_der
        )
        ca_basic = ca.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        server_basic = server.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        server_eku = server.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
        server_san = server.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise WitnessPublicStageError(
            "Witness TLS certificate extensions are invalid"
        ) from exc
    if (
        not ca_basic.ca
        or ca.subject != ca.issuer
        or server_basic.ca
        or ExtendedKeyUsageOID.SERVER_AUTH not in server_eku
        or not _public_key_is_strong(ca.public_key())
        or not _public_key_is_strong(server.public_key())
    ):
        raise WitnessPublicStageError(
            "Witness TLS certificate purpose is invalid"
        )
    for certificate in (ca, server):
        algorithm = certificate.signature_hash_algorithm
        if (
            algorithm is not None
            and algorithm.name not in {"sha256", "sha384", "sha512"}
        ):
            raise WitnessPublicStageError(
                "Witness TLS certificate signature algorithm is unsafe"
            )
    try:
        ca.verify_directly_issued_by(ca)
        server.verify_directly_issued_by(ca)
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise WitnessPublicStageError(
            "Witness TLS certificate chain is invalid"
        ) from exc
    try:
        expected_ip = ipaddress.ip_address(server_name)
    except ValueError as exc:
        raise WitnessPublicStageError(
            "Witness TLS server name must be a canonical IP address"
        ) from exc
    if str(expected_ip) != server_name:
        raise WitnessPublicStageError(
            "Witness TLS server name must be a canonical IP address"
        )
    if expected_ip not in server_san.get_values_for_type(
        x509.IPAddress
    ):
        raise WitnessPublicStageError(
            "Witness TLS certificate SAN differs"
        )
    for label, certificate in (("CA", ca), ("server", server)):
        not_before, not_after = _certificate_times(certificate)
        if (
            not_before > now
            or (
                not_after - now
            ).total_seconds() < MIN_CERTIFICATE_REMAINING_SECONDS
        ):
            raise WitnessPublicStageError(
                f"Witness {label} certificate is not safely valid"
            )
    canonical_ca = ca.public_bytes(serialization.Encoding.PEM)
    if ca_pem != canonical_ca:
        raise WitnessPublicStageError(
            "Witness CA certificate is not canonical PEM"
        )
    server_pem = server.public_bytes(serialization.Encoding.PEM)
    return (
        hashlib.sha256(canonical_ca).hexdigest(),
        hashlib.sha256(server_pem).hexdigest(),
    )


def _health_evidence(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    release_manifest_sha256: str,
    ca_pem: bytes,
    server_name: str,
    observed_at_epoch: int,
    now: datetime,
    command_runner: CommandRunner,
    http_probe: HttpProbe,
    tls_probe: TlsProbe,
) -> tuple[dict[str, Any], str, str]:
    systemd = _run_exact(command_runner, SYSTEMD_COMMAND)
    if systemd.stdout != b"active\n" or systemd.stderr:
        raise WitnessPublicStageError(
            "writer-witness systemd unit is not exactly active"
        )
    http_rows: dict[str, dict[str, Any]] = {}
    for path, expected_body in HEALTH_EXPECTATIONS.items():
        observation = http_probe(path, HEALTH_TIMEOUT_SECONDS)
        if (
            not isinstance(observation, HttpObservation)
            or isinstance(observation.status_code, bool)
            or observation.status_code != 200
            or observation.content_type != "application/json"
            or not isinstance(observation.body, bytes)
            or observation.body != expected_body
            or len(observation.body) > MAX_HTTP_BODY_BYTES
        ):
            raise WitnessPublicStageError(
                f"Witness loopback health response differs: {path}"
            )
        http_rows[path] = {
            "status_code": observation.status_code,
            "content_type": observation.content_type,
            "body_sha256": hashlib.sha256(
                observation.body
            ).hexdigest(),
            "body_bytes": len(observation.body),
        }
    tls = tls_probe(ca_pem, server_name, HEALTH_TIMEOUT_SECONDS)
    if not isinstance(tls, TlsObservation):
        raise WitnessPublicStageError(
            "loopback TLS probe result is invalid"
        )
    ca_sha256, server_sha256 = _validate_tls_observation(
        ca_pem,
        tls,
        server_name=server_name,
        now=now,
    )
    document = {
        "schema": HEALTH_ATTESTATION_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "release_manifest_sha256": release_manifest_sha256,
        "observed_at_epoch": observed_at_epoch,
        "systemd": {
            "argv": list(SYSTEMD_COMMAND),
            "returncode": 0,
            "stdout_sha256": hashlib.sha256(systemd.stdout).hexdigest(),
            "stdout_bytes": len(systemd.stdout),
            "stderr_sha256": hashlib.sha256(systemd.stderr).hexdigest(),
            "stderr_bytes": len(systemd.stderr),
            "active_state": "active",
        },
        "loopback_http": http_rows,
        "loopback_tls": {
            "host": LOOPBACK_HOST,
            "port": LOOPBACK_TLS_PORT,
            "server_name": server_name,
            "protocol": tls.protocol,
            "cipher": tls.cipher,
            "ca_sha256": ca_sha256,
            "server_cert_sha256": server_sha256,
            "certificate_encoding": "canonical-pem",
        },
    }
    if set(document) != HEALTH_ATTESTATION_FIELDS:
        raise WitnessPublicStageError(
            "health attestation fields are not exact"
        )
    return document, ca_sha256, server_sha256


def _assert_fresh_epoch(
    observed_at_epoch: int,
    *,
    now: datetime,
) -> None:
    if (
        isinstance(observed_at_epoch, bool)
        or not isinstance(observed_at_epoch, int)
        or not 1 <= observed_at_epoch <= 4_102_444_800
    ):
        raise WitnessPublicStageError(
            "health observation epoch is invalid"
        )
    age = now.timestamp() - observed_at_epoch
    if age < -MAX_HEALTH_FUTURE_SKEW_SECONDS:
        raise WitnessPublicStageError(
            "health observation is from the future"
        )
    if age > MAX_HEALTH_AGE_SECONDS:
        raise WitnessPublicStageError(
            "health observation is stale"
        )


def _assert_output_directory(
    path: Path,
    *,
    required_uid: int,
    required_gid: int,
) -> None:
    _assert_trusted_directory(
        path,
        required_uid=required_uid,
        required_gid=required_gid,
        exact_mode=0o700,
        label="Witness public stage output directory",
    )
    for name in (
        HEALTH_ATTESTATION_NAME,
        PUBLIC_INPUT_NAME,
        STAGE_OPERATION_NAME,
        CONTROLLER_BINDING_NAME,
    ):
        try:
            (path / name).lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WitnessPublicStageError(
                "cannot inspect Witness public stage output"
            ) from exc
        raise WitnessPublicStageError(
            f"create-only output already exists: {name}"
        )


def _publish_output(
    path: Path,
    payload: bytes,
    *,
    required_uid: int,
) -> None:
    if os.geteuid() != required_uid:
        raise WitnessPublicStageError(
            "producer effective uid differs from the required owner"
        )
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=f"Witness public stage output {path.name}",
            mode=0o600,
            max_size=MAX_PUBLIC_FILE_BYTES,
        )
    except SecureFileError as exc:
        raise WitnessPublicStageError(str(exc)) from exc


def produce_witness_public_stage(
    *,
    operation_id: str,
    release_sha: str,
    release_root: Path,
    release_prefix: Path,
    active_release_root: Path,
    ca_certificate: Path,
    witness_tls_server_name: str,
    output_directory: Path,
    required_uid: int = 0,
    required_gid: int = 0,
    command_runner: CommandRunner = _default_command_runner,
    http_probe: HttpProbe = _default_http_probe,
    tls_probe: TlsProbe = _default_tls_probe,
    now: datetime | None = None,
    observed_at_epoch: int | None = None,
    checkpoint: Checkpoint = _noop_checkpoint,
) -> dict[str, Any]:
    operation_id = _canonical_operation_id(operation_id)
    release_sha = _release_sha(release_sha)
    if (
        isinstance(required_uid, bool)
        or isinstance(required_gid, bool)
        or required_uid < 0
        or required_gid < 0
    ):
        raise WitnessPublicStageError(
            "required owner identity is invalid"
        )
    if not all(
        path.is_absolute()
        for path in (
            release_root,
            release_prefix,
            active_release_root,
            ca_certificate,
            output_directory,
        )
    ):
        raise WitnessPublicStageError(
            "Witness public stage paths must be absolute"
        )
    expected_release_root = release_prefix / release_sha
    if release_root != expected_release_root:
        raise WitnessPublicStageError(
            "candidate release root is outside the exact staged path"
        )
    injected_now = now is not None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise WitnessPublicStageError(
            "reference time must be timezone-aware"
        )
    current = current.astimezone(timezone.utc)
    observed_epoch = (
        int(current.timestamp())
        if observed_at_epoch is None
        else observed_at_epoch
    )
    _assert_fresh_epoch(observed_epoch, now=current)
    _assert_output_directory(
        output_directory,
        required_uid=required_uid,
        required_gid=required_gid,
    )

    (
        release_tree_sha,
        candidate_before,
        candidate_root_identity,
    ) = _verify_git_release(
        release_root,
        release_sha=release_sha,
        runner=command_runner,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    (
        active_resolved,
        active_manifest_raw,
        active_manifest,
        active_manifest_identity,
    ) = _load_active_manifest(
        active_release_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    _assert_native_subset_unchanged(
        release_root,
        candidate_files=candidate_before,
        active_manifest=active_manifest,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    checkpoint("after-release-subset")
    candidate_after = _scan_candidate_tree(
        release_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    if candidate_after != candidate_before:
        raise WitnessPublicStageError(
            "candidate release changed during attestation"
        )
    active_manifest_after, active_manifest_identity_after = (
        _read_stable_public_file(
            active_resolved / "release-manifest.json",
            label="active native release manifest",
            required_uid=required_uid,
            required_gid=required_gid,
            allowed_modes=frozenset({0o644}),
        )
    )
    if (
        active_manifest_after != active_manifest_raw
        or active_manifest_identity_after != active_manifest_identity
    ):
        raise WitnessPublicStageError(
            "active native release manifest changed during attestation"
        )
    active_resolved_after, _ = _resolve_active_release(
        active_release_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    if active_resolved_after != active_resolved:
        raise WitnessPublicStageError(
            "active native release pointer changed during attestation"
        )

    ca_pem, ca_identity = _read_stable_public_file(
        ca_certificate,
        label="Witness public CA certificate",
        required_uid=required_uid,
        required_gid=required_gid,
        allowed_modes=frozenset({0o600, 0o644}),
    )
    release_manifest_sha256 = hashlib.sha256(
        active_manifest_raw
    ).hexdigest()
    health_document, ca_sha256, server_sha256 = _health_evidence(
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        release_manifest_sha256=release_manifest_sha256,
        ca_pem=ca_pem,
        server_name=witness_tls_server_name,
        observed_at_epoch=observed_epoch,
        now=current,
        command_runner=command_runner,
        http_probe=http_probe,
        tls_probe=tls_probe,
    )
    health_bytes = _canonical_json(health_document)
    health_sha256 = hashlib.sha256(health_bytes).hexdigest()

    public_document = {
        "schema": PUBLIC_INPUT_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "release_manifest_sha256": release_manifest_sha256,
        "health_attestation_sha256": health_sha256,
        "health_attested_at_epoch": observed_epoch,
        "ca_sha256": ca_sha256,
        "server_cert_sha256": server_sha256,
        "native_release_reused": True,
        "current_mutated": False,
        "service_mutated": False,
        "legacy_secret_material_copied": False,
    }
    if set(public_document) != PUBLIC_INPUT_FIELDS:
        raise WitnessPublicStageError(
            "Witness public prepare input fields are not exact"
        )
    public_bytes = _canonical_json(public_document)
    stage_attestation_sha256 = hashlib.sha256(public_bytes).hexdigest()

    stage_document = {
        "schema": STAGE_OPERATION_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "candidate_release_root": str(release_root),
        "active_native_release_root": str(active_resolved),
        "release_manifest_sha256": release_manifest_sha256,
        "release_subset_entries": len(active_manifest),
        "health_attestation_sha256": health_sha256,
        "stage_attestation_sha256": stage_attestation_sha256,
        "health_attested_at_epoch": observed_epoch,
        "native_release_reused": True,
        "current_mutated": False,
        "service_mutated": False,
        "legacy_secret_material_copied": False,
        "runtime_image_ids": {},
    }
    if set(stage_document) != STAGE_OPERATION_FIELDS:
        raise WitnessPublicStageError(
            "Witness stage operation manifest fields are not exact"
        )
    stage_bytes = _canonical_json(stage_document)
    stage_sha256 = hashlib.sha256(stage_bytes).hexdigest()
    controller_binding = {
        "schema": CONTROLLER_BINDING_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": "witness",
        "stage_operation_manifest_sha256": stage_sha256,
        "stage_attestation_sha256": stage_attestation_sha256,
        "runtime_image_ids": {},
    }
    if set(controller_binding) != CONTROLLER_BINDING_FIELDS:
        raise WitnessPublicStageError(
            "Witness controller stage binding fields are not exact"
        )
    controller_binding_bytes = _canonical_json(controller_binding)
    controller_binding_sha256 = hashlib.sha256(
        controller_binding_bytes
    ).hexdigest()

    checkpoint("before-publish")
    final_root = _assert_trusted_directory(
        release_root,
        required_uid=required_uid,
        required_gid=required_gid,
        exact_mode=None,
        label="candidate release root",
    )
    if _metadata_identity(final_root) != candidate_root_identity:
        raise WitnessPublicStageError(
            "candidate release root changed during attestation"
        )
    candidate_final = _scan_candidate_tree(
        release_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    if candidate_final != candidate_before:
        raise WitnessPublicStageError(
            "candidate release changed before publication"
        )
    final_active_manifest, final_active_identity = (
        _read_stable_public_file(
            active_resolved / "release-manifest.json",
            label="active native release manifest",
            required_uid=required_uid,
            required_gid=required_gid,
            allowed_modes=frozenset({0o644}),
        )
    )
    final_active_root, _ = _resolve_active_release(
        active_release_root,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    if (
        final_active_manifest != active_manifest_raw
        or final_active_identity != active_manifest_identity
        or final_active_root != active_resolved
    ):
        raise WitnessPublicStageError(
            "active native release changed before publication"
        )
    final_ca, final_ca_identity = _read_stable_public_file(
        ca_certificate,
        label="Witness public CA certificate",
        required_uid=required_uid,
        required_gid=required_gid,
        allowed_modes=frozenset({0o600, 0o644}),
    )
    if final_ca != ca_pem or final_ca_identity != ca_identity:
        raise WitnessPublicStageError(
            "Witness public CA changed before publication"
        )
    publication_now = (
        current
        if injected_now
        else datetime.now(timezone.utc)
    )
    _assert_fresh_epoch(observed_epoch, now=publication_now)
    _publish_output(
        output_directory / HEALTH_ATTESTATION_NAME,
        health_bytes,
        required_uid=required_uid,
    )
    _publish_output(
        output_directory / PUBLIC_INPUT_NAME,
        public_bytes,
        required_uid=required_uid,
    )
    _publish_output(
        output_directory / STAGE_OPERATION_NAME,
        stage_bytes,
        required_uid=required_uid,
    )
    _publish_output(
        output_directory / CONTROLLER_BINDING_NAME,
        controller_binding_bytes,
        required_uid=required_uid,
    )
    outputs = {
        HEALTH_ATTESTATION_NAME: {
            "sha256": health_sha256,
            "bytes": len(health_bytes),
        },
        PUBLIC_INPUT_NAME: {
            "sha256": stage_attestation_sha256,
            "bytes": len(public_bytes),
        },
        STAGE_OPERATION_NAME: {
            "sha256": stage_sha256,
            "bytes": len(stage_bytes),
        },
        CONTROLLER_BINDING_NAME: {
            "sha256": controller_binding_sha256,
            "bytes": len(controller_binding_bytes),
        },
    }
    summary = {
        "schema": SUMMARY_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "native_release_reused": True,
        "outputs": outputs,
        "stage_binding": controller_binding,
    }
    return summary


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a non-negative integer"
        ) from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(
            "must be a non-negative integer"
        )
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument(
        "--release-root",
        type=Path,
        help=(
            "defaults to /srv/trading-bot-three-site/releases/RELEASE_SHA"
        ),
    )
    parser.add_argument(
        "--active-release-root",
        type=Path,
        default=ACTIVE_RELEASE_ROOT,
    )
    parser.add_argument(
        "--ca-certificate",
        type=Path,
        default=CA_CERTIFICATE_PATH,
    )
    parser.add_argument("--witness-tls-server-name", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--required-uid",
        type=_non_negative_integer,
        default=0,
    )
    parser.add_argument(
        "--required-gid",
        type=_non_negative_integer,
        default=0,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    release_root = args.release_root or (
        STAGED_RELEASE_PREFIX / args.release_sha
    )
    try:
        summary = produce_witness_public_stage(
            operation_id=args.operation_id,
            release_sha=args.release_sha,
            release_root=release_root,
            release_prefix=STAGED_RELEASE_PREFIX,
            active_release_root=args.active_release_root,
            ca_certificate=args.ca_certificate,
            witness_tls_server_name=args.witness_tls_server_name,
            output_directory=args.output_directory,
            required_uid=args.required_uid,
            required_gid=args.required_gid,
        )
    except WitnessPublicStageError as exc:
        print(
            _canonical_json(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                }
            ).decode("ascii")
        )
        return 2
    print(_canonical_json(summary).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
