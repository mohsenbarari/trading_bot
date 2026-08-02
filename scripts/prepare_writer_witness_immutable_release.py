#!/usr/bin/env python3
"""Prepare and preflight a pinned, non-secret Writer Witness release package.

This utility deliberately has no network, S3, SSH, service-manager, Docker, or
activation operations.  It turns one reviewed source commit into a detached
source-tree archive plus a timing profile and records their hashes.  The
resulting directory is an input to a later, separately authorised transport
and host activation transaction; it is not an installed or running release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PINNED_SOURCE_COMMIT = "a0d8fa5a3b696ecfee3c0e787ea0791d035b1f32"
PROFILE_SCHEMA = "gold-trade-writer-witness-release-profile-v2"
PACKAGE_SCHEMA = "gold-trade-writer-witness-release-package-v1"
TIMING_ATTESTATION_SCHEMA = "gold-trade-writer-witness-client-timing-attestation-v1"
PAIRED_TIMING_ATTESTATION_SCHEMA = "gold-trade-writer-witness-paired-client-timing-attestation-v1"
AGENT_CONFIG_SCHEMA = "production-writer-lease-agent-v1"
SOURCE_ARCHIVE_PREFIX = "writer-witness-source"
MAX_CONTROL_FILE_BYTES = 1024 * 1024
MAX_AGENT_CONFIG_BYTES = 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_GIT_POINTER_BYTES = 16 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")

DEFAULT_PROFILE_PATH = (
    REPOSITORY_ROOT / "deploy/production/writer-witness-60s-release.json"
)
DEFAULT_RUNTIME_TEMPLATE_PATH = (
    REPOSITORY_ROOT / "deploy/production/writer-witness-runtime-60s.env.example"
)

# The historic service source is intentionally not copied into the WebApp
# control release.  These files prove that the detached archive contains the
# reviewed Witness implementation and enough release assets to build it later.
REQUIRED_SOURCE_PATHS = (
    "writer_witness_app.py",
    "tests/test_writer_witness_service.py",
    "tests/test_render_writer_witness_credentials.py",
    "scripts/build_writer_witness_release.sh",
    "scripts/provision_writer_witness_host.sh",
    "scripts/render_writer_witness_credentials.py",
    "scripts/verify_writer_witness_release.py",
    "deploy/production/writer-witness-runtime.env.example",
    "deploy/writer-witness/001_initial.sql",
    "deploy/writer-witness/002_failover_operation_ledger.sql",
    "deploy/writer-witness/003_human_approval_relay.sql",
    "deploy/writer-witness/python-runtime.json",
    "deploy/writer-witness/requirements.lock",
    "deploy/writer-witness/wheelhouse.sha256",
    "deploy/writer-witness/writer-witness-runtime-profile.json",
    "deploy/writer-witness/nginx.conf.template",
    "deploy/writer-witness/writer-witness.service",
    "core/runtime_sites.py",
    "core/writer_witness_auth.py",
    "core/writer_witness_contract.py",
    "core/writer_witness_control.py",
)


class WitnessReleasePreparationError(RuntimeError):
    """The release package or timing compatibility cannot be proven safe."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WitnessReleasePreparationError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
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


def _require_absolute_file(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise WitnessReleasePreparationError(f"{field} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise WitnessReleasePreparationError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WitnessReleasePreparationError(f"{field} must be one canonical regular file")
    if metadata.st_size < 1 or metadata.st_size > MAX_CONTROL_FILE_BYTES:
        raise WitnessReleasePreparationError(f"{field} has an unsafe size")
    if metadata.st_mode & 0o022:
        raise WitnessReleasePreparationError(f"{field} is writable by a non-owner")
    return resolved


def _read_controlled_file(path: Path, *, field: str, root_only: bool) -> bytes:
    path = _require_absolute_file(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WitnessReleasePreparationError(f"cannot safely open {field}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_AGENT_CONFIG_BYTES
            or before.st_mode & (0o077 if root_only else 0o022)
        ):
            raise WitnessReleasePreparationError(f"{field} has unsafe ownership or mode")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
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
        if len(result) != before.st_size or any(
            getattr(before, name) != getattr(after, name) for name in identity
        ):
            raise WitnessReleasePreparationError(f"{field} changed while being read")
        return result
    finally:
        os.close(descriptor)


def _require_root_owned_directory(
    path: Path,
    *,
    field: str,
    private: bool,
    allow_indirect: bool = False,
) -> Path:
    """Resolve one trusted directory without accepting an unsafe ownership boundary."""

    if not path.is_absolute():
        raise WitnessReleasePreparationError(f"{field} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise WitnessReleasePreparationError(f"cannot inspect {field}") from exc
    if (
        (not allow_indirect and resolved != path)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
    ):
        raise WitnessReleasePreparationError(f"{field} must be one canonical non-symlink directory")
    disallowed_permissions = 0o077 if private else 0o022
    if resolved_metadata.st_uid != 0 or resolved_metadata.st_mode & disallowed_permissions:
        requirement = "private" if private else "non-group-or-other-writable"
        raise WitnessReleasePreparationError(f"{field} is not root-owned and {requirement}")
    return resolved


def _read_root_owned_nonwritable_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    """Read a root-owned control file while rejecting links and replacement races."""

    path = _require_absolute_file(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WitnessReleasePreparationError(f"cannot safely open {field}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or before.st_mode & 0o022
        ):
            raise WitnessReleasePreparationError(f"{field} has unsafe ownership or mode")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
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
        if len(result) != before.st_size or any(
            getattr(before, name) != getattr(after, name) for name in identity
        ):
            raise WitnessReleasePreparationError(f"{field} changed while being read")
        return result
    finally:
        os.close(descriptor)


def _resolve_worktree_git_directory(worktree: Path) -> Path:
    """Resolve either a `.git` directory or a normal worktree gitdir pointer."""

    entry = worktree / ".git"
    try:
        metadata = entry.lstat()
    except OSError as exc:
        raise WitnessReleasePreparationError("source worktree has no inspectable .git entry") from exc
    if stat.S_ISDIR(metadata.st_mode):
        return _require_root_owned_directory(
            entry,
            field="source Git directory",
            private=False,
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise WitnessReleasePreparationError("source worktree .git entry is neither a directory nor a file")
    pointer = _read_root_owned_nonwritable_file(
        entry,
        field="source worktree .git pointer",
        maximum_bytes=MAX_GIT_POINTER_BYTES,
    )
    if pointer.endswith(b"\n"):
        pointer = pointer[:-1]
    if (
        not pointer.startswith(b"gitdir: ")
        or b"\n" in pointer
        or b"\r" in pointer
        or b"\x00" in pointer
    ):
        raise WitnessReleasePreparationError("source worktree .git pointer is malformed")
    try:
        raw_target = pointer[len(b"gitdir: ") :].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WitnessReleasePreparationError("source worktree .git pointer is not UTF-8") from exc
    if not raw_target:
        raise WitnessReleasePreparationError("source worktree .git pointer has no target")
    target = Path(raw_target)
    if not target.is_absolute():
        target = entry.parent / target
    return _require_root_owned_directory(
        target,
        field="resolved source Git directory",
        private=False,
        allow_indirect=True,
    )


def _load_json_bytes(value: bytes, *, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessReleasePreparationError(f"{field} is not valid strict UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise WitnessReleasePreparationError(f"{field} must be a JSON object")
    return parsed


def _require_exact_fields(value: Mapping[str, Any], *, expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise WitnessReleasePreparationError(f"{field} fields do not match the approved schema")


def _require_int(value: object, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WitnessReleasePreparationError(f"{field} is invalid")
    return value


def _load_profile(path: Path) -> dict[str, Any]:
    payload = _load_json_bytes(
        _read_controlled_file(path, field="release profile", root_only=False),
        field="release profile",
    )
    _require_exact_fields(
        payload,
        expected={
            "schema",
            "release_id",
            "source_commit",
            "source_runtime_profile_sha256",
            "source_release_manifest_sha256",
            "witness",
            "webapp_fi_client",
            "webapp_ir_client",
        },
        field="release profile",
    )
    if payload.get("schema") != PROFILE_SCHEMA:
        raise WitnessReleasePreparationError("release profile schema is unsupported")
    release_id = payload.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise WitnessReleasePreparationError("release profile release_id is invalid")
    if payload.get("source_commit") != PINNED_SOURCE_COMMIT:
        raise WitnessReleasePreparationError("release profile does not pin the approved Witness source")
    source_runtime_profile_sha256 = payload.get("source_runtime_profile_sha256")
    if not isinstance(source_runtime_profile_sha256, str) or not SHA256_RE.fullmatch(
        source_runtime_profile_sha256
    ):
        raise WitnessReleasePreparationError("release profile source runtime profile hash is invalid")
    source_release_manifest_sha256 = payload.get("source_release_manifest_sha256")
    if not isinstance(source_release_manifest_sha256, str) or not SHA256_RE.fullmatch(
        source_release_manifest_sha256
    ):
        raise WitnessReleasePreparationError("release profile source release manifest hash is invalid")
    witness = payload.get("witness")
    if not isinstance(witness, dict):
        raise WitnessReleasePreparationError("release profile witness settings are invalid")
    _require_exact_fields(
        witness,
        expected={
            "logical_authority",
            "physical_site",
            "authoritative_site",
            "lease_duration_seconds",
            "enforce_configured_lease_duration",
            "renew_interval_seconds",
            "safety_margin_seconds",
            "max_clock_skew_seconds",
            "auth_max_age_seconds",
        },
        field="release profile witness",
    )
    if (
        witness.get("logical_authority") != "webapp"
        or witness.get("physical_site") != "witness"
        or witness.get("authoritative_site") != "webapp_fi"
        or witness.get("enforce_configured_lease_duration") is not True
    ):
        raise WitnessReleasePreparationError("release profile has an unsafe Witness identity or enforcement mode")
    duration = _require_int(
        witness.get("lease_duration_seconds"),
        field="release profile witness lease_duration_seconds",
        minimum=30,
        maximum=3600,
    )
    interval = _require_int(
        witness.get("renew_interval_seconds"),
        field="release profile witness renew_interval_seconds",
        minimum=1,
        maximum=3600,
    )
    margin = _require_int(
        witness.get("safety_margin_seconds"),
        field="release profile witness safety_margin_seconds",
        minimum=5,
        maximum=3600,
    )
    skew = _require_int(
        witness.get("max_clock_skew_seconds"),
        field="release profile witness max_clock_skew_seconds",
        minimum=0,
        maximum=60,
    )
    auth_age = _require_int(
        witness.get("auth_max_age_seconds"),
        field="release profile witness auth_max_age_seconds",
        minimum=1,
        maximum=60,
    )
    if duration != 60 or interval != 10 or margin != 15 or interval + margin >= duration:
        raise WitnessReleasePreparationError("release profile does not use the approved 60/10/15 timing")
    if auth_age <= skew:
        raise WitnessReleasePreparationError("release profile authentication window is unsafe")
    for client_name, expected_mode, expected_site in (
        ("webapp_fi_client", "fenced_fi_writer", "webapp_fi"),
        ("webapp_ir_client", "writer", "webapp_ir"),
    ):
        client = payload.get(client_name)
        label = "WebApp-FI" if expected_site == "webapp_fi" else "WebApp-IR"
        if not isinstance(client, dict):
            raise WitnessReleasePreparationError(
                f"release profile {label} client settings are invalid"
            )
        _require_exact_fields(
            client,
            expected={
                "mode",
                "site",
                "lease_duration_seconds",
                "renew_interval_seconds",
                "safety_margin_seconds",
            },
            field=f"release profile {label} client",
        )
        if client.get("mode") != expected_mode or client.get("site") != expected_site:
            raise WitnessReleasePreparationError(
                f"release profile does not bind the required {label} lease client"
            )
        for key, expected in (
            ("lease_duration_seconds", duration),
            ("renew_interval_seconds", interval),
            ("safety_margin_seconds", margin),
        ):
            if client.get(key) != expected:
                raise WitnessReleasePreparationError(
                    "release profile client timing differs from Witness timing"
                )
    return payload


def _git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    stdout: Any | None = subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        "/usr/bin/git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository),
        *arguments,
    ]
    try:
        return subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.PIPE,
            env=_git_environment(),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise WitnessReleasePreparationError("cannot verify the pinned Witness source repository") from exc


def _reported_git_directory(
    repository: Path,
    *,
    argument: str,
    field: str,
) -> Path:
    raw = _run_git(
        repository,
        ["rev-parse", argument, "--path-format=absolute"],
    ).stdout.strip()
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WitnessReleasePreparationError(f"{field} is not UTF-8") from exc
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise WitnessReleasePreparationError(f"{field} is malformed")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repository / candidate
    return _require_root_owned_directory(
        candidate,
        field=field,
        private=False,
        allow_indirect=True,
    )


def _require_source_repository(path: Path) -> tuple[Path, str]:
    repository = _require_root_owned_directory(
        path,
        field="source worktree",
        private=False,
    )
    pointer_git_directory = _resolve_worktree_git_directory(repository)
    inside = _run_git(repository, ["rev-parse", "--is-inside-work-tree"]).stdout.strip()
    if inside != b"true":
        raise WitnessReleasePreparationError("source repository is not a Git worktree")
    reported_git_directory = _reported_git_directory(
        repository,
        argument="--git-dir",
        field="reported source Git directory",
    )
    if reported_git_directory != pointer_git_directory:
        raise WitnessReleasePreparationError("source worktree Git directory does not match its .git entry")
    _reported_git_directory(
        repository,
        argument="--git-common-dir",
        field="resolved source common Git directory",
    )
    try:
        commit = (
            _run_git(repository, ["rev-parse", "--verify", PINNED_SOURCE_COMMIT + "^{commit}"])
            .stdout.strip()
            .decode("ascii")
        )
    except UnicodeDecodeError as exc:
        raise WitnessReleasePreparationError("approved Witness source commit is not ASCII") from exc
    if commit != PINNED_SOURCE_COMMIT or not COMMIT_RE.fullmatch(commit):
        raise WitnessReleasePreparationError("source repository lacks the approved Witness commit")
    try:
        tree = (
            _run_git(repository, ["rev-parse", PINNED_SOURCE_COMMIT + "^{tree}"])
            .stdout.strip()
            .decode("ascii")
        )
    except UnicodeDecodeError as exc:
        raise WitnessReleasePreparationError("approved Witness source tree identity is not ASCII") from exc
    if not COMMIT_RE.fullmatch(tree):
        raise WitnessReleasePreparationError("approved Witness source tree identity is invalid")
    return repository, tree


def _source_file_hashes(
    repository: Path,
    *,
    profile: Mapping[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in REQUIRED_SOURCE_PATHS:
        if PurePosixPath(relative).as_posix() != relative or relative.startswith("/") or ".." in PurePosixPath(relative).parts:
            raise WitnessReleasePreparationError("required source path is unsafe")
        payload = _run_git(repository, ["show", f"{PINNED_SOURCE_COMMIT}:{relative}"]).stdout
        if not payload:
            raise WitnessReleasePreparationError("approved Witness source file is unexpectedly empty")
        result[relative] = _sha256_bytes(payload)
    app_source = _run_git(repository, ["show", f"{PINNED_SOURCE_COMMIT}:writer_witness_app.py"]).stdout
    required_fragments = (
        b"writer_witness_enforce_configured_lease_duration",
        b"writer_witness_lease_duration_mismatch",
        b"ACTION_ACQUIRE",
        b"ACTION_RENEW",
        b"caller_site",
        b"caller_key_id_sha256",
        b'"contract_version": 2',
    )
    if not all(fragment in app_source for fragment in required_fragments):
        raise WitnessReleasePreparationError("approved Witness source lacks duration-enforcement code")
    runtime_profile_path = "deploy/writer-witness/writer-witness-runtime-profile.json"
    runtime_profile = _run_git(
        repository,
        ["show", f"{PINNED_SOURCE_COMMIT}:{runtime_profile_path}"],
    ).stdout
    if _sha256_bytes(runtime_profile) != profile["source_runtime_profile_sha256"]:
        raise WitnessReleasePreparationError(
            "approved Witness source runtime profile does not match the control profile"
        )
    source_profile = _load_json_bytes(
        runtime_profile,
        field="approved Witness source runtime profile",
    )
    witness = profile["witness"]
    expected_source_profile = {
        "schema": "gold-trade-writer-witness-runtime-profile-v1",
        "logical_authority": witness["logical_authority"],
        "physical_site": witness["physical_site"],
        "authoritative_site": witness["authoritative_site"],
        "lease_duration_seconds": witness["lease_duration_seconds"],
        "enforce_configured_lease_duration": witness["enforce_configured_lease_duration"],
        "renew_interval_seconds": witness["renew_interval_seconds"],
        "safety_margin_seconds": witness["safety_margin_seconds"],
        "max_clock_skew_seconds": witness["max_clock_skew_seconds"],
        "auth_max_age_seconds": witness["auth_max_age_seconds"],
    }
    if source_profile != expected_source_profile:
        raise WitnessReleasePreparationError(
            "approved Witness source runtime profile fields do not match the control profile"
        )
    renderer_source = _run_git(
        repository,
        ["show", f"{PINNED_SOURCE_COMMIT}:scripts/render_writer_witness_credentials.py"],
    ).stdout
    provisioner_source = _run_git(
        repository,
        ["show", f"{PINNED_SOURCE_COMMIT}:scripts/provision_writer_witness_host.sh"],
    ).stdout
    if (
        b"WRITER_WITNESS_ENFORCE_CONFIGURED_LEASE_DURATION=true" not in renderer_source
        or b"WRITER_WITNESS_RUNTIME_PROFILE_SHA256" not in renderer_source
        or b"--mode verify-runtime-profile" not in provisioner_source
    ):
        raise WitnessReleasePreparationError(
            "approved Witness source lacks candidate profile-binding verification"
        )
    return result


def _write_new_file(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, mode)
    except OSError as exc:
        raise WitnessReleasePreparationError(f"cannot create new package file: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Preserve a newly created partial artifact for forensic inspection.
        # A failed package is never reused because its directory is immutable
        # to this helper after the first create attempt.
        raise


def _archive_source_tree(repository: Path, destination: Path) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o600)
    except OSError as exc:
        raise WitnessReleasePreparationError("cannot create the Witness source archive") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _run_git(
                repository,
                [
                    "archive",
                    "--format=tar",
                    f"--prefix={SOURCE_ARCHIVE_PREFIX}/",
                    PINNED_SOURCE_COMMIT,
                ],
                stdout=handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Keep a failed archive in the new package directory. It may contain
        # the only useful evidence of a source/archive failure, and this
        # helper never deletes package artifacts automatically.
        raise
    digest, size = _sha256_file(destination)
    if size < 1 or size > MAX_SOURCE_ARCHIVE_BYTES:
        raise WitnessReleasePreparationError("Witness source archive has an unsafe size")
    return digest, size


def _verify_source_archive(
    archive: Path,
    *,
    expected_hashes: Mapping[str, str],
) -> None:
    observed: dict[str, str] = {}
    try:
        with tarfile.open(archive, mode="r:") as handle:
            for member in handle:
                pure = PurePosixPath(member.name)
                if (
                    not member.name
                    or member.name.startswith("/")
                    or "\\" in member.name
                    or any(part in ("", ".", "..") for part in pure.parts)
                    or pure.parts[0] != SOURCE_ARCHIVE_PREFIX
                ):
                    raise WitnessReleasePreparationError("Witness source archive contains an unsafe path")
                if member.isdir():
                    continue
                if not member.isfile() or member.linkname:
                    raise WitnessReleasePreparationError("Witness source archive contains a non-regular entry")
                relative = PurePosixPath(*pure.parts[1:]).as_posix()
                if relative in expected_hashes:
                    source = handle.extractfile(member)
                    if source is None:
                        raise WitnessReleasePreparationError("Witness source archive entry cannot be read")
                    digest = hashlib.sha256()
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    observed[relative] = digest.hexdigest()
    except (OSError, tarfile.TarError) as exc:
        raise WitnessReleasePreparationError("Witness source archive cannot be safely verified") from exc
    if observed != dict(expected_hashes):
        raise WitnessReleasePreparationError("Witness source archive does not match the approved source files")


def _require_new_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise WitnessReleasePreparationError("destination must be an absolute path")
    if path.exists() or path.is_symlink():
        raise WitnessReleasePreparationError("destination must not already exist")
    if not path.name or path.name in {".", ".."}:
        raise WitnessReleasePreparationError("destination name is invalid")
    parent = _require_root_owned_directory(
        path.parent,
        field="destination parent",
        private=True,
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        parent_descriptor = os.open(parent, flags)
    except OSError as exc:
        raise WitnessReleasePreparationError("destination parent cannot be safely opened") from exc
    try:
        parent_metadata = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != 0
            or parent_metadata.st_mode & 0o077
        ):
            raise WitnessReleasePreparationError("destination parent is no longer root-private")
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent_descriptor)
        except OSError as exc:
            raise WitnessReleasePreparationError("cannot create package destination") from exc
        child_flags = flags
        try:
            child_descriptor = os.open(path.name, child_flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise WitnessReleasePreparationError("cannot safely reopen package destination") from exc
        try:
            os.fchmod(child_descriptor, 0o700)
            child_metadata = os.fstat(child_descriptor)
            if (
                not stat.S_ISDIR(child_metadata.st_mode)
                or child_metadata.st_uid != 0
                or stat.S_IMODE(child_metadata.st_mode) != 0o700
            ):
                raise WitnessReleasePreparationError("new package destination has unsafe ownership or mode")
        finally:
            os.close(child_descriptor)
    finally:
        os.close(parent_descriptor)
    created = parent / path.name
    try:
        resolved = created.resolve(strict=True)
        metadata = created.lstat()
    except OSError as exc:
        raise WitnessReleasePreparationError("new package destination cannot be inspected") from exc
    if resolved != created or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise WitnessReleasePreparationError("new package destination is not one canonical directory")
    return resolved


def prepare_release_package(
    *,
    source_repository: Path,
    destination: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    runtime_template_path: Path = DEFAULT_RUNTIME_TEMPLATE_PATH,
) -> dict[str, Any]:
    """Build a detached package; it creates no host state outside destination."""

    profile = _load_profile(profile_path)
    profile_bytes = _read_controlled_file(profile_path, field="release profile", root_only=False)
    runtime_template_bytes = _read_controlled_file(
        runtime_template_path,
        field="runtime template",
        root_only=False,
    )
    repository, source_tree = _require_source_repository(source_repository)
    source_hashes = _source_file_hashes(repository, profile=profile)
    package = _require_new_directory(destination)
    source_archive_name = f"writer-witness-source-{PINNED_SOURCE_COMMIT}.tar"
    source_archive = package / source_archive_name
    archive_sha256, archive_bytes = _archive_source_tree(repository, source_archive)
    _verify_source_archive(source_archive, expected_hashes=source_hashes)
    profile_name = "writer-witness-60s-release.json"
    runtime_template_name = "writer-witness-runtime-60s.env.example"
    _write_new_file(package / profile_name, profile_bytes)
    _write_new_file(package / runtime_template_name, runtime_template_bytes)
    manifest: dict[str, Any] = {
        "schema": PACKAGE_SCHEMA,
        "release_id": profile["release_id"],
        "source": {
            "commit": PINNED_SOURCE_COMMIT,
            "tree": source_tree,
            "release_manifest_sha256": profile["source_release_manifest_sha256"],
            "archive": {
                "name": source_archive_name,
                "sha256": archive_sha256,
                "bytes": archive_bytes,
            },
            "required_files": dict(sorted(source_hashes.items())),
        },
        "profile": {
            "name": profile_name,
            "sha256": _sha256_bytes(profile_bytes),
        },
        "runtime_template": {
            "name": runtime_template_name,
            "sha256": _sha256_bytes(runtime_template_bytes),
        },
    }
    _write_new_file(package / "release-package.json", _canonical_json_bytes(manifest) + b"\n")
    return {
        "status": "prepared",
        "package_directory": str(package),
        "release_id": profile["release_id"],
        "source_commit": PINNED_SOURCE_COMMIT,
        "source_tree": source_tree,
        "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
        "source_archive_sha256": archive_sha256,
        "source_archive_bytes": archive_bytes,
        "profile_sha256": manifest["profile"]["sha256"],
        "runtime_template_sha256": manifest["runtime_template"]["sha256"],
    }


def _require_absolute_config_path(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\x00" in value
        or ".." in PurePosixPath(value).parts
    ):
        raise WitnessReleasePreparationError(f"{field} is invalid")
    return value


def _verify_webapp_client_timing(
    *,
    agent_config_path: Path,
    profile: Mapping[str, Any],
    client_name: str,
) -> dict[str, Any]:
    """Verify one local agent config without opening its secret material.

    This is deliberately static.  The separate client-attestation helper is
    responsible for the live TLS handshake and HMAC-authenticated Witness
    query using this site's own secret; this control helper never reads it.
    """

    if client_name not in {"webapp_fi_client", "webapp_ir_client"}:
        raise WitnessReleasePreparationError("requested WebApp client is unsupported")
    client = profile[client_name]
    site = client["site"]
    label = "WebApp-FI" if site == "webapp_fi" else "WebApp-IR"
    config = _load_json_bytes(
        _read_controlled_file(
            agent_config_path,
            field=f"{label} writer lease-agent config",
            root_only=True,
        ),
        field=f"{label} writer lease-agent config",
    )
    base_fields = {"schema", "mode", "site", "lease_file", "runtime", "witness"}
    expected_config_fields = base_fields | (
        {"fenced_preflight_config"} if site == "webapp_fi" else {"release_provenance"}
    )
    _require_exact_fields(
        config,
        expected=expected_config_fields,
        field=f"{label} writer lease-agent config",
    )
    if (
        config.get("schema") != AGENT_CONFIG_SCHEMA
        or config.get("mode") != client["mode"]
        or config.get("site") != site
    ):
        raise WitnessReleasePreparationError(f"{label} lease-agent identity is incompatible")
    _require_absolute_config_path(config.get("lease_file"), field=f"{label} lease file")
    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        raise WitnessReleasePreparationError(f"{label} managed runtime is invalid")
    _require_exact_fields(
        runtime,
        expected={"compose_file", "env_file", "selection_env_file", "services"},
        field=f"{label} managed runtime",
    )
    _require_absolute_config_path(runtime.get("compose_file"), field=f"{label} compose file")
    _require_absolute_config_path(runtime.get("env_file"), field=f"{label} runtime environment")
    if site == "webapp_fi":
        if runtime.get("selection_env_file") is not None:
            raise WitnessReleasePreparationError("WebApp-FI fenced client must not select a standby candidate")
        _require_absolute_config_path(
            config.get("fenced_preflight_config"),
            field="WebApp-FI fenced identity preflight config",
        )
    else:
        _require_absolute_config_path(
            runtime.get("selection_env_file"),
            field="WebApp-IR selected candidate environment",
        )
        provenance = config.get("release_provenance")
        if not isinstance(provenance, dict):
            raise WitnessReleasePreparationError("WebApp-IR release provenance is invalid")
        _require_exact_fields(
            provenance,
            expected={"receipt", "application_release_sha", "application_release_root"},
            field="WebApp-IR release provenance",
        )
        _require_absolute_config_path(provenance.get("receipt"), field="WebApp-IR provenance receipt")
        _require_absolute_config_path(
            provenance.get("application_release_root"),
            field="WebApp-IR application release root",
        )
        if not isinstance(provenance.get("application_release_sha"), str) or not COMMIT_RE.fullmatch(
            provenance["application_release_sha"]
        ):
            raise WitnessReleasePreparationError("WebApp-IR application release identity is invalid")

    witness = config.get("witness")
    if not isinstance(witness, dict):
        raise WitnessReleasePreparationError(f"{label} lease-agent Witness section is invalid")
    _require_exact_fields(
        witness,
        expected={
            "url",
            "key_id",
            "secret_file",
            "public_key_file",
            "ca_bundle",
            "timeout_seconds",
            "lease_duration_seconds",
            "safety_margin_seconds",
            "renew_interval_seconds",
        },
        field=f"{label} lease-agent Witness section",
    )
    for field in ("secret_file", "public_key_file", "ca_bundle"):
        _require_absolute_config_path(witness.get(field), field=f"{label} Witness {field}")
    if not isinstance(witness.get("url"), str) or not witness["url"].startswith("https://"):
        raise WitnessReleasePreparationError(f"{label} Witness URL is invalid")
    timeout = witness.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= float(timeout) <= 10:
        raise WitnessReleasePreparationError(f"{label} Witness timeout is invalid")
    timing: dict[str, int] = {}
    for key in ("lease_duration_seconds", "renew_interval_seconds", "safety_margin_seconds"):
        value = witness.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise WitnessReleasePreparationError(f"{label} Witness {key} is invalid")
        if value != client[key]:
            raise WitnessReleasePreparationError(
                f"{label} timing is incompatible with the 60-second Witness release"
            )
        timing[key] = value
    return {
        "schema": TIMING_ATTESTATION_SCHEMA,
        "release_id": profile["release_id"],
        "source_commit": profile["source_commit"],
        "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
        "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
        "site": site,
        "mode": client["mode"],
        "timing": timing,
        "compatible": True,
    }


def verify_webapp_fi_client_timing(
    *,
    agent_config_path: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> dict[str, Any]:
    """Backward-compatible static attestation for the fenced FI client."""

    return _verify_webapp_client_timing(
        agent_config_path=agent_config_path,
        profile=_load_profile(profile_path),
        client_name="webapp_fi_client",
    )


def verify_paired_webapp_client_timing(
    *,
    webapp_fi_agent_config_path: Path,
    webapp_ir_agent_config_path: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> dict[str, Any]:
    """Prove both local agents are statically compatible before live probing."""

    profile = _load_profile(profile_path)
    fi = _verify_webapp_client_timing(
        agent_config_path=webapp_fi_agent_config_path,
        profile=profile,
        client_name="webapp_fi_client",
    )
    ir = _verify_webapp_client_timing(
        agent_config_path=webapp_ir_agent_config_path,
        profile=profile,
        client_name="webapp_ir_client",
    )
    return {
        "schema": PAIRED_TIMING_ATTESTATION_SCHEMA,
        "release_id": profile["release_id"],
        "source_commit": profile["source_commit"],
        "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
        "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
        "clients": {
            "webapp_fi": {key: fi[key] for key in ("mode", "timing")},
            "webapp_ir": {key: ir[key] for key in ("mode", "timing")},
        },
        "compatible": True,
    }


def _write_optional_attestation(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.is_absolute():
        raise WitnessReleasePreparationError("attestation output must be an absolute path")
    _write_new_file(path, _canonical_json_bytes(payload) + b"\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="create a detached pinned source package")
    prepare.add_argument("--source-repository", type=Path, required=True)
    prepare.add_argument("--destination", type=Path, required=True)
    prepare.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    prepare.add_argument("--runtime-template", type=Path, default=DEFAULT_RUNTIME_TEMPLATE_PATH)
    verify = commands.add_parser("verify-client-timing", help="attest root-only WebApp-FI timing")
    verify.add_argument("--webapp-fi-agent-config", type=Path, required=True)
    verify.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    verify.add_argument("--output", type=Path)
    paired = commands.add_parser(
        "verify-paired-client-timing",
        help="attest root-only WebApp-FI and WebApp-IR timing together",
    )
    paired.add_argument("--webapp-fi-agent-config", type=Path, required=True)
    paired.add_argument("--webapp-ir-agent-config", type=Path, required=True)
    paired.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    paired.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            result = prepare_release_package(
                source_repository=arguments.source_repository,
                destination=arguments.destination,
                profile_path=arguments.profile,
                runtime_template_path=arguments.runtime_template,
            )
        elif arguments.command == "verify-client-timing":
            result = verify_webapp_fi_client_timing(
                agent_config_path=arguments.webapp_fi_agent_config,
                profile_path=arguments.profile,
            )
            if arguments.output is not None:
                _write_optional_attestation(arguments.output, result)
        else:
            result = verify_paired_webapp_client_timing(
                webapp_fi_agent_config_path=arguments.webapp_fi_agent_config,
                webapp_ir_agent_config_path=arguments.webapp_ir_agent_config,
                profile_path=arguments.profile,
            )
            if arguments.output is not None:
                _write_optional_attestation(arguments.output, result)
        print(_canonical_json_bytes(result).decode("ascii"))
        return 0
    except WitnessReleasePreparationError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
