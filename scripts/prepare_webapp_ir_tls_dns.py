#!/usr/bin/env python3
"""Prepare WA-IR public TLS without changing live routing or active Nginx.

The worker has deliberately narrow responsibilities:

* create a root-only private key and CSR on WA-IR;
* create/read back/propagate/delete one ACME DNS-01 TXT record in Arvan;
* issue from a transported CSR in an isolated Certbot directory;
* verify and install the returned certificate in a campaign generation;
* test a loopback-only Nginx candidate and emit non-secret evidence.

It contains no production A-record update, no sites-enabled write, and no
Nginx reload/restart primitive.  A later, separately approved cutover agent
must consume the final activation manifest.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.secure_file_io import (
    SecureFileError,
    append_hash_chained_jsonl,
    read_secure_bytes,
    read_secure_text,
    sha256_secure_file,
    verify_hash_chained_jsonl,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from core.docker_image_identity import (
    DockerImageIdentityError,
    verify_content_descriptor,
)


SCHEMA_PREFIX = "trading-bot.webapp-ir-public-tls"
ARVAN_API_BASE = "https://napi.arvancloud.ir/cdn/4.0"
ROOT_DOMAIN = "gold-trade.ir"
PRODUCTION_HOSTNAME = "coin.gold-trade.ir"
ARVAN_A_RECORD_NAME = "coin"
ACME_TXT_RECORD_NAME = "_acme-challenge.coin"
WA_IR_PUBLIC_IP = "95.38.164.29"
DEFAULT_CAMPAIGN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
DEFAULT_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
DEFAULT_NGINX = Path("/usr/sbin/nginx")
DEFAULT_OPENSSL = Path("/usr/bin/openssl")
DEFAULT_CERTBOT = Path("/usr/bin/certbot")
DEFAULT_CURL = Path("/usr/bin/curl")
DEFAULT_DIG = Path("/usr/bin/dig")
DEFAULT_GIT = Path("/usr/bin/git")
RELEASE_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MAX_CERTIFICATE_BYTES = 1024 * 1024
MIN_CERTIFICATE_VALIDITY_SECONDS = 21 * 24 * 60 * 60
MAX_PROCESS_STATE_BYTES = 64 * 1024

CUTOVER_MANIFEST_SCHEMA = "production-shadow-cutover-manifest-v1"
ACTIVATION_PRECONDITION_SCHEMA = (
    "production-shadow-phase-evidence-verification-v1"
)
ACTIVATION_PRECONDITION_PHASE = "pre_first_write_acceptance"
ACTIVATION_PRECONDITION_OPERATION = "verify-pre-first-write-acceptance"
EXPECTED_ACTIVATION_ROLES = ["bot_fi", "webapp_fi", "webapp_ir", "witness"]
CUTOVER_MANIFEST_FIELDS = {
    "schema",
    "campaign_id",
    "operation_id",
    "created_at",
    "release_sha",
    "release_tree_sha",
    "legacy_release_sha",
    "topology",
    "deployment",
    "artifacts",
    "policy",
}
ACTIVATION_PRECONDITION_FIELDS = {
    "schema",
    "status",
    "phase",
    "operation",
    "campaign_id",
    "operation_id",
    "release_sha",
    "legacy_release_sha",
    "manifest_sha256",
    "plan_sha256",
    "approval_sha256",
    "phase_evidence_schema_sha256",
    "manifest_artifact_bindings_sha256",
    "prior_phase_evidence_closure_sha256",
    "phase_input_closure_sha256",
    "prior_phase_count",
    "evidence_sha256",
    "verified_roles",
    "verified_claim_count",
    "captured_at",
    "verified_at",
    "production_contacted",
}
TOPOLOGY_FIELDS = {"role", "host", "ssh_user", "ssh_port", "transport"}
EXPECTED_CUTOVER_TOPOLOGY = {
    "bot_fi": {
        "role": "bot_fi",
        "host": "65.109.216.187",
        "ssh_user": None,
        "ssh_port": None,
        "transport": "local-controller",
    },
    "webapp_fi": {
        "role": "webapp_fi",
        "host": "65.109.220.59",
        "ssh_user": "root",
        "ssh_port": 37067,
        "transport": "ssh-control",
    },
    "webapp_ir": {
        "role": "webapp_ir",
        "host": WA_IR_PUBLIC_IP,
        "ssh_user": "root",
        "ssh_port": 22,
        "transport": "ssh-control-object-storage-payload-only",
    },
    "witness": {
        "role": "witness",
        "host": "37.152.191.11",
        "ssh_user": "root",
        "ssh_port": 22,
        "transport": "ssh-control-object-storage-payload-only",
    },
}
CUTOVER_DEPLOYMENT_FIELDS = {
    "production_hostname",
    "legacy_compose_project",
    "shadow_compose_project",
    "shadow_root",
    "controller_journal_path",
    "controller_evidence_root",
}
CUTOVER_ARTIFACT_FIELDS = {
    "release_bundle_sha256",
    "release_bundle_bytes",
    "role_materials",
    "image_artifacts",
    "role_runtime_image_ids",
    "postgres_runtime_uid",
    "postgres_runtime_gid",
    "postgres_image_ref",
    "legacy_bot_rollback_sha256",
    "legacy_webapp_rollback_sha256",
    "legacy_bot_redis_rollback_sha256",
    "legacy_webapp_redis_rollback_sha256",
    "shadow_compose_sha256",
    "cutover_approval_sha256",
    "human_approval_policy_sha256",
    "nginx_freeze_generation_sha256",
    "nginx_rollback_generation_sha256",
    "postcommit_executor_contract_sha256",
    "phase_evidence_schema_sha256",
    "host_agent_sha256",
    "host_agent_contract_sha256",
    "phase_evidence_verifier_sha256",
}
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
DOCKER_RUNTIME_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
IMAGE_ARTIFACT_FIELDS = {
    "archive_sha256",
    "archive_bytes",
    "config_digest",
    "content_descriptor",
    "content_identity",
}
ROLE_MATERIAL_FIELDS = {"sha256", "bytes", "transport", "format"}
CUTOVER_POLICY_FIELDS = {
    "plan_only_default",
    "write_block_required",
    "legacy_writers_stop_required",
    "zero_client_readback_required",
    "final_snapshot_hashes_required",
    "witness_lease_required",
    "readonly_switch_required",
    "rollback_before_first_write_only",
    "object_storage_private_versioned_age_required",
    "direct_payload_to_webapp_ir_forbidden",
    "staging_forbidden",
    "current_path_mutation_forbidden",
    "destructive_cleanup_forbidden",
    "database_downgrade_forbidden",
    "legacy_redis_restore_forbidden",
    "pristine_shadow_redis_required",
    "nginx_generation_coordinated_all_vhosts_required",
    "postcommit_forward_recovery_required",
    "iran_public_route_prepromotion_forbidden",
    "iran_effects_prepromotion_forbidden",
    "queue_rehydrate_before_claim_required",
}


class WebAppIrTlsError(RuntimeError):
    """Raised when a public TLS preparation invariant cannot be proven."""


RequestFn = Callable[
    [str, str, str, dict[str, Any] | None],
    dict[str, Any],
]
RunFn = Callable[..., subprocess.CompletedProcess[bytes]]
SleepFn = Callable[[float], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_text() -> str:
    return _now().isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _limited_digest(payload: bytes) -> dict[str, Any]:
    return {"sha256": _sha256_bytes(payload), "bytes": len(payload)}


def validate_campaign_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WebAppIrTlsError("campaign_id must be a canonical UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise WebAppIrTlsError("campaign_id must be a canonical lowercase UUID")
    return canonical


def validate_operation_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WebAppIrTlsError("operation_id must be a canonical UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise WebAppIrTlsError("operation_id must be a canonical lowercase UUID")
    return canonical


def validate_release_sha(value: str) -> str:
    normalized = str(value).strip().lower()
    if RELEASE_RE.fullmatch(normalized) is None:
        raise WebAppIrTlsError("release_sha must be exactly 40 lowercase hex characters")
    return normalized


def validate_sha256(value: str, *, label: str) -> str:
    normalized = str(value).strip().lower()
    if SHA256_RE.fullmatch(normalized) is None:
        raise WebAppIrTlsError(f"{label} must be exactly 64 lowercase hex characters")
    return normalized


def validate_production_scope(
    *,
    root_domain: str,
    hostname: str,
    a_record_name: str = ARVAN_A_RECORD_NAME,
    txt_record_name: str = ACME_TXT_RECORD_NAME,
) -> None:
    observed = (root_domain, hostname, a_record_name, txt_record_name)
    expected = (
        ROOT_DOMAIN,
        PRODUCTION_HOSTNAME,
        ARVAN_A_RECORD_NAME,
        ACME_TXT_RECORD_NAME,
    )
    if observed != expected:
        raise WebAppIrTlsError(
            "TLS preparation is pinned to the reviewed production hostname and record names"
        )


def validate_tcp_port(value: int, *, label: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise WebAppIrTlsError(f"{label} must be between 1024 and 65535")
    return port


def _directory_metadata_is_trusted(
    metadata: os.stat_result,
    *,
    private_leaf: bool,
) -> bool:
    if not stat.S_ISDIR(metadata.st_mode):
        return False
    mode = stat.S_IMODE(metadata.st_mode)
    if private_leaf:
        return (
            metadata.st_uid == os.geteuid()
            and metadata.st_gid == os.getegid()
            and mode == 0o700
        )
    if metadata.st_uid not in {0, os.geteuid()}:
        return False
    if mode & 0o022:
        return (
            metadata.st_uid == 0
            and bool(metadata.st_mode & stat.S_ISVTX)
        )
    return True


def _open_directory_chain(
    path: Path,
    *,
    create: bool,
    private_leaf: bool,
) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise WebAppIrTlsError(f"trusted directory path is not absolute: {path}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open("/", flags)
    try:
        parts = path.parts[1:]
        if not parts:
            metadata = os.fstat(descriptor)
            if not _directory_metadata_is_trusted(
                metadata,
                private_leaf=private_leaf,
            ):
                raise WebAppIrTlsError(f"trusted directory metadata is unsafe: {path}")
            return descriptor
        for index, component in enumerate(parts):
            last = index == len(parts) - 1
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise WebAppIrTlsError(
                        f"private directory does not exist: {path}"
                    )
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                except OSError as exc:
                    raise WebAppIrTlsError(
                        f"cannot create private directory: {path}"
                    ) from exc
            except OSError as exc:
                raise WebAppIrTlsError(
                    f"trusted directory chain is unsafe: {path}"
                ) from exc
            os.close(descriptor)
            descriptor = child
            metadata = os.fstat(descriptor)
            if not _directory_metadata_is_trusted(
                metadata,
                private_leaf=private_leaf and last,
            ):
                raise WebAppIrTlsError(
                    f"trusted directory metadata is unsafe: {path}"
                )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _assert_trusted_parent_chain(path: Path) -> None:
    descriptor = _open_directory_chain(
        path.parent,
        create=False,
        private_leaf=False,
    )
    os.close(descriptor)


def validate_executable(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise WebAppIrTlsError(f"{label} must be an absolute path")
    _assert_trusted_parent_chain(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WebAppIrTlsError(f"{label} is unavailable: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or metadata.st_nlink != 1
        or not os.access(path, os.X_OK)
    ):
        raise WebAppIrTlsError(f"{label} is not a trusted root-owned executable")
    return path


def validate_trusted_regular_file(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise WebAppIrTlsError(f"{label} must be an absolute path")
    _assert_trusted_parent_chain(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WebAppIrTlsError(f"{label} is unavailable: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or metadata.st_nlink != 1
    ):
        raise WebAppIrTlsError(f"{label} is not a trusted root-owned regular file")
    return path


def attest_trusted_ca_bundle(path: Path) -> dict[str, Any]:
    """Attest one stable root-owned CA bundle without accepting path aliases."""

    validate_trusted_regular_file(path, label="CA bundle")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WebAppIrTlsError(f"cannot securely open CA bundle: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > 16 * 1024 * 1024
        ):
            raise WebAppIrTlsError("CA bundle metadata is not trusted")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise WebAppIrTlsError("CA bundle changed while being attested")
    finally:
        os.close(descriptor)
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "bytes": size,
        "uid": before.st_uid,
        "gid": before.st_gid,
        "mode": f"{stat.S_IMODE(before.st_mode):04o}",
        "nlink": before.st_nlink,
    }


def _assert_private_directory(path: Path, *, create: bool = False) -> None:
    descriptor = _open_directory_chain(
        path,
        create=create,
        private_leaf=True,
    )
    os.close(descriptor)


@contextmanager
def _exclusive_operation_lock(directory: Path, *, name: str) -> Iterable[None]:
    _assert_private_directory(directory, create=True)
    if re.fullmatch(r"[a-z0-9-]+\.lock", name) is None:
        raise WebAppIrTlsError("operation lock name is invalid")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(directory / name, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise WebAppIrTlsError("operation lock file metadata is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WebAppIrTlsError("another process owns this operation lock") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise WebAppIrTlsError(f"refusing to unlink unsafe operation file: {path}")
    path.unlink()
    _fsync_directory(path.parent)


def _unlink_exact_private_files(paths: Iterable[Path]) -> None:
    for path in paths:
        _unlink_private_file(path)


def _cleanup_private_directory(
    path: Path,
    *,
    allowed_names: set[str],
) -> None:
    """Remove one exact private crash directory with a closed file allow-list."""

    if not path.exists():
        return
    _assert_private_directory(path)
    entries = list(path.iterdir())
    unexpected = sorted(entry.name for entry in entries if entry.name not in allowed_names)
    if unexpected:
        raise WebAppIrTlsError(
            "crash directory contains unexpected entries: " + ",".join(unexpected)
        )
    _unlink_exact_private_files(entries)
    try:
        path.rmdir()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise WebAppIrTlsError(f"cannot remove reconciled crash directory: {path}") from exc


def _read_json_secure(path: Path, *, label: str, max_size: int = 1024 * 1024) -> dict[str, Any]:
    try:
        decoded = json.loads(read_secure_text(path, label=label, max_size=max_size))
    except (SecureFileError, json.JSONDecodeError) as exc:
        raise WebAppIrTlsError(f"{label} is not a valid secure JSON object") from exc
    if not isinstance(decoded, dict):
        raise WebAppIrTlsError(f"{label} must be a JSON object")
    return decoded


def _write_new_json(path: Path, value: dict[str, Any], *, label: str) -> None:
    try:
        write_secure_new_bytes(
            path,
            _canonical_bytes(value) + b"\n",
            label=label,
            mode=0o600,
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc


def _write_atomic_json(path: Path, value: dict[str, Any], *, label: str) -> None:
    try:
        write_secure_atomic_bytes(
            path,
            _canonical_bytes(value) + b"\n",
            label=label,
            mode=0o600,
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc


def _append_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    try:
        return append_hash_chained_jsonl(
            path,
            {
                "timestamp": _now_text(),
                "host": socket.gethostname(),
                **event,
            },
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc


def _run(
    argv: Sequence[str | os.PathLike[str]],
    *,
    input_bytes: bytes | None = None,
    timeout: float = 60.0,
    env: dict[str, str] | None = None,
    run_fn: RunFn = subprocess.run,
) -> subprocess.CompletedProcess[bytes]:
    normalized = [os.fspath(item) for item in argv]
    try:
        completed = run_fn(
            normalized,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WebAppIrTlsError(f"command failed to execute: {normalized[0]}") from exc
    if completed.returncode != 0:
        stderr_digest = _sha256_bytes(bytes(completed.stderr or b""))
        raise WebAppIrTlsError(
            f"command failed ({normalized[0]}, exit={completed.returncode}, "
            f"stderr_sha256={stderr_digest})"
        )
    return completed


def _hash_trusted_code_file(path: Path, *, label: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WebAppIrTlsError(f"cannot open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or before.st_size > 16 * 1024 * 1024
        ):
            raise WebAppIrTlsError(f"{label} metadata is not trusted")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise WebAppIrTlsError(f"{label} changed while hashing")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def validate_exact_release_runtime(
    release_sha: str,
    *,
    git_bin: Path = DEFAULT_GIT,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    """Bind root-executed worker code to the immutable checked-out release."""

    release_sha = validate_release_sha(release_sha)
    validate_executable(git_bin, label="git")
    worker_relative = Path("scripts/prepare_webapp_ir_tls_dns.py")
    secure_io_relative = Path("core/secure_file_io.py")
    docker_identity_relative = Path("core/docker_image_identity.py")
    worker_path = REPOSITORY_ROOT / worker_relative
    secure_io_path = REPOSITORY_ROOT / secure_io_relative
    docker_identity_path = REPOSITORY_ROOT / docker_identity_relative
    for path, label in (
        (worker_path, "TLS worker source"),
        (secure_io_path, "secure file helper source"),
        (docker_identity_path, "Docker image identity helper source"),
    ):
        validate_trusted_regular_file(path, label=label)
    top = _run(
        [git_bin, "-C", REPOSITORY_ROOT, "rev-parse", "--show-toplevel"],
        run_fn=run_fn,
    ).stdout.decode("utf-8", errors="strict").strip()
    if Path(top).resolve(strict=True) != REPOSITORY_ROOT.resolve(strict=True):
        raise WebAppIrTlsError("TLS worker repository root differs from Git top-level")
    head = _run(
        [git_bin, "-C", REPOSITORY_ROOT, "rev-parse", "--verify", "HEAD"],
        run_fn=run_fn,
    ).stdout.decode("ascii", errors="strict").strip().lower()
    if head != release_sha:
        raise WebAppIrTlsError("TLS worker Git HEAD differs from approved release_sha")
    _run(
        [
            git_bin,
            "-C",
            REPOSITORY_ROOT,
            "ls-files",
            "--error-unmatch",
            worker_relative,
            secure_io_relative,
            docker_identity_relative,
        ],
        run_fn=run_fn,
    )
    _run(
        [
            git_bin,
            "-C",
            REPOSITORY_ROOT,
            "diff",
            "--quiet",
            "HEAD",
            "--",
            worker_relative,
            secure_io_relative,
            docker_identity_relative,
        ],
        run_fn=run_fn,
    )
    return {
        "schema": f"{SCHEMA_PREFIX}.runtime-source-binding.v1",
        "release_sha": release_sha,
        "git_head": head,
        "repository_root_sha256": _sha256_bytes(
            str(REPOSITORY_ROOT.resolve(strict=True)).encode("utf-8")
        ),
        "worker_path": str(worker_relative),
        "worker_sha256": _hash_trusted_code_file(
            worker_path,
            label="TLS worker source",
        ),
        "secure_file_helper_path": str(secure_io_relative),
        "secure_file_helper_sha256": _hash_trusted_code_file(
            secure_io_path,
            label="secure file helper source",
        ),
        "docker_image_identity_helper_path": str(docker_identity_relative),
        "docker_image_identity_helper_sha256": _hash_trusted_code_file(
            docker_identity_path,
            label="Docker image identity helper source",
        ),
        "tracked_files_clean": True,
    }


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise WebAppIrTlsError("Arvan API redirects are forbidden")


def _validate_arvan_url(url: str) -> None:
    reviewed = urllib.parse.urlsplit(ARVAN_API_BASE)
    candidate = urllib.parse.urlsplit(url)
    if (
        candidate.scheme != "https"
        or candidate.hostname != reviewed.hostname
        or candidate.port not in (None, 443)
        or candidate.username
        or candidate.password
        or not candidate.path.startswith(reviewed.path.rstrip("/") + "/")
    ):
        raise WebAppIrTlsError("Arvan request URL is outside the reviewed HTTPS API")


def arvan_request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    _validate_arvan_url(url)
    if method not in {"GET", "POST", "DELETE"}:
        raise WebAppIrTlsError("TLS DNS worker only permits GET, POST, and DELETE")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Apikey {token}",
        "User-Agent": "trading-bot-webapp-ir-tls-dns/1",
    }
    body = None
    if payload is not None:
        body = _canonical_bytes(payload)
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise WebAppIrTlsError(f"Arvan API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WebAppIrTlsError(f"Arvan API is unreachable: {exc.reason}") from exc
    if not raw:
        return {"status": True, "_http_status": status}
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebAppIrTlsError("Arvan API returned a non-JSON response") from exc
    if not isinstance(decoded, dict):
        raise WebAppIrTlsError("Arvan API returned an unexpected response shape")
    return decoded


def load_arvan_token(path: Path) -> str:
    try:
        token = read_secure_text(path, label="Arvan API token", max_size=16 * 1024).strip()
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc
    if not token or len(token) > 4096 or any(character.isspace() for character in token):
        raise WebAppIrTlsError("Arvan API token file is empty or malformed")
    return token


def _records_url() -> str:
    return (
        f"{ARVAN_API_BASE}/domains/"
        f"{urllib.parse.quote(ROOT_DOMAIN, safe='')}/dns-records"
    )


def _records_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    records = response.get("data")
    if not isinstance(records, list):
        raise WebAppIrTlsError("Arvan DNS record list has an unexpected response shape")
    return [record for record in records if isinstance(record, dict)]


def _txt_text(record: dict[str, Any]) -> str | None:
    value = record.get("value")
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


def _exact_txt_records(
    response: dict[str, Any],
    *,
    name: str,
    validation: str | None = None,
) -> list[dict[str, Any]]:
    return [
        record
        for record in _records_from_response(response)
        if str(record.get("type", "")).lower() == "txt"
        and record.get("name") == name
        and (validation is None or _txt_text(record) == validation)
    ]


def _normalize_a_record(record: dict[str, Any]) -> dict[str, Any]:
    if str(record.get("type", "")).lower() != "a" or record.get("name") != ARVAN_A_RECORD_NAME:
        raise WebAppIrTlsError("production A record has an unexpected identity")
    values = record.get("value")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise WebAppIrTlsError("production A record must contain exactly one origin")
    ip = values[0].get("ip")
    try:
        parsed_ip = ipaddress.ip_address(str(ip))
    except ValueError as exc:
        raise WebAppIrTlsError("production A record origin is not an IP address") from exc
    if parsed_ip.version != 4:
        raise WebAppIrTlsError("production A record origin must be IPv4")
    record_id = record.get("id")
    ttl = record.get("ttl")
    if not isinstance(record_id, str) or not record_id:
        raise WebAppIrTlsError("production A record has no immutable record id")
    if not isinstance(ttl, int) or not 60 <= ttl <= 86400:
        raise WebAppIrTlsError("production A record TTL is outside the reviewed range")
    return {
        "id": record_id,
        "type": "a",
        "name": ARVAN_A_RECORD_NAME,
        "value": [
            {
                "ip": str(parsed_ip),
                "port": values[0].get("port"),
                "weight": values[0].get("weight", 100),
                "country": values[0].get("country", ""),
            }
        ],
        "ttl": ttl,
        "cloud": record.get("cloud"),
        "upstream_https": record.get("upstream_https"),
        "ip_filter_mode": record.get("ip_filter_mode"),
    }


def fetch_production_a_rrset(
    *,
    token: str,
    request_fn: RequestFn = arvan_request,
) -> dict[str, Any]:
    response = request_fn("GET", _records_url(), token, None)
    matches = [
        record
        for record in _records_from_response(response)
        if str(record.get("type", "")).lower() == "a"
        and record.get("name") == ARVAN_A_RECORD_NAME
    ]
    if len(matches) != 1:
        raise WebAppIrTlsError(
            f"expected exactly one production A record, found {len(matches)}"
        )
    return _normalize_a_record(matches[0])


def desired_wa_ir_a_rrset(current: dict[str, Any]) -> dict[str, Any]:
    desired = json.loads(json.dumps(current))
    desired["value"][0]["ip"] = WA_IR_PUBLIC_IP
    return desired


def build_txt_payload(validation: str) -> dict[str, Any]:
    if (
        not isinstance(validation, str)
        or not 20 <= len(validation) <= 512
        or re.fullmatch(r"[A-Za-z0-9_-]+", validation) is None
    ):
        raise WebAppIrTlsError("ACME DNS validation has an unexpected format")
    return {
        "type": "txt",
        "name": ACME_TXT_RECORD_NAME,
        "value": {"text": validation},
        "ttl": 120,
        "cloud": False,
    }


def _challenge_state_path(state_dir: Path, validation: str) -> Path:
    return state_dir / f"challenge-{_sha256_bytes(validation.encode('ascii'))}.json"


def _parse_dig_txt(stdout: bytes) -> list[str]:
    values: list[str] = []
    for raw_line in stdout.decode("utf-8", errors="strict").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        chunks = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        if chunks:
            values.append("".join(chunks).replace(r"\"", '"').replace(r"\\", "\\"))
        else:
            values.append(line)
    return values


def wait_for_authoritative_txt(
    *,
    validation: str,
    dig_bin: Path = DEFAULT_DIG,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 5.0,
    stable_rounds: int = 2,
    run_fn: RunFn = subprocess.run,
    sleep_fn: SleepFn = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    validate_executable(dig_bin, label="dig")
    ns_result = _run(
        [dig_bin, "+time=3", "+tries=1", "+short", "NS", ROOT_DOMAIN],
        timeout=10,
        run_fn=run_fn,
    )
    nameservers = sorted(
        {
            line.strip().rstrip(".")
            for line in ns_result.stdout.decode("ascii", errors="strict").splitlines()
            if line.strip()
        }
    )
    if len(nameservers) < 2:
        raise WebAppIrTlsError("authoritative nameserver discovery returned fewer than two servers")
    deadline = monotonic_fn() + timeout_seconds
    consecutive = 0
    rounds = 0
    observations: dict[str, str] = {}
    while monotonic_fn() <= deadline:
        rounds += 1
        observations = {}
        all_visible = True
        for nameserver in nameservers:
            result = _run(
                [
                    dig_bin,
                    "+time=3",
                    "+tries=1",
                    "+short",
                    "TXT",
                    PRODUCTION_HOSTNAME.replace("coin.", "_acme-challenge.coin.", 1),
                    f"@{nameserver}",
                ],
                timeout=10,
                run_fn=run_fn,
            )
            values = _parse_dig_txt(result.stdout)
            observations[nameserver] = _sha256_json(values)
            if values != [validation]:
                all_visible = False
        if all_visible:
            consecutive += 1
            if consecutive >= stable_rounds:
                return {
                    "nameservers": nameservers,
                    "rounds": rounds,
                    "stable_rounds": consecutive,
                    "observation_sha256_by_nameserver": observations,
                }
        else:
            consecutive = 0
        sleep_fn(interval_seconds)
    raise WebAppIrTlsError("ACME TXT record did not become stable on all authoritative servers")


def _validate_dns01_state(
    state: dict[str, Any],
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    validation: str,
) -> None:
    validation_sha256 = _sha256_bytes(validation.encode("ascii"))
    expected = {
        "schema": f"{SCHEMA_PREFIX}.dns01-state.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "record_name": ACME_TXT_RECORD_NAME,
        "validation": validation,
        "validation_sha256": validation_sha256,
        "payload_sha256": _sha256_json(build_txt_payload(validation)),
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise WebAppIrTlsError("existing ACME challenge state has different bindings")
    if state.get("phase") not in {"create_intent", "created"}:
        raise WebAppIrTlsError("ACME challenge state phase is invalid")
    validate_sha256(
        str(state.get("before_a_rrset_sha256", "")),
        label="before_a_rrset_sha256",
    )
    if _sha256_json(state.get("before_a_rrset")) != state.get(
        "before_a_rrset_sha256"
    ):
        raise WebAppIrTlsError("ACME challenge A-record baseline hash mismatch")
    record_id = state.get("record_id")
    if state.get("phase") == "create_intent":
        if record_id is not None:
            raise WebAppIrTlsError("ACME create-intent unexpectedly contains a record id")
    elif not isinstance(record_id, str) or not record_id:
        raise WebAppIrTlsError("created ACME state has no immutable record id")


def _dns01_readback_record(
    *,
    response: dict[str, Any],
    validation: str,
    expected_record_id: str | None,
) -> dict[str, Any]:
    owner_records = _exact_txt_records(
        response,
        name=ACME_TXT_RECORD_NAME,
    )
    exact_records = [
        record for record in owner_records if _txt_text(record) == validation
    ]
    if len(owner_records) != 1 or len(exact_records) != 1:
        raise WebAppIrTlsError(
            "ACME TXT owner read-back is not exactly the operation validation"
        )
    record = exact_records[0]
    record_id = record.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise WebAppIrTlsError("Arvan TXT read-back has no immutable record id")
    if expected_record_id is not None and expected_record_id != record_id:
        raise WebAppIrTlsError("Arvan TXT response id differs from exact read-back")
    return record


def create_dns01_challenge(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    validation: str,
    token: str,
    state_dir: Path,
    journal_path: Path,
    request_fn: RequestFn = arvan_request,
    propagation_fn: Callable[..., dict[str, Any]] = wait_for_authoritative_txt,
) -> dict[str, Any]:
    validate_production_scope(root_domain=ROOT_DOMAIN, hostname=PRODUCTION_HOSTNAME)
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    payload = build_txt_payload(validation)
    _assert_private_directory(state_dir, create=True)
    _assert_private_directory(journal_path.parent, create=True)
    state_path = _challenge_state_path(state_dir, validation)
    validation_sha256 = _sha256_bytes(validation.encode("ascii"))
    before_a = fetch_production_a_rrset(token=token, request_fn=request_fn)

    if state_path.exists():
        state = _read_json_secure(state_path, label="ACME challenge state")
        _validate_dns01_state(
            state,
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            validation=validation,
        )
        if state.get("before_a_rrset") != before_a:
            raise WebAppIrTlsError("production A record differs from DNS-01 intent baseline")
        provider = request_fn("GET", _records_url(), token, None)
        if state["phase"] == "create_intent":
            owner_records = _exact_txt_records(
                provider,
                name=ACME_TXT_RECORD_NAME,
            )
            if not owner_records:
                response = request_fn("POST", _records_url(), token, payload)
                response_data = response.get("data")
                response_record_id = (
                    response_data.get("id")
                    if isinstance(response_data, dict)
                    else None
                )
                provider = request_fn("GET", _records_url(), token, None)
            else:
                response_record_id = None
            record = _dns01_readback_record(
                response=provider,
                validation=validation,
                expected_record_id=response_record_id,
            )
            record_id = str(record["id"])
            _append_event(
                journal_path,
                {
                    "event": "webapp_ir.tls.dns01.create.readback",
                    "campaign_id": campaign_id,
                    "operation_id": operation_id,
                    "release_sha": release_sha,
                    "record_id": record_id,
                    "validation_sha256": validation_sha256,
                    "provider_created_at": record.get("created_at"),
                    "provider_updated_at": record.get("updated_at"),
                    "record_receipt_sha256": _sha256_json(
                        {
                            "id": record_id,
                            "type": record.get("type"),
                            "name": record.get("name"),
                            "value_sha256": validation_sha256,
                            "ttl": record.get("ttl"),
                            "cloud": record.get("cloud"),
                            "created_at": record.get("created_at"),
                            "updated_at": record.get("updated_at"),
                        }
                    ),
                    "adopted_after_interruption": bool(owner_records),
                },
            )
            state = {
                **state,
                "phase": "created",
                "record_id": record_id,
                "provider_created_at": record.get("created_at"),
                "provider_updated_at": record.get("updated_at"),
                "provider_readback_at": _now_text(),
            }
            _write_atomic_json(
                state_path,
                state,
                label="ACME challenge state",
            )
        else:
            record = _dns01_readback_record(
                response=provider,
                validation=validation,
                expected_record_id=str(state.get("record_id")),
            )
            record_id = str(record["id"])
        propagation = propagation_fn(validation=validation)
        after_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
        if after_a != before_a:
            raise WebAppIrTlsError("production A record changed while rechecking DNS-01")
        propagated = _append_event(
            journal_path,
            {
                "event": "webapp_ir.tls.dns01.propagated",
                "campaign_id": campaign_id,
                "operation_id": operation_id,
                "release_sha": release_sha,
                "record_id": record_id,
                "validation_sha256": validation_sha256,
                "propagation_sha256": _sha256_json(propagation),
                "after_a_rrset_sha256": _sha256_json(after_a),
                "resumed": True,
            },
        )
        return {
            "status": "already_present",
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "propagation": propagation,
            "a_rrset_sha256": _sha256_json(before_a),
            "journal_event_hash": propagated["event_hash"],
        }

    existing = _exact_txt_records(
        request_fn("GET", _records_url(), token, None),
        name=ACME_TXT_RECORD_NAME,
    )
    if existing:
        raise WebAppIrTlsError(
            "ACME TXT owner name is not empty; refusing to disturb another challenge"
        )
    intent = _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.create.intent",
            "schema": f"{SCHEMA_PREFIX}.dns01-journal.v1",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_name": ACME_TXT_RECORD_NAME,
            "validation_sha256": validation_sha256,
            "before_a_rrset_sha256": _sha256_json(before_a),
            "payload_sha256": _sha256_json(payload),
        },
    )
    state = {
        "schema": f"{SCHEMA_PREFIX}.dns01-state.v1",
        "phase": "create_intent",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "record_id": None,
        "record_name": ACME_TXT_RECORD_NAME,
        "validation": validation,
        "validation_sha256": validation_sha256,
        "payload_sha256": _sha256_json(payload),
        "before_a_rrset": before_a,
        "before_a_rrset_sha256": _sha256_json(before_a),
        "create_intent_event_hash": intent["event_hash"],
        "created_at": _now_text(),
    }
    _write_new_json(state_path, state, label="ACME challenge state")
    response = request_fn("POST", _records_url(), token, payload)
    response_data = response.get("data")
    response_record_id = (
        response_data.get("id") if isinstance(response_data, dict) else None
    )
    record = _dns01_readback_record(
        response=request_fn("GET", _records_url(), token, None),
        validation=validation,
        expected_record_id=response_record_id,
    )
    record_id = str(record["id"])
    _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.create.readback",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "provider_created_at": record.get("created_at"),
            "provider_updated_at": record.get("updated_at"),
            "record_receipt_sha256": _sha256_json(
                {
                    "id": record_id,
                    "type": record.get("type"),
                    "name": record.get("name"),
                    "value_sha256": validation_sha256,
                    "ttl": record.get("ttl"),
                    "cloud": record.get("cloud"),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                }
            ),
        },
    )
    state = {
        **state,
        "phase": "created",
        "record_id": record_id,
        "provider_created_at": record.get("created_at"),
        "provider_updated_at": record.get("updated_at"),
        "provider_readback_at": _now_text(),
    }
    _write_atomic_json(state_path, state, label="ACME challenge state")
    try:
        propagation = propagation_fn(validation=validation)
    except Exception:
        try:
            delete_dns01_challenge(
                campaign_id=campaign_id,
                operation_id=operation_id,
                release_sha=release_sha,
                validation=validation,
                token=token,
                state_dir=state_dir,
                journal_path=journal_path,
                request_fn=request_fn,
            )
        except Exception as cleanup_error:
            raise WebAppIrTlsError(
                "DNS-01 propagation failed and exact-record cleanup also failed"
            ) from cleanup_error
        raise
    after_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
    if after_a != before_a:
        try:
            delete_dns01_challenge(
                campaign_id=campaign_id,
                operation_id=operation_id,
                release_sha=release_sha,
                validation=validation,
                token=token,
                state_dir=state_dir,
                journal_path=journal_path,
                request_fn=request_fn,
            )
        except Exception as cleanup_error:
            raise WebAppIrTlsError(
                "production A record changed and DNS-01 cleanup failed"
            ) from cleanup_error
        raise WebAppIrTlsError("production A record changed during DNS-01 creation")
    propagated = _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.propagated",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "propagation_sha256": _sha256_json(propagation),
            "after_a_rrset_sha256": _sha256_json(after_a),
        },
    )
    return {
        "status": "created_and_propagated",
        "record_id": record_id,
        "validation_sha256": validation_sha256,
        "propagation": propagation,
        "a_rrset_sha256": _sha256_json(before_a),
        "journal_event_hash": propagated["event_hash"],
    }


def delete_dns01_challenge(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    validation: str,
    token: str,
    state_dir: Path,
    journal_path: Path,
    request_fn: RequestFn = arvan_request,
) -> dict[str, Any]:
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    build_txt_payload(validation)
    _assert_private_directory(state_dir)
    _assert_private_directory(journal_path.parent)
    state_path = _challenge_state_path(state_dir, validation)
    validation_sha256 = _sha256_bytes(validation.encode("ascii"))
    if not state_path.exists():
        unowned = _exact_txt_records(
            request_fn("GET", _records_url(), token, None),
            name=ACME_TXT_RECORD_NAME,
            validation=validation,
        )
        if unowned:
            raise WebAppIrTlsError(
                "matching provider TXT exists without operation state; refusing deletion"
            )
        return {"status": "already_absent", "validation_sha256": validation_sha256}
    state = _read_json_secure(state_path, label="ACME challenge state")
    _validate_dns01_state(
        state,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        validation=validation,
    )
    record_id = state.get("record_id")
    before_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
    a_drifted_before_cleanup = (
        _sha256_json(before_a) != state.get("before_a_rrset_sha256")
    )
    current = _exact_txt_records(
        request_fn("GET", _records_url(), token, None),
        name=ACME_TXT_RECORD_NAME,
        validation=validation,
    )
    if not current:
        _append_event(
            journal_path,
            {
                "event": "webapp_ir.tls.dns01.cleanup.already_absent",
                "campaign_id": campaign_id,
                "operation_id": operation_id,
                "release_sha": release_sha,
                "record_id": record_id,
                "validation_sha256": validation_sha256,
            },
        )
        _unlink_private_file(state_path)
        return {
            "status": "already_absent",
            "record_id": record_id,
            "validation_sha256": validation_sha256,
        }
    if len(current) != 1:
        raise WebAppIrTlsError("ACME cleanup exact provider record is not unique")
    observed_record_id = current[0].get("id")
    if not isinstance(observed_record_id, str) or not observed_record_id:
        raise WebAppIrTlsError("ACME cleanup provider record has no immutable id")
    if record_id is not None and observed_record_id != record_id:
        raise WebAppIrTlsError("ACME cleanup exact provider record does not match state")
    record_id = observed_record_id
    intent = _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.delete.intent",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "before_a_rrset_sha256": _sha256_json(before_a),
        },
    )
    record_url = f"{_records_url()}/{urllib.parse.quote(record_id, safe='')}"
    request_fn("DELETE", record_url, token, None)
    records_after = request_fn("GET", _records_url(), token, None)
    remaining_id = [
        record
        for record in _records_from_response(records_after)
        if record.get("id") == record_id
    ]
    remaining_value = _exact_txt_records(
        records_after,
        name=ACME_TXT_RECORD_NAME,
        validation=validation,
    )
    if remaining_id or remaining_value:
        raise WebAppIrTlsError("Arvan TXT delete did not verify absent by id and value")
    after_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
    a_changed_during_cleanup = after_a != before_a
    completed = _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.delete.readback",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "delete_intent_event_hash": intent["event_hash"],
            "after_a_rrset_sha256": _sha256_json(after_a),
            "a_drifted_before_cleanup": a_drifted_before_cleanup,
            "a_changed_during_cleanup": a_changed_during_cleanup,
        },
    )
    _unlink_private_file(state_path)
    if a_drifted_before_cleanup or a_changed_during_cleanup:
        raise WebAppIrTlsError(
            "owned DNS-01 record was removed, but production A routing drift was detected"
        )
    return {
        "status": "deleted_and_verified",
        "record_id": record_id,
        "validation_sha256": validation_sha256,
        "a_rrset_sha256": _sha256_json(after_a),
        "journal_event_hash": completed["event_hash"],
    }


def reconcile_dns01_state(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    token: str,
    state_dir: Path,
    journal_path: Path,
    request_fn: RequestFn = arvan_request,
) -> dict[str, Any]:
    """Delete only an operation-owned TXT record after an interrupted hook."""

    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    _assert_private_directory(state_dir)
    _assert_private_directory(journal_path.parent)
    states = sorted(state_dir.glob("challenge-*.json"))
    if len(states) > 1:
        raise WebAppIrTlsError("more than one DNS-01 challenge state exists")
    if not states:
        exact = _exact_txt_records(
            request_fn("GET", _records_url(), token, None),
            name=ACME_TXT_RECORD_NAME,
        )
        if exact:
            raise WebAppIrTlsError(
                "ACME TXT exists without operation state; reconciliation refuses deletion"
            )
        return {"status": "no_operation_state"}
    state_path = states[0]
    if re.fullmatch(r"challenge-[0-9a-f]{64}\.json", state_path.name) is None:
        raise WebAppIrTlsError("DNS-01 state filename is malformed")
    state = _read_json_secure(state_path, label="ACME challenge state")
    validation = state.get("validation")
    if not isinstance(validation, str):
        raise WebAppIrTlsError("DNS-01 reconciliation state lacks its exact validation")
    build_txt_payload(validation)
    _validate_dns01_state(
        state,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        validation=validation,
    )
    record_id = state.get("record_id")
    validation_sha256 = state.get("validation_sha256")
    validate_sha256(str(validation_sha256), label="validation_sha256")
    before_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
    response = request_fn("GET", _records_url(), token, None)
    matching_value = [
        record
        for record in _exact_txt_records(
            response,
            name=ACME_TXT_RECORD_NAME,
            validation=validation,
        )
    ]
    matching_id = (
        [
            record
            for record in _records_from_response(response)
            if record.get("id") == record_id
        ]
        if record_id is not None
        else []
    )
    if not matching_value:
        if matching_id:
            raise WebAppIrTlsError(
                "provider record id remains but no longer matches operation validation"
            )
        _append_event(
            journal_path,
            {
                "event": "webapp_ir.tls.dns01.reconcile.already_absent",
                "campaign_id": campaign_id,
                "operation_id": operation_id,
                "release_sha": release_sha,
                "record_id": record_id,
                "validation_sha256": validation_sha256,
            },
        )
        _unlink_private_file(state_path)
        return {"status": "state_removed_record_already_absent", "record_id": record_id}
    if len(matching_value) != 1:
        raise WebAppIrTlsError("DNS-01 validation is not unique at the provider")
    record = matching_value[0]
    observed_record_id = record.get("id")
    if not isinstance(observed_record_id, str) or not observed_record_id:
        raise WebAppIrTlsError("DNS-01 provider record has no immutable id")
    if record_id is not None and observed_record_id != record_id:
        raise WebAppIrTlsError("DNS-01 validation record id differs from operation state")
    record_id = observed_record_id
    text = _txt_text(record)
    if (
        str(record.get("type", "")).lower() != "txt"
        or record.get("name") != ACME_TXT_RECORD_NAME
        or not isinstance(text, str)
        or _sha256_bytes(text.encode("ascii", errors="strict")) != validation_sha256
    ):
        raise WebAppIrTlsError("provider record no longer matches operation-owned TXT hash")
    intent = _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.reconcile.delete.intent",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "before_a_rrset_sha256": _sha256_json(before_a),
        },
    )
    request_fn(
        "DELETE",
        f"{_records_url()}/{urllib.parse.quote(record_id, safe='')}",
        token,
        None,
    )
    after_response = request_fn("GET", _records_url(), token, None)
    if any(
        record.get("id") == record_id
        for record in _records_from_response(after_response)
    ):
        raise WebAppIrTlsError("reconciled TXT record remains after provider delete")
    after_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
    completed = _append_event(
        journal_path,
        {
            "event": "webapp_ir.tls.dns01.reconcile.delete.readback",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "record_id": record_id,
            "validation_sha256": validation_sha256,
            "delete_intent_event_hash": intent["event_hash"],
            "after_a_rrset_sha256": _sha256_json(after_a),
            "a_rrset_unchanged": before_a == after_a,
        },
    )
    _unlink_private_file(state_path)
    if before_a != after_a or state.get("before_a_rrset_sha256") != _sha256_json(after_a):
        raise WebAppIrTlsError(
            "owned DNS-01 residue was removed, but production A routing drift was detected"
        )
    return {
        "status": "owned_record_deleted_and_verified",
        "record_id": record_id,
        "journal_event_hash": completed["event_hash"],
    }


def _operation_root(
    campaign_root: Path,
    campaign_id: str,
    operation_id: str,
) -> Path:
    return (
        campaign_root
        / campaign_id
        / "public-tls"
        / "operations"
        / operation_id
    )


def _spki_from_pem(
    public_key_pem: bytes,
    *,
    openssl_bin: Path,
    run_fn: RunFn,
) -> bytes:
    result = _run(
        [openssl_bin, "pkey", "-pubin", "-outform", "DER"],
        input_bytes=public_key_pem,
        run_fn=run_fn,
    )
    return bytes(result.stdout)


def key_spki_sha256(
    key_path: Path,
    *,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> str:
    validate_executable(openssl_bin, label="openssl")
    result = _run(
        [openssl_bin, "pkey", "-in", key_path, "-pubout", "-outform", "DER"],
        run_fn=run_fn,
    )
    return _sha256_bytes(bytes(result.stdout))


def csr_spki_sha256(
    csr_path: Path,
    *,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> str:
    validate_executable(openssl_bin, label="openssl")
    public = _run(
        [openssl_bin, "req", "-in", csr_path, "-pubkey", "-noout"],
        run_fn=run_fn,
    )
    return _sha256_bytes(
        _spki_from_pem(bytes(public.stdout), openssl_bin=openssl_bin, run_fn=run_fn)
    )


def certificate_spki_sha256(
    certificate_path: Path,
    *,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> str:
    validate_executable(openssl_bin, label="openssl")
    public = _run(
        [openssl_bin, "x509", "-in", certificate_path, "-pubkey", "-noout"],
        run_fn=run_fn,
    )
    return _sha256_bytes(
        _spki_from_pem(bytes(public.stdout), openssl_bin=openssl_bin, run_fn=run_fn)
    )


def _extract_sans(output: bytes) -> dict[str, list[str]]:
    text = output.decode("utf-8", errors="strict")
    dns_names = sorted(set(re.findall(r"DNS:([^,\s]+)", text)))
    ip_addresses = sorted(set(re.findall(r"IP Address:([^,\s]+)", text)))
    return {"dns": dns_names, "ip": ip_addresses}


def verify_csr(
    csr_path: Path,
    *,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    validate_executable(openssl_bin, label="openssl")
    try:
        csr_sha256, csr_bytes = sha256_secure_file(
            csr_path,
            label="WA-IR CSR",
            max_size=MAX_CERTIFICATE_BYTES,
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc
    _run(
        [openssl_bin, "req", "-in", csr_path, "-verify", "-noout"],
        run_fn=run_fn,
    )
    extensions = _run(
        [openssl_bin, "req", "-in", csr_path, "-noout", "-text"],
        run_fn=run_fn,
    )
    sans = _extract_sans(bytes(extensions.stdout))
    if sans != {"dns": [PRODUCTION_HOSTNAME], "ip": []}:
        raise WebAppIrTlsError("CSR SAN set is not exactly the production hostname")
    return {
        "csr_sha256": csr_sha256,
        "csr_bytes": csr_bytes,
        "public_key_spki_sha256": csr_spki_sha256(
            csr_path,
            openssl_bin=openssl_bin,
            run_fn=run_fn,
        ),
        "exact_sans": sans,
    }


def _csr_receipt(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    key_path: Path,
    csr_path: Path,
    verified: dict[str, Any],
    key_spki: str,
    recovered: bool,
) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA_PREFIX}.csr-receipt.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "generated_at": _now_text(),
        "csr_path": str(csr_path),
        "private_key_path": str(key_path),
        "csr_sha256": verified["csr_sha256"],
        "public_key_spki_sha256": key_spki,
        "exact_sans": verified["exact_sans"],
        "private_key_exported": False,
        "recovered_from_partial_generation": recovered,
    }


def _cleanup_csr_temporaries(operation_root: Path) -> None:
    for entry in operation_root.iterdir():
        if re.fullmatch(r"\.(?:key|csr)-[0-9a-f]{32}\.tmp", entry.name):
            _unlink_private_file(entry)


def _generate_csr_for_existing_key(
    *,
    key_path: Path,
    csr_path: Path,
    operation_root: Path,
    openssl_bin: Path,
    run_fn: RunFn,
) -> None:
    temporary_csr = operation_root / f".csr-{secrets.token_hex(16)}.tmp"
    old_umask = os.umask(0o077)
    try:
        _run(
            [
                openssl_bin,
                "req",
                "-new",
                "-key",
                key_path,
                "-sha256",
                "-subj",
                f"/CN={PRODUCTION_HOSTNAME}",
                "-addext",
                f"subjectAltName=DNS:{PRODUCTION_HOSTNAME}",
                "-addext",
                "basicConstraints=critical,CA:FALSE",
                "-addext",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "-addext",
                "extendedKeyUsage=serverAuth",
                "-out",
                temporary_csr,
            ],
            run_fn=run_fn,
        )
        temporary_csr.chmod(0o600)
        payload = read_secure_bytes(
            temporary_csr,
            label="recovered WA-IR CSR",
            max_size=MAX_CERTIFICATE_BYTES,
        )
        write_secure_new_bytes(
            csr_path,
            payload,
            label="WA-IR CSR",
            mode=0o600,
            max_size=MAX_CERTIFICATE_BYTES,
        )
    except (SecureFileError, OSError) as exc:
        raise WebAppIrTlsError("failed to recover WA-IR CSR from existing key") from exc
    finally:
        os.umask(old_umask)
        _unlink_private_file(temporary_csr)


def generate_wa_ir_key_and_csr(
    *,
    campaign_root: Path,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    validate_executable(openssl_bin, label="openssl")
    operation_root = _operation_root(campaign_root, campaign_id, operation_id)
    _assert_private_directory(operation_root, create=True)
    key_path = operation_root / "wa-ir-private-key.pem"
    csr_path = operation_root / "wa-ir-request.csr"
    receipt_path = operation_root / "csr-receipt.json"
    _cleanup_csr_temporaries(operation_root)
    existing = [path.exists() for path in (key_path, csr_path, receipt_path)]
    if any(existing):
        if csr_path.exists() and not key_path.exists():
            raise WebAppIrTlsError("WA-IR CSR exists without its private key")
        if receipt_path.exists() and not all((key_path.exists(), csr_path.exists())):
            raise WebAppIrTlsError("WA-IR CSR receipt exists without complete key material")
        recovered = False
        if key_path.exists() and not csr_path.exists():
            _generate_csr_for_existing_key(
                key_path=key_path,
                csr_path=csr_path,
                operation_root=operation_root,
                openssl_bin=openssl_bin,
                run_fn=run_fn,
            )
            recovered = True
        verified = verify_csr(csr_path, openssl_bin=openssl_bin, run_fn=run_fn)
        key_spki = key_spki_sha256(key_path, openssl_bin=openssl_bin, run_fn=run_fn)
        if key_spki != verified["public_key_spki_sha256"]:
            raise WebAppIrTlsError("existing WA-IR private key does not match its CSR")
        if not receipt_path.exists():
            receipt = _csr_receipt(
                campaign_id=campaign_id,
                operation_id=operation_id,
                release_sha=release_sha,
                key_path=key_path,
                csr_path=csr_path,
                verified=verified,
                key_spki=key_spki,
                recovered=True,
            )
            _write_new_json(receipt_path, receipt, label="WA-IR CSR receipt")
            return {"status": "recovered_partial_generation", **receipt}
        receipt = _read_json_secure(receipt_path, label="WA-IR CSR receipt")
        expected = {
            "schema": f"{SCHEMA_PREFIX}.csr-receipt.v1",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "role": "webapp_ir",
            "expected_host": WA_IR_PUBLIC_IP,
            "production_hostname": PRODUCTION_HOSTNAME,
            "csr_path": str(csr_path),
            "private_key_path": str(key_path),
            "csr_sha256": verified["csr_sha256"],
            "public_key_spki_sha256": key_spki,
            "exact_sans": verified["exact_sans"],
            "private_key_exported": False,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError("existing WA-IR CSR receipt binding mismatch")
        return {"status": "verified_existing", **receipt}

    token = secrets.token_hex(16)
    temporary_key = operation_root / f".key-{token}.tmp"
    temporary_csr = operation_root / f".csr-{token}.tmp"
    old_umask = os.umask(0o077)
    try:
        _run(
            [
                openssl_bin,
                "req",
                "-new",
                "-newkey",
                "rsa:3072",
                "-sha256",
                "-nodes",
                "-subj",
                f"/CN={PRODUCTION_HOSTNAME}",
                "-addext",
                f"subjectAltName=DNS:{PRODUCTION_HOSTNAME}",
                "-addext",
                "basicConstraints=critical,CA:FALSE",
                "-addext",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "-addext",
                "extendedKeyUsage=serverAuth",
                "-keyout",
                temporary_key,
                "-out",
                temporary_csr,
            ],
            run_fn=run_fn,
        )
        temporary_key.chmod(0o600)
        temporary_csr.chmod(0o600)
        key_payload = read_secure_bytes(
            temporary_key,
            label="temporary WA-IR private key",
            max_size=MAX_CERTIFICATE_BYTES,
        )
        csr_payload = read_secure_bytes(
            temporary_csr,
            label="temporary WA-IR CSR",
            max_size=MAX_CERTIFICATE_BYTES,
        )
        write_secure_new_bytes(
            key_path,
            key_payload,
            label="WA-IR private key",
            mode=0o600,
            max_size=MAX_CERTIFICATE_BYTES,
        )
        write_secure_new_bytes(
            csr_path,
            csr_payload,
            label="WA-IR CSR",
            mode=0o600,
            max_size=MAX_CERTIFICATE_BYTES,
        )
    except (SecureFileError, OSError) as exc:
        raise WebAppIrTlsError("failed to publish WA-IR key/CSR atomically") from exc
    finally:
        os.umask(old_umask)
        _unlink_private_file(temporary_key)
        _unlink_private_file(temporary_csr)
    verified = verify_csr(csr_path, openssl_bin=openssl_bin, run_fn=run_fn)
    key_spki = key_spki_sha256(key_path, openssl_bin=openssl_bin, run_fn=run_fn)
    if key_spki != verified["public_key_spki_sha256"]:
        raise WebAppIrTlsError("new WA-IR private key does not match its CSR")
    receipt = _csr_receipt(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        key_path=key_path,
        csr_path=csr_path,
        verified=verified,
        key_spki=key_spki,
        recovered=False,
    )
    _write_new_json(receipt_path, receipt, label="WA-IR CSR receipt")
    return {"status": "generated", **receipt}


def _pem_certificates(payload: bytes) -> list[bytes]:
    certificates = re.findall(
        rb"-----BEGIN CERTIFICATE-----\r?\n"
        rb".+?"
        rb"-----END CERTIFICATE-----\r?\n?",
        payload,
        flags=re.DOTALL,
    )
    if not certificates or b"".join(certificates).replace(b"\r\n", b"\n") != payload.replace(
        b"\r\n", b"\n"
    ):
        raise WebAppIrTlsError("certificate artifact is not a strict PEM certificate chain")
    return [certificate.replace(b"\r\n", b"\n") for certificate in certificates]


def _parse_openssl_dates(output: bytes) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in output.decode("ascii", errors="strict").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    try:
        not_before = datetime.strptime(
            values["notBefore"], "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
        not_after = datetime.strptime(
            values["notAfter"], "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError) as exc:
        raise WebAppIrTlsError("OpenSSL returned unparseable certificate dates") from exc
    if not_before >= not_after:
        raise WebAppIrTlsError("certificate validity interval is invalid")
    return not_before.isoformat(), not_after.isoformat()


def verify_certificate_material(
    *,
    private_key_path: Path | None,
    csr_path: Path,
    fullchain_path: Path,
    chain_path: Path,
    ca_bundle: Path = DEFAULT_CA_BUNDLE,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
    minimum_validity_seconds: int = MIN_CERTIFICATE_VALIDITY_SECONDS,
) -> dict[str, Any]:
    validate_executable(openssl_bin, label="openssl")
    ca_attestation = attest_trusted_ca_bundle(ca_bundle)
    for path, label in (
        (csr_path, "WA-IR CSR"),
        (fullchain_path, "WA-IR fullchain"),
        (chain_path, "WA-IR issuer chain"),
    ):
        _assert_trusted_parent_chain(path)
        if path.parent != fullchain_path.parent and label != "WA-IR CSR":
            raise WebAppIrTlsError("certificate artifacts must share one private directory")
    if private_key_path is not None:
        _assert_trusted_parent_chain(private_key_path)
    _assert_private_directory(fullchain_path.parent)
    try:
        fullchain_payload = read_secure_bytes(
            fullchain_path,
            label="WA-IR fullchain",
            max_size=MAX_CERTIFICATE_BYTES,
        )
        chain_payload = read_secure_bytes(
            chain_path,
            label="WA-IR issuer chain",
            max_size=MAX_CERTIFICATE_BYTES,
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc
    fullchain_certificates = _pem_certificates(fullchain_payload)
    chain_certificates = _pem_certificates(chain_payload)
    if len(fullchain_certificates) < 2:
        raise WebAppIrTlsError("fullchain must contain a leaf and at least one issuer certificate")
    if fullchain_certificates[1:] != chain_certificates:
        raise WebAppIrTlsError("fullchain issuer certificates do not exactly match chain.pem")
    temporary_leaf = fullchain_path.parent / f".leaf-{secrets.token_hex(16)}.pem"
    try:
        write_secure_new_bytes(
            temporary_leaf,
            fullchain_certificates[0],
            label="temporary WA-IR leaf certificate",
            mode=0o600,
            max_size=MAX_CERTIFICATE_BYTES,
        )
        csr = verify_csr(csr_path, openssl_bin=openssl_bin, run_fn=run_fn)
        leaf_spki = certificate_spki_sha256(
            temporary_leaf,
            openssl_bin=openssl_bin,
            run_fn=run_fn,
        )
        if leaf_spki != csr["public_key_spki_sha256"]:
            raise WebAppIrTlsError("issued certificate does not match transported CSR")
        if private_key_path is not None:
            key_spki = key_spki_sha256(
                private_key_path,
                openssl_bin=openssl_bin,
                run_fn=run_fn,
            )
            if key_spki != leaf_spki:
                raise WebAppIrTlsError("issued certificate does not match WA-IR private key")
        extensions = _run(
            [openssl_bin, "x509", "-in", temporary_leaf, "-noout", "-text"],
            run_fn=run_fn,
        )
        sans = _extract_sans(bytes(extensions.stdout))
        if sans != {"dns": [PRODUCTION_HOSTNAME], "ip": []}:
            raise WebAppIrTlsError("certificate SAN set is not exactly the production hostname")
        purpose = _run(
            [openssl_bin, "x509", "-in", temporary_leaf, "-purpose", "-noout"],
            run_fn=run_fn,
        )
        if b"SSL server : Yes" not in purpose.stdout:
            raise WebAppIrTlsError("certificate is not valid for TLS server authentication")
        _run(
            [
                openssl_bin,
                "x509",
                "-in",
                temporary_leaf,
                "-checkend",
                str(int(minimum_validity_seconds)),
                "-noout",
            ],
            run_fn=run_fn,
        )
        _run(
            [
                openssl_bin,
                "verify",
                "-purpose",
                "sslserver",
                "-verify_hostname",
                PRODUCTION_HOSTNAME,
                "-CAfile",
                ca_bundle,
                "-untrusted",
                chain_path,
                temporary_leaf,
            ],
            run_fn=run_fn,
        )
        dates = _run(
            [
                openssl_bin,
                "x509",
                "-in",
                temporary_leaf,
                "-noout",
                "-startdate",
                "-enddate",
            ],
            run_fn=run_fn,
        )
        not_before, not_after = _parse_openssl_dates(bytes(dates.stdout))
        leaf_der = _run(
            [openssl_bin, "x509", "-in", temporary_leaf, "-outform", "DER"],
            run_fn=run_fn,
        )
    finally:
        _unlink_private_file(temporary_leaf)
    return {
        "key_csr_match": (
            private_key_path is None
            or key_spki_sha256(
                private_key_path,
                openssl_bin=openssl_bin,
                run_fn=run_fn,
            )
            == csr["public_key_spki_sha256"]
        ),
        "key_cert_match": (
            private_key_path is None
            or key_spki_sha256(
                private_key_path,
                openssl_bin=openssl_bin,
                run_fn=run_fn,
            )
            == leaf_spki
        ),
        "csr_cert_match": csr["public_key_spki_sha256"] == leaf_spki,
        "exact_sans": sans,
        "required_eku": ["serverAuth"],
        "eku_server_auth": True,
        "chain_verified": True,
        "hostname_verified": True,
        "validity_verified": True,
        "not_before": not_before,
        "not_after": not_after,
        "csr_sha256": csr["csr_sha256"],
        "leaf_cert_sha256": _sha256_bytes(bytes(leaf_der.stdout)),
        "fullchain_sha256": _sha256_bytes(fullchain_payload),
        "chain_sha256": _sha256_bytes(chain_payload),
        "public_key_spki_sha256": leaf_spki,
        "ca_bundle": ca_attestation,
    }


def _hook_command(
    *,
    script_path: Path,
    subcommand: str,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    token_file: Path,
    state_dir: Path,
    journal_path: Path,
) -> str:
    argv = [
        sys.executable,
        str(script_path),
        subcommand,
        "--campaign-id",
        campaign_id,
        "--operation-id",
        operation_id,
        "--release-sha",
        release_sha,
        "--arvan-token-file",
        str(token_file),
        "--state-dir",
        str(state_dir),
        "--journal",
        str(journal_path),
    ]
    return " ".join(shlex.quote(value) for value in argv)


def _protect_certificate_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.chmod(0o600)
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_gid != os.getegid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_nlink != 1
                ):
                    raise WebAppIrTlsError(
                        f"Certbot output metadata is unsafe: {path}"
                    )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise WebAppIrTlsError(f"cannot protect Certbot output: {path}") from exc


def _verify_dns01_journal(
    journal_path: Path,
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        journal = verify_hash_chained_jsonl(
            journal_path,
            label="DNS-01 journal",
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc
    event_names = [event.get("event") for event in journal]
    for event in journal:
        if any(
            event.get(key) != value
            for key, value in {
                "campaign_id": campaign_id,
                "operation_id": operation_id,
                "release_sha": release_sha,
            }.items()
        ):
            raise WebAppIrTlsError("DNS-01 journal event identity mismatch")
    deletion_events = {
        "webapp_ir.tls.dns01.delete.readback",
        "webapp_ir.tls.dns01.cleanup.already_absent",
        "webapp_ir.tls.dns01.reconcile.delete.readback",
        "webapp_ir.tls.dns01.reconcile.already_absent",
    }
    if (
        "webapp_ir.tls.dns01.create.readback" not in event_names
        or "webapp_ir.tls.dns01.propagated" not in event_names
        or not deletion_events.intersection(event_names)
    ):
        raise WebAppIrTlsError("DNS-01 journal does not prove create/propagate/delete")
    record_receipts = [
        {
            "event": event.get("event"),
            "record_id": event.get("record_id"),
            "provider_created_at": event.get("provider_created_at"),
            "provider_updated_at": event.get("provider_updated_at"),
            "timestamp": event.get("timestamp"),
            "event_hash": event.get("event_hash"),
        }
        for event in journal
        if event.get("event")
        in {
            "webapp_ir.tls.dns01.create.readback",
            "webapp_ir.tls.dns01.propagated",
            *deletion_events,
        }
    ]
    return journal, record_receipts


def _validate_issuance_state(
    state: dict[str, Any],
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    csr_sha256: str,
    command_sha256: str,
    ca_attestation: dict[str, Any],
) -> dict[str, Any]:
    expected = {
        "schema": f"{SCHEMA_PREFIX}.issuance-state.v1",
        "phase": "certbot_intent",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "csr_sha256": csr_sha256,
        "certbot_argv_sha256": command_sha256,
        "ca_bundle": ca_attestation,
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise WebAppIrTlsError("certificate issuance state binding mismatch")
    baseline = state.get("before_a_rrset")
    if (
        not isinstance(baseline, dict)
        or _sha256_json(baseline) != state.get("before_a_rrset_sha256")
    ):
        raise WebAppIrTlsError("certificate issuance A-record baseline is invalid")
    return baseline


def _build_issuance_receipt(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    csr: dict[str, Any],
    verification: dict[str, Any],
    journal_path: Path,
    journal: list[dict[str, Any]],
    record_receipts: list[dict[str, Any]],
    before_a: dict[str, Any],
    after_a: dict[str, Any],
    command: Sequence[str | os.PathLike[str]],
    completed: subprocess.CompletedProcess[bytes] | None,
    cert_path: Path,
    chain_path: Path,
    fullchain_path: Path,
) -> dict[str, Any]:
    command_output: dict[str, Any]
    if completed is None:
        command_output = {
            "certbot_stdout": {
                "available": False,
                "reason": "recovered_after_certbot_process_exit",
            },
            "certbot_stderr": {
                "available": False,
                "reason": "recovered_after_certbot_process_exit",
            },
        }
    else:
        command_output = {
            "certbot_stdout": _limited_digest(bytes(completed.stdout)),
            "certbot_stderr": _limited_digest(bytes(completed.stderr)),
        }
    return {
        "schema": f"{SCHEMA_PREFIX}.issuance-receipt.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "issued_at": _now_text(),
        "receipt_recovered": completed is None,
        "csr_sha256": csr["csr_sha256"],
        "leaf_cert_sha256": verification["leaf_cert_sha256"],
        "chain_sha256": verification["chain_sha256"],
        "fullchain_sha256": verification["fullchain_sha256"],
        "public_key_spki_sha256": verification["public_key_spki_sha256"],
        "not_before": verification["not_before"],
        "not_after": verification["not_after"],
        "exact_sans": verification["exact_sans"],
        "required_eku": verification["required_eku"],
        "ca_bundle": verification["ca_bundle"],
        "dns01_journal_sha256": sha256_secure_file(
            journal_path,
            label="DNS-01 journal",
        )[0],
        "dns01_event_hashes": [event["event_hash"] for event in journal],
        "dns01_record_receipts": record_receipts,
        "before_a_rrset": before_a,
        "before_a_rrset_sha256": _sha256_json(before_a),
        "after_a_rrset_sha256": _sha256_json(after_a),
        "certbot_argv_sha256": _sha256_json(
            [os.fspath(item) for item in command]
        ),
        **command_output,
        "certificate_paths": {
            "leaf": str(cert_path),
            "chain": str(chain_path),
            "fullchain": str(fullchain_path),
        },
    }


def issue_certificate_from_csr(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    csr_path: Path,
    output_dir: Path,
    email: str,
    token_file: Path,
    script_path: Path,
    certbot_bin: Path = DEFAULT_CERTBOT,
    openssl_bin: Path = DEFAULT_OPENSSL,
    ca_bundle: Path = DEFAULT_CA_BUNDLE,
    request_fn: RequestFn = arvan_request,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    if EMAIL_RE.fullmatch(email) is None or len(email) > 254:
        raise WebAppIrTlsError("Certbot email is malformed")
    validate_executable(certbot_bin, label="certbot")
    validate_executable(openssl_bin, label="openssl")
    ca_attestation = attest_trusted_ca_bundle(ca_bundle)
    validate_trusted_regular_file(script_path, label="DNS-01 hook worker")
    if script_path.resolve(strict=True) != Path(__file__).resolve(strict=True):
        raise WebAppIrTlsError("Certbot hook worker must be this reviewed release file")
    token = load_arvan_token(token_file)
    csr = verify_csr(csr_path, openssl_bin=openssl_bin, run_fn=run_fn)
    _assert_private_directory(output_dir, create=True)
    cert_path = output_dir / "leaf.pem"
    chain_path = output_dir / "chain.pem"
    fullchain_path = output_dir / "fullchain.pem"
    receipt_path = output_dir / "issuance-receipt.json"
    issuance_state_path = output_dir / "issuance-state.json"
    state_dir = output_dir / "dns01-state"
    journal_path = output_dir / "dns01-journal.jsonl"
    for directory in (
        state_dir,
        journal_path.parent,
        output_dir / "certbot-config",
        output_dir / "certbot-work",
        output_dir / "certbot-logs",
    ):
        _assert_private_directory(directory, create=True)

    auth_hook = _hook_command(
        script_path=script_path,
        subcommand="dns-auth",
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        token_file=token_file,
        state_dir=state_dir,
        journal_path=journal_path,
    )
    cleanup_hook = _hook_command(
        script_path=script_path,
        subcommand="dns-cleanup",
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        token_file=token_file,
        state_dir=state_dir,
        journal_path=journal_path,
    )
    command = [
        certbot_bin,
        "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email",
        email,
        "--manual",
        "--preferred-challenges",
        "dns",
        "--manual-auth-hook",
        auth_hook,
        "--manual-cleanup-hook",
        cleanup_hook,
        "--csr",
        csr_path,
        "--cert-path",
        cert_path,
        "--chain-path",
        chain_path,
        "--fullchain-path",
        fullchain_path,
        "--config-dir",
        output_dir / "certbot-config",
        "--work-dir",
        output_dir / "certbot-work",
        "--logs-dir",
        output_dir / "certbot-logs",
    ]
    command_sha256 = _sha256_json([os.fspath(item) for item in command])
    certificate_paths = (cert_path, chain_path, fullchain_path)

    if receipt_path.exists():
        if not all(path.exists() for path in certificate_paths):
            raise WebAppIrTlsError(
                "certificate issuance receipt exists without complete certificate output"
            )
        reconcile_dns01_state(
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            token=token,
            state_dir=state_dir,
            journal_path=journal_path,
            request_fn=request_fn,
        )
        _protect_certificate_outputs(certificate_paths)
        verification = verify_certificate_material(
            private_key_path=None,
            csr_path=csr_path,
            fullchain_path=fullchain_path,
            chain_path=chain_path,
            ca_bundle=ca_bundle,
            openssl_bin=openssl_bin,
            run_fn=run_fn,
        )
        receipt = _read_json_secure(receipt_path, label="certificate issuance receipt")
        current_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
        journal, record_receipts = _verify_dns01_journal(
            journal_path,
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
        )
        expected = {
            "schema": f"{SCHEMA_PREFIX}.issuance-receipt.v1",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "role": "webapp_ir",
            "expected_host": WA_IR_PUBLIC_IP,
            "production_hostname": PRODUCTION_HOSTNAME,
            "csr_sha256": csr["csr_sha256"],
            "leaf_cert_sha256": verification["leaf_cert_sha256"],
            "chain_sha256": verification["chain_sha256"],
            "fullchain_sha256": verification["fullchain_sha256"],
            "public_key_spki_sha256": verification["public_key_spki_sha256"],
            "exact_sans": verification["exact_sans"],
            "required_eku": verification["required_eku"],
            "ca_bundle": ca_attestation,
            "dns01_journal_sha256": sha256_secure_file(
                journal_path,
                label="DNS-01 journal",
            )[0],
            "dns01_event_hashes": [event["event_hash"] for event in journal],
            "dns01_record_receipts": record_receipts,
            "before_a_rrset": current_a,
            "before_a_rrset_sha256": _sha256_json(current_a),
            "after_a_rrset_sha256": _sha256_json(current_a),
            "certbot_argv_sha256": command_sha256,
            "certificate_paths": {
                "leaf": str(cert_path),
                "chain": str(chain_path),
                "fullchain": str(fullchain_path),
            },
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError("existing issuance receipt binding mismatch")
        if issuance_state_path.exists():
            state = _read_json_secure(
                issuance_state_path,
                label="certificate issuance state",
            )
            baseline = _validate_issuance_state(
                state,
                campaign_id=campaign_id,
                operation_id=operation_id,
                release_sha=release_sha,
                csr_sha256=csr["csr_sha256"],
                command_sha256=command_sha256,
                ca_attestation=ca_attestation,
            )
            if baseline != current_a:
                raise WebAppIrTlsError(
                    "production A record differs from completed issuance intent"
                )
            _unlink_private_file(issuance_state_path)
        return {"status": "verified_existing", **receipt}

    if issuance_state_path.exists():
        state = _read_json_secure(
            issuance_state_path,
            label="certificate issuance state",
        )
        before_a = _validate_issuance_state(
            state,
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            csr_sha256=csr["csr_sha256"],
            command_sha256=command_sha256,
            ca_attestation=ca_attestation,
        )
        if fetch_production_a_rrset(token=token, request_fn=request_fn) != before_a:
            raise WebAppIrTlsError(
                "production A record differs from certificate issuance intent"
            )
    else:
        reconcile_dns01_state(
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            token=token,
            state_dir=state_dir,
            journal_path=journal_path,
            request_fn=request_fn,
        )
        before_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
        state = {
            "schema": f"{SCHEMA_PREFIX}.issuance-state.v1",
            "phase": "certbot_intent",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "csr_sha256": csr["csr_sha256"],
            "certbot_argv_sha256": command_sha256,
            "ca_bundle": ca_attestation,
            "before_a_rrset": before_a,
            "before_a_rrset_sha256": _sha256_json(before_a),
            "created_at": _now_text(),
        }
        _write_new_json(
            issuance_state_path,
            state,
            label="certificate issuance state",
        )

    reconcile_dns01_state(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        token=token,
        state_dir=state_dir,
        journal_path=journal_path,
        request_fn=request_fn,
    )
    if fetch_production_a_rrset(token=token, request_fn=request_fn) != before_a:
        raise WebAppIrTlsError(
            "production A record changed while reconciling certificate issuance"
        )

    present = [path.exists() for path in certificate_paths]
    completed: subprocess.CompletedProcess[bytes] | None
    if any(present) and not all(present):
        _unlink_exact_private_files(
            path for path, exists in zip(certificate_paths, present) if exists
        )
        present = [False, False, False]
    if all(present):
        completed = None
    else:
        try:
            completed = _run(command, timeout=900, run_fn=run_fn)
        except Exception as issuance_error:
            try:
                reconcile_dns01_state(
                    campaign_id=campaign_id,
                    operation_id=operation_id,
                    release_sha=release_sha,
                    token=token,
                    state_dir=state_dir,
                    journal_path=journal_path,
                    request_fn=request_fn,
                )
            except Exception as cleanup_error:
                raise WebAppIrTlsError(
                    "certificate issuance failed and DNS-01 reconciliation also failed"
                ) from cleanup_error
            raise issuance_error
    _protect_certificate_outputs(certificate_paths)
    _fsync_directory(output_dir)
    try:
        cert_payload = read_secure_bytes(
            cert_path,
            label="Certbot leaf certificate",
            max_size=MAX_CERTIFICATE_BYTES,
        )
        fullchain_payload = read_secure_bytes(
            fullchain_path,
            label="Certbot fullchain",
            max_size=MAX_CERTIFICATE_BYTES,
        )
    except SecureFileError as exc:
        raise WebAppIrTlsError(str(exc)) from exc
    if _pem_certificates(cert_payload) != _pem_certificates(fullchain_payload)[:1]:
        raise WebAppIrTlsError("Certbot leaf output differs from fullchain leaf")
    verification = verify_certificate_material(
        private_key_path=None,
        csr_path=csr_path,
        fullchain_path=fullchain_path,
        chain_path=chain_path,
        ca_bundle=ca_bundle,
        openssl_bin=openssl_bin,
        run_fn=run_fn,
    )
    reconcile_dns01_state(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        token=token,
        state_dir=state_dir,
        journal_path=journal_path,
        request_fn=request_fn,
    )
    state_residue = [
        path for path in state_dir.iterdir() if path.name != "dns01-provider.lock"
    ]
    if state_residue:
        raise WebAppIrTlsError("Certbot returned with DNS-01 operation state residue")
    after_a = fetch_production_a_rrset(token=token, request_fn=request_fn)
    if after_a != before_a:
        raise WebAppIrTlsError("production A record changed while issuing the certificate")
    journal, record_receipts = _verify_dns01_journal(
        journal_path,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
    )
    receipt = _build_issuance_receipt(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        csr=csr,
        verification=verification,
        journal_path=journal_path,
        journal=journal,
        record_receipts=record_receipts,
        before_a=before_a,
        after_a=after_a,
        command=command,
        completed=completed,
        cert_path=cert_path,
        chain_path=chain_path,
        fullchain_path=fullchain_path,
    )
    _write_new_json(receipt_path, receipt, label="certificate issuance receipt")
    _unlink_private_file(issuance_state_path)
    return {
        "status": (
            "recovered_complete_issuance"
            if completed is None
            else "issued_and_dns_cleaned"
        ),
        **receipt,
    }


def _file_attestation(path: Path, *, label: str, expected_mode: int = 0o600) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WebAppIrTlsError(f"cannot inspect {label}: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or metadata.st_nlink != 1
    ):
        raise WebAppIrTlsError(f"{label} file metadata is unsafe")
    try:
        digest, size = sha256_secure_file(path, label=label)
        realpath = path.resolve(strict=True)
    except (SecureFileError, OSError) as exc:
        raise WebAppIrTlsError(f"cannot attest {label}") from exc
    return {
        "path": str(path),
        "sha256": digest,
        "bytes": size,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "nlink": metadata.st_nlink,
        "realpath_sha256": _sha256_bytes(str(realpath).encode("utf-8")),
    }


def _relocated_file_attestation(
    attestation: dict[str, Any],
    *,
    final_path: Path,
) -> dict[str, Any]:
    return {
        **attestation,
        "path": str(final_path),
        "realpath_sha256": _sha256_bytes(str(final_path).encode("utf-8")),
    }


def _installation_receipt_document(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    generation_id: str,
    generation_root: Path,
    verification: dict[str, Any],
    file_attestations: dict[str, dict[str, Any]],
    recovered: bool,
) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA_PREFIX}.installation-receipt.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "generation_id": generation_id,
        "generation_path": str(generation_root),
        "installed_at": _now_text(),
        "receipt_recovered": recovered,
        **verification,
        "files": file_attestations,
        "private_key_exported": False,
    }


def install_wa_ir_certificate_generation(
    *,
    campaign_root: Path,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    transported_fullchain_path: Path,
    transported_chain_path: Path,
    ca_bundle: Path = DEFAULT_CA_BUNDLE,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    operation_root = _operation_root(campaign_root, campaign_id, operation_id)
    _assert_private_directory(operation_root)
    source_key = operation_root / "wa-ir-private-key.pem"
    source_csr = operation_root / "wa-ir-request.csr"
    csr_receipt = _read_json_secure(
        operation_root / "csr-receipt.json",
        label="WA-IR CSR receipt",
    )
    verification = verify_certificate_material(
        private_key_path=source_key,
        csr_path=source_csr,
        fullchain_path=transported_fullchain_path,
        chain_path=transported_chain_path,
        ca_bundle=ca_bundle,
        openssl_bin=openssl_bin,
        run_fn=run_fn,
    )
    expected_csr = {
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "csr_sha256": verification["csr_sha256"],
        "public_key_spki_sha256": verification["public_key_spki_sha256"],
    }
    if any(csr_receipt.get(key) != value for key, value in expected_csr.items()):
        raise WebAppIrTlsError("certificate does not bind to the WA-IR CSR receipt")
    generation_id = f"{operation_id}-{verification['leaf_cert_sha256'][:16]}"
    generations_root = campaign_root / campaign_id / "public-tls" / "generations"
    _assert_private_directory(generations_root, create=True)
    generation_root = generations_root / generation_id
    final_destinations = {
        "private_key": generation_root / "private-key.pem",
        "csr": generation_root / "request.csr",
        "leaf": generation_root / "leaf.pem",
        "chain": generation_root / "chain.pem",
        "fullchain": generation_root / "fullchain.pem",
    }
    receipt_path = generation_root / "installation-receipt.json"
    fullchain_payload = read_secure_bytes(
        transported_fullchain_path,
        label="transported fullchain",
        max_size=MAX_CERTIFICATE_BYTES,
    )
    payloads = {
        "private_key": read_secure_bytes(
            source_key,
            label="WA-IR private key",
            max_size=MAX_CERTIFICATE_BYTES,
        ),
        "csr": read_secure_bytes(
            source_csr,
            label="WA-IR CSR",
            max_size=MAX_CERTIFICATE_BYTES,
        ),
        "leaf": _pem_certificates(fullchain_payload)[0],
        "chain": read_secure_bytes(
            transported_chain_path,
            label="transported issuer chain",
            max_size=MAX_CERTIFICATE_BYTES,
        ),
        "fullchain": fullchain_payload,
    }
    if generation_root.exists():
        _assert_private_directory(generation_root)
        if not all(path.exists() for path in final_destinations.values()):
            raise WebAppIrTlsError("partial WA-IR certificate generation exists")
        installed_verification = verify_certificate_material(
            private_key_path=final_destinations["private_key"],
            csr_path=final_destinations["csr"],
            fullchain_path=final_destinations["fullchain"],
            chain_path=final_destinations["chain"],
            ca_bundle=ca_bundle,
            openssl_bin=openssl_bin,
            run_fn=run_fn,
        )
        if installed_verification != verification:
            raise WebAppIrTlsError(
                "existing TLS generation verification differs from transported material"
            )
        current_attestations = {
            name: _file_attestation(path, label=f"installed {name}")
            for name, path in final_destinations.items()
        }
        for name, path in final_destinations.items():
            if current_attestations[name]["sha256"] != _sha256_bytes(payloads[name]):
                raise WebAppIrTlsError("existing TLS generation content mismatch")
        if not receipt_path.exists():
            receipt = _installation_receipt_document(
                campaign_id=campaign_id,
                operation_id=operation_id,
                release_sha=release_sha,
                generation_id=generation_id,
                generation_root=generation_root,
                verification=installed_verification,
                file_attestations=current_attestations,
                recovered=True,
            )
            _write_new_json(receipt_path, receipt, label="TLS installation receipt")
            return {"status": "recovered_generation_receipt", **receipt}
        receipt = _read_json_secure(receipt_path, label="TLS installation receipt")
        expected = {
            "schema": f"{SCHEMA_PREFIX}.installation-receipt.v1",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "role": "webapp_ir",
            "expected_host": WA_IR_PUBLIC_IP,
            "production_hostname": PRODUCTION_HOSTNAME,
            "generation_id": generation_id,
            "generation_path": str(generation_root),
            "files": current_attestations,
            "private_key_exported": False,
            **installed_verification,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError("existing TLS installation receipt binding mismatch")
        return {"status": "verified_existing", **receipt}
    initializing_root = generations_root / f"initializing-{generation_id}"
    if initializing_root.exists():
        _cleanup_private_directory(
            initializing_root,
            allowed_names={
                path.name for path in final_destinations.values()
            }
            | {"installation-receipt.json"},
        )
    _assert_private_directory(initializing_root, create=True)
    initializing_destinations = {
        name: initializing_root / path.name
        for name, path in final_destinations.items()
    }
    try:
        for name, path in initializing_destinations.items():
            try:
                write_secure_new_bytes(
                    path,
                    payloads[name],
                    label=f"installed {name}",
                    mode=0o600,
                    max_size=MAX_CERTIFICATE_BYTES,
                )
            except SecureFileError as exc:
                raise WebAppIrTlsError(str(exc)) from exc
        installed_verification = verify_certificate_material(
            private_key_path=initializing_destinations["private_key"],
            csr_path=initializing_destinations["csr"],
            fullchain_path=initializing_destinations["fullchain"],
            chain_path=initializing_destinations["chain"],
            ca_bundle=ca_bundle,
            openssl_bin=openssl_bin,
            run_fn=run_fn,
        )
        if installed_verification != verification:
            raise WebAppIrTlsError(
                "installed TLS generation verification changed after copy"
            )
        initializing_attestations = {
            name: _file_attestation(path, label=f"installed {name}")
            for name, path in initializing_destinations.items()
        }
        final_attestations = {
            name: _relocated_file_attestation(
                initializing_attestations[name],
                final_path=final_destinations[name],
            )
            for name in final_destinations
        }
        receipt = _installation_receipt_document(
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            generation_id=generation_id,
            generation_root=generation_root,
            verification=installed_verification,
            file_attestations=final_attestations,
            recovered=False,
        )
        _write_new_json(
            initializing_root / "installation-receipt.json",
            receipt,
            label="TLS installation receipt",
        )
        _fsync_directory(initializing_root)
        if generation_root.exists():
            raise WebAppIrTlsError("TLS generation appeared concurrently")
        os.rename(initializing_root, generation_root)
        _fsync_directory(generations_root)
    except Exception:
        if initializing_root.exists():
            _cleanup_private_directory(
                initializing_root,
                allowed_names={
                    path.name for path in initializing_destinations.values()
                }
                | {"installation-receipt.json"},
            )
        raise
    final_attestations = {
        name: _file_attestation(path, label=f"installed {name}")
        for name, path in final_destinations.items()
    }
    if receipt.get("files") != final_attestations:
        raise WebAppIrTlsError("TLS installation attestations changed across atomic rename")
    return {"status": "installed_and_verified", **receipt}


def capture_dns_baseline(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    token: str,
    output_path: Path,
    dig_bin: Path = DEFAULT_DIG,
    request_fn: RequestFn = arvan_request,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    validate_executable(dig_bin, label="dig")
    current = fetch_production_a_rrset(token=token, request_fn=request_fn)
    desired = desired_wa_ir_a_rrset(current)
    public = _run(
        [dig_bin, "+time=3", "+tries=1", "+short", "A", PRODUCTION_HOSTNAME],
        timeout=10,
        run_fn=run_fn,
    )
    public_ips: list[str] = []
    for value in public.stdout.decode("ascii", errors="strict").splitlines():
        value = value.strip()
        if not value:
            continue
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError as exc:
            raise WebAppIrTlsError("public DNS A lookup returned a non-IP value") from exc
        if parsed.version != 4:
            raise WebAppIrTlsError("public DNS A lookup returned non-IPv4 data")
        public_ips.append(str(parsed))
    public_ips = sorted(set(public_ips))
    current_ip = current["value"][0]["ip"]
    if public_ips != [current_ip]:
        raise WebAppIrTlsError("public DNS A lookup does not match Arvan provider read-back")
    receipt = {
        "schema": f"{SCHEMA_PREFIX}.dns-baseline.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "production_hostname": PRODUCTION_HOSTNAME,
        "captured_at": _now_text(),
        "expected_pre_activation_dns_a_rrset": current,
        "expected_pre_activation_dns_a_rrset_sha256": _sha256_json(current),
        "desired_dns_a_rrset": desired,
        "desired_dns_a_rrset_sha256": _sha256_json(desired),
        "rollback_dns_a_rrset": current,
        "rollback_dns_a_rrset_sha256": _sha256_json(current),
        "public_resolver_a_values": public_ips,
        "public_resolver_a_values_sha256": _sha256_json(public_ips),
    }
    _write_new_json(output_path, receipt, label="production DNS baseline")
    return receipt


def capture_active_nginx_baseline(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    output_path: Path,
    nginx_bin: Path = DEFAULT_NGINX,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    campaign_id = validate_campaign_id(campaign_id)
    operation_id = validate_operation_id(operation_id)
    release_sha = validate_release_sha(release_sha)
    validate_executable(nginx_bin, label="nginx")
    command = [nginx_bin, "-T"]
    result = _run(command, timeout=30, run_fn=run_fn)
    combined = bytes(result.stdout) + b"\x00stderr\x00" + bytes(result.stderr)
    receipt = {
        "schema": f"{SCHEMA_PREFIX}.nginx-baseline.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "expected_host": WA_IR_PUBLIC_IP,
        "captured_at": _now_text(),
        "nginx_t_argv_sha256": _sha256_json([os.fspath(item) for item in command]),
        "nginx_t_stdout": _limited_digest(bytes(result.stdout)),
        "nginx_t_stderr": _limited_digest(bytes(result.stderr)),
        "active_nginx_generation_sha256": _sha256_bytes(combined),
    }
    _write_new_json(output_path, receipt, label="active Nginx baseline")
    return receipt


def _nginx_quote(value: Path | str) -> str:
    text = os.fspath(value)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise WebAppIrTlsError("Nginx path contains a forbidden control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_loopback_candidate_nginx(
    *,
    generation_root: Path,
    candidate_port: int,
    shadow_upstream_port: int,
) -> str:
    candidate_port = validate_tcp_port(candidate_port, label="candidate listener port")
    shadow_upstream_port = validate_tcp_port(
        shadow_upstream_port,
        label="shadow upstream port",
    )
    if candidate_port == shadow_upstream_port:
        raise WebAppIrTlsError("candidate listener and shadow upstream ports must differ")
    key_path = generation_root / "private-key.pem"
    fullchain_path = generation_root / "fullchain.pem"
    pid_path = generation_root / "candidate-nginx.pid"
    error_log = generation_root / "candidate-error.log"
    access_log = generation_root / "candidate-access.log"
    return f"""master_process off;
worker_processes 1;
pid {_nginx_quote(pid_path)};
error_log {_nginx_quote(error_log)} notice;

events {{
    worker_connections 128;
}}

http {{
    access_log {_nginx_quote(access_log)};
    server_tokens off;

    server {{
        listen 127.0.0.1:{candidate_port} ssl;
        server_name {PRODUCTION_HOSTNAME};

        ssl_certificate {_nginx_quote(fullchain_path)};
        ssl_certificate_key {_nginx_quote(key_path)};
        ssl_protocols TLSv1.2 TLSv1.3;

        location = /health/live {{
            proxy_pass http://127.0.0.1:{shadow_upstream_port};
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }}

        location / {{
            return 404;
        }}
    }}
}}
"""


def stage_loopback_candidate_nginx(
    *,
    installation_receipt_path: Path,
    candidate_port: int,
    shadow_upstream_port: int,
    nginx_baseline_before_path: Path,
    nginx_baseline_after_path: Path,
    nginx_bin: Path = DEFAULT_NGINX,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    installation = _read_json_secure(
        installation_receipt_path,
        label="TLS installation receipt",
    )
    generation_root = Path(str(installation.get("generation_path", "")))
    _assert_private_directory(generation_root)
    campaign_id = validate_campaign_id(str(installation.get("campaign_id", "")))
    operation_id = validate_operation_id(str(installation.get("operation_id", "")))
    release_sha = validate_release_sha(str(installation.get("release_sha", "")))
    before = _read_json_secure(
        nginx_baseline_before_path,
        label="active Nginx baseline before candidate",
    )
    for document, label in ((before, "Nginx baseline before"),):
        expected = {
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
        }
        if any(document.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError(f"{label} identity mismatch")
    validate_executable(nginx_bin, label="nginx")
    candidate_port = validate_tcp_port(candidate_port, label="candidate listener port")
    shadow_upstream_port = validate_tcp_port(
        shadow_upstream_port,
        label="shadow upstream port",
    )
    config_path = generation_root / "candidate-nginx.conf"
    receipt_path = generation_root / "candidate-nginx-receipt.json"
    config_payload = render_loopback_candidate_nginx(
        generation_root=generation_root,
        candidate_port=candidate_port,
        shadow_upstream_port=shadow_upstream_port,
    ).encode("ascii")
    if receipt_path.exists():
        if not config_path.exists():
            raise WebAppIrTlsError(
                "Nginx candidate receipt exists without its configuration"
            )
        receipt = _read_json_secure(receipt_path, label="Nginx candidate receipt")
        config_sha256 = _file_attestation(
            config_path,
            label="Nginx candidate configuration",
        )["sha256"]
        expected = {
            "schema": f"{SCHEMA_PREFIX}.candidate-nginx-receipt.v1",
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "role": "webapp_ir",
            "expected_host": WA_IR_PUBLIC_IP,
            "production_hostname": PRODUCTION_HOSTNAME,
            "generation_id": installation.get("generation_id"),
            "candidate_nginx_generation_path": str(config_path),
            "candidate_nginx_generation_sha256": _sha256_bytes(config_payload),
            "candidate_listener": f"127.0.0.1:{candidate_port}",
            "shadow_upstream": f"127.0.0.1:{shadow_upstream_port}",
            "readiness_url": (
                f"https://{PRODUCTION_HOSTNAME}:{candidate_port}/health/live"
            ),
            "readiness_path": "/health/live",
            "nginx_t_exit": 0,
            "active_nginx_before_sha256": before[
                "active_nginx_generation_sha256"
            ],
            "active_nginx_after_sha256": before[
                "active_nginx_generation_sha256"
            ],
            "active_nginx_unchanged": True,
        }
        if config_sha256 != _sha256_bytes(config_payload):
            raise WebAppIrTlsError("existing Nginx candidate configuration differs")
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError("existing Nginx candidate receipt binding mismatch")
        after = _read_json_secure(
            nginx_baseline_after_path,
            label="active Nginx baseline after candidate",
        )
        _require_bound_documents(
            (("Nginx baseline after", after),),
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
        )
        if (
            after.get("active_nginx_generation_sha256")
            != before.get("active_nginx_generation_sha256")
        ):
            raise WebAppIrTlsError(
                "active Nginx baseline changed after candidate receipt"
            )
        return {"status": "verified_existing", **receipt}
    if config_path.exists():
        if _file_attestation(
            config_path,
            label="Nginx candidate configuration",
        )["sha256"] != _sha256_bytes(config_payload):
            raise WebAppIrTlsError("partial Nginx candidate configuration differs")
    else:
        try:
            write_secure_new_bytes(
                config_path,
                config_payload,
                label="Nginx candidate configuration",
                mode=0o600,
            )
        except SecureFileError as exc:
            raise WebAppIrTlsError(str(exc)) from exc
    command = [nginx_bin, "-t", "-c", config_path, "-p", generation_root]
    result = _run(command, timeout=30, run_fn=run_fn)
    if not nginx_baseline_after_path.exists():
        capture_active_nginx_baseline(
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            output_path=nginx_baseline_after_path,
            nginx_bin=nginx_bin,
            run_fn=run_fn,
        )
    after = _read_json_secure(
        nginx_baseline_after_path,
        label="active Nginx baseline after candidate",
    )
    _require_bound_documents(
        (("Nginx baseline after", after),),
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
    )
    if (
        before.get("active_nginx_generation_sha256")
        != after.get("active_nginx_generation_sha256")
    ):
        raise WebAppIrTlsError("active Nginx configuration changed while staging candidate")
    receipt = {
        "schema": f"{SCHEMA_PREFIX}.candidate-nginx-receipt.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "generation_id": installation.get("generation_id"),
        "generated_at": _now_text(),
        "candidate_nginx_generation_path": str(config_path),
        "candidate_nginx_generation_sha256": _sha256_bytes(config_payload),
        "candidate_listener": f"127.0.0.1:{candidate_port}",
        "shadow_upstream": f"127.0.0.1:{shadow_upstream_port}",
        "readiness_url": (
            f"https://{PRODUCTION_HOSTNAME}:{candidate_port}/health/live"
        ),
        "readiness_path": "/health/live",
        "nginx_t_argv_sha256": _sha256_json([os.fspath(item) for item in command]),
        "nginx_t_exit": result.returncode,
        "nginx_t_stdout": _limited_digest(bytes(result.stdout)),
        "nginx_t_stderr": _limited_digest(bytes(result.stderr)),
        "active_nginx_before_sha256": before["active_nginx_generation_sha256"],
        "active_nginx_after_sha256": after["active_nginx_generation_sha256"],
        "active_nginx_unchanged": True,
    }
    _write_new_json(receipt_path, receipt, label="Nginx candidate receipt")
    return {"status": "staged_and_syntax_verified", **receipt}


def _port_available(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _listener_accepting(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()


def _wait_for_listener(
    port: int,
    *,
    expected_open: bool,
    timeout_seconds: float,
    sleep_fn: SleepFn = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> None:
    deadline = monotonic_fn() + timeout_seconds
    while monotonic_fn() <= deadline:
        if _listener_accepting(port) is expected_open:
            return
        sleep_fn(0.1)
    state = "open" if expected_open else "closed"
    raise WebAppIrTlsError(f"candidate listener did not become {state}")


def _read_proc_identity(pid: int) -> tuple[int, str, list[str]] | None:
    if pid <= 1:
        raise WebAppIrTlsError("candidate process pid is unsafe")
    try:
        stat_payload = (Path("/proc") / str(pid) / "stat").read_text(
            encoding="ascii"
        )
        cmdline_payload = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WebAppIrTlsError("cannot inspect candidate process identity") from exc
    _, separator, trailing = stat_payload.rpartition(")")
    fields = trailing.strip().split()
    if not separator or len(fields) < 20:
        raise WebAppIrTlsError("candidate /proc stat has an invalid shape")
    try:
        start_ticks = int(fields[19])
    except ValueError as exc:
        raise WebAppIrTlsError("candidate /proc start time is invalid") from exc
    state = fields[0]
    argv = [
        value.decode("utf-8", errors="strict")
        for value in cmdline_payload.split(b"\0")
        if value
    ]
    return start_ticks, state, argv


def _candidate_executable_identity(command: list[str]) -> dict[str, Any]:
    path = Path(command[0])
    validate_executable(path, label="candidate executable")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise WebAppIrTlsError("cannot inspect candidate executable") from exc
    return {
        "path": str(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def _candidate_process_matches(
    *,
    pid: int,
    argv: list[str],
    command: list[str],
) -> bool:
    if argv == command:
        return True
    if Path(command[0]).name != "nginx" or len(argv) != 1:
        return False
    if re.fullmatch(r"nginx: (?:worker|master) process(?: .*)?", argv[0]) is None:
        return False
    try:
        expected = Path(command[0]).stat()
        observed = (Path("/proc") / str(pid) / "exe").stat()
    except OSError:
        return False
    return (expected.st_dev, expected.st_ino) == (observed.st_dev, observed.st_ino)


def _find_exact_candidate_processes(command: list[str]) -> list[int]:
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid <= 1:
            continue
        try:
            identity = _read_proc_identity(pid)
        except (UnicodeDecodeError, WebAppIrTlsError):
            continue
        if identity is not None and identity[2] == command:
            matches.append(pid)
    return sorted(matches)


def _read_candidate_pid_file(path: Path) -> int | None:
    try:
        payload = read_secure_bytes(
            path,
            label="candidate Nginx pid file",
            max_size=64,
        )
    except SecureFileError:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise WebAppIrTlsError("cannot securely read candidate Nginx pid") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_gid != os.getegid()
                or metadata.st_mode & 0o022
                or metadata.st_nlink != 1
                or metadata.st_size > 64
            ):
                raise WebAppIrTlsError("candidate Nginx pid metadata is unsafe")
            payload = os.read(descriptor, 65)
        finally:
            os.close(descriptor)
    normalized = payload.strip()
    if not normalized:
        return None
    try:
        pid = int(normalized.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebAppIrTlsError("candidate Nginx pid content is invalid") from exc
    if pid <= 1:
        raise WebAppIrTlsError("candidate Nginx pid is unsafe")
    return pid


def _terminate_candidate_process_group(
    *,
    pid: int,
    expected_start_ticks: int,
    command: list[str],
    sleep_fn: SleepFn,
) -> None:
    identity = _read_proc_identity(pid)
    if identity is None:
        return
    start_ticks, process_state, argv = identity
    if start_ticks != expected_start_ticks:
        raise WebAppIrTlsError(
            "candidate process pid was reused; refusing to signal it"
        )
    if process_state == "Z":
        return
    if not _candidate_process_matches(pid=pid, argv=argv, command=command):
        raise WebAppIrTlsError(
            "candidate process command differs; refusing to signal it"
        )
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    if pgid != pid:
        raise WebAppIrTlsError("candidate process does not own an isolated group")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() <= deadline:
        identity = _read_proc_identity(pid)
        if identity is None or identity[1] == "Z":
            return
        if identity[0] != expected_start_ticks:
            raise WebAppIrTlsError(
                "candidate process pid changed during termination"
            )
        sleep_fn(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() <= deadline:
        identity = _read_proc_identity(pid)
        if identity is None or identity[1] == "Z":
            return
        if identity[0] != expected_start_ticks:
            raise WebAppIrTlsError(
                "candidate process pid changed during forced termination"
            )
        sleep_fn(0.1)
    raise WebAppIrTlsError("candidate process group did not terminate")


def _validate_candidate_process_state(
    state: dict[str, Any],
    *,
    candidate: dict[str, Any],
    command: list[str],
    config_sha256: str,
    port: int,
) -> None:
    expected = {
        "schema": f"{SCHEMA_PREFIX}.candidate-process-state.v1",
        "campaign_id": candidate.get("campaign_id"),
        "operation_id": candidate.get("operation_id"),
        "release_sha": candidate.get("release_sha"),
        "generation_id": candidate.get("generation_id"),
        "command": command,
        "command_sha256": _sha256_json(command),
        "executable_identity": _candidate_executable_identity(command),
        "config_sha256": config_sha256,
        "candidate_port": port,
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise WebAppIrTlsError("candidate process state binding mismatch")
    if state.get("phase") not in {"spawn_intent", "running"}:
        raise WebAppIrTlsError("candidate process state phase is invalid")


def _reconcile_candidate_process(
    *,
    generation_root: Path,
    candidate: dict[str, Any],
    command: list[str],
    config_sha256: str,
    port: int,
    sleep_fn: SleepFn,
) -> None:
    state_path = generation_root / "candidate-process-state.json"
    pid_path = generation_root / "candidate-nginx.pid"
    if state_path.exists():
        state = _read_json_secure(
            state_path,
            label="candidate process state",
            max_size=MAX_PROCESS_STATE_BYTES,
        )
        _validate_candidate_process_state(
            state,
            candidate=candidate,
            command=command,
            config_sha256=config_sha256,
            port=port,
        )
        if state["phase"] == "running":
            pid = state.get("pid")
            start_ticks = state.get("proc_start_ticks")
            if (
                not isinstance(pid, int)
                or isinstance(pid, bool)
                or not isinstance(start_ticks, int)
                or isinstance(start_ticks, bool)
            ):
                raise WebAppIrTlsError("candidate running state has invalid pid data")
            _terminate_candidate_process_group(
                pid=pid,
                expected_start_ticks=start_ticks,
                command=command,
                sleep_fn=sleep_fn,
            )
        else:
            candidate_pid = (
                _read_candidate_pid_file(pid_path) if pid_path.exists() else None
            )
            if candidate_pid is not None:
                identity = _read_proc_identity(candidate_pid)
                if identity is not None and not _candidate_process_matches(
                    pid=candidate_pid,
                    argv=identity[2],
                    command=command,
                ):
                    raise WebAppIrTlsError(
                        "candidate pid file differs from interrupted spawn intent"
                    )
                matches = [candidate_pid] if identity is not None else []
            else:
                matches = _find_exact_candidate_processes(command)
            if len(matches) > 1:
                raise WebAppIrTlsError(
                    "multiple candidate processes match interrupted spawn intent"
                )
            if matches:
                identity = _read_proc_identity(matches[0])
                if identity is None:
                    pass
                else:
                    _terminate_candidate_process_group(
                        pid=matches[0],
                        expected_start_ticks=identity[0],
                        command=command,
                        sleep_fn=sleep_fn,
                    )
        _unlink_private_file(pid_path)
        _unlink_private_file(state_path)
    elif pid_path.exists():
        pid = _read_candidate_pid_file(pid_path)
        if pid is None:
            matches = _find_exact_candidate_processes(command)
            if len(matches) > 1:
                raise WebAppIrTlsError(
                    "multiple candidate processes exist with an empty pid file"
                )
            pid = matches[0] if matches else None
        identity = _read_proc_identity(pid) if pid is not None else None
        if pid is not None and identity is not None:
            if not _candidate_process_matches(
                pid=pid,
                argv=identity[2],
                command=command,
            ):
                raise WebAppIrTlsError("candidate pid file points to a different process")
            _terminate_candidate_process_group(
                pid=pid,
                expected_start_ticks=identity[0],
                command=command,
                sleep_fn=sleep_fn,
            )
        _unlink_private_file(pid_path)
    matches = _find_exact_candidate_processes(command)
    if matches:
        raise WebAppIrTlsError("candidate process remains after reconciliation")
    _wait_for_listener(
        port,
        expected_open=False,
        timeout_seconds=10,
        sleep_fn=sleep_fn,
    )


def probe_loopback_candidate_nginx(
    *,
    candidate_receipt_path: Path,
    installation_receipt_path: Path,
    output_path: Path,
    ca_bundle: Path = DEFAULT_CA_BUNDLE,
    nginx_bin: Path = DEFAULT_NGINX,
    curl_bin: Path = DEFAULT_CURL,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    candidate = _read_json_secure(
        candidate_receipt_path,
        label="Nginx candidate receipt",
    )
    installation = _read_json_secure(
        installation_receipt_path,
        label="TLS installation receipt",
    )
    for key in ("campaign_id", "operation_id", "release_sha", "generation_id"):
        if candidate.get(key) != installation.get(key):
            raise WebAppIrTlsError("candidate and TLS installation identity mismatch")
    ca_attestation = attest_trusted_ca_bundle(ca_bundle)
    listener = str(candidate.get("candidate_listener", ""))
    match = re.fullmatch(r"127\.0\.0\.1:([0-9]+)", listener)
    if match is None:
        raise WebAppIrTlsError("candidate listener is not exact loopback")
    port = validate_tcp_port(int(match.group(1)), label="candidate listener port")
    config_path = Path(str(candidate.get("candidate_nginx_generation_path", "")))
    config_attestation = _file_attestation(
        config_path,
        label="Nginx candidate configuration",
    )
    if config_attestation["sha256"] != candidate.get(
        "candidate_nginx_generation_sha256"
    ):
        raise WebAppIrTlsError("candidate configuration hash drift")
    for executable, label in (
        (nginx_bin, "nginx"),
        (curl_bin, "curl"),
        (openssl_bin, "openssl"),
    ):
        validate_executable(executable, label=label)
    if not _port_available(port):
        raise WebAppIrTlsError("candidate loopback listener is already occupied")
    generation_root = config_path.parent
    _assert_private_directory(generation_root)
    command = [
        os.fspath(nginx_bin),
        "-c",
        os.fspath(config_path),
        "-p",
        os.fspath(generation_root),
        "-g",
        "daemon off;",
    ]
    _reconcile_candidate_process(
        generation_root=generation_root,
        candidate=candidate,
        command=command,
        config_sha256=config_attestation["sha256"],
        port=port,
        sleep_fn=sleep_fn,
    )
    if output_path.exists():
        receipt = _read_json_secure(
            output_path,
            label="Nginx candidate probe receipt",
        )
        expected = {
            "schema": f"{SCHEMA_PREFIX}.candidate-probe-receipt.v1",
            "campaign_id": candidate["campaign_id"],
            "operation_id": candidate["operation_id"],
            "release_sha": candidate["release_sha"],
            "role": "webapp_ir",
            "expected_host": WA_IR_PUBLIC_IP,
            "production_hostname": PRODUCTION_HOSTNAME,
            "generation_id": candidate["generation_id"],
            "candidate_listener": listener,
            "listener_absent_before": True,
            "listener_bound_during_probe": True,
            "listener_absent_after_twice": True,
            "shadow_upstream": candidate["shadow_upstream"],
            "shadow_upstream_loopback": True,
            "readiness_path": candidate["readiness_path"],
            "readiness_http_status": 200,
            "peer_leaf_cert_sha256": installation.get("leaf_cert_sha256"),
            "peer_public_key_spki_sha256": installation.get(
                "public_key_spki_sha256"
            ),
            "peer_hostname_verified": True,
            "peer_chain_verified": True,
            "ca_bundle": ca_attestation,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError("existing candidate probe receipt binding mismatch")
        if _listener_accepting(port):
            raise WebAppIrTlsError(
                "candidate listener remains despite completed probe receipt"
            )
        return {"status": "verified_existing", **receipt}
    try:
        process_state_path = generation_root / "candidate-process-state.json"
        spawn_intent = {
            "schema": f"{SCHEMA_PREFIX}.candidate-process-state.v1",
            "phase": "spawn_intent",
            "campaign_id": candidate.get("campaign_id"),
            "operation_id": candidate.get("operation_id"),
            "release_sha": candidate.get("release_sha"),
            "generation_id": candidate.get("generation_id"),
            "command": command,
            "command_sha256": _sha256_json(command),
            "executable_identity": _candidate_executable_identity(command),
            "config_sha256": config_attestation["sha256"],
            "candidate_port": port,
            "created_at": _now_text(),
        }
        _write_new_json(
            process_state_path,
            spawn_intent,
            label="candidate process state",
        )
        process = popen_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        _unlink_private_file(generation_root / "candidate-process-state.json")
        raise WebAppIrTlsError("failed to start isolated Nginx candidate") from exc
    identity = None
    identity_deadline = time.monotonic() + 5
    while time.monotonic() <= identity_deadline:
        identity = _read_proc_identity(process.pid)
        if identity is not None and _candidate_process_matches(
            pid=process.pid,
            argv=identity[2],
            command=command,
        ):
            break
        if process.poll() is not None:
            identity = None
            break
        sleep_fn(0.01)
    if identity is None or not _candidate_process_matches(
        pid=process.pid,
        argv=identity[2],
        command=command,
    ):
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        _unlink_private_file(generation_root / "candidate-nginx.pid")
        _unlink_private_file(generation_root / "candidate-process-state.json")
        raise WebAppIrTlsError("isolated Nginx candidate process identity is unavailable")
    if os.getpgid(process.pid) != process.pid:
        raise WebAppIrTlsError("isolated Nginx candidate did not own a process group")
    process_state = {
        **spawn_intent,
        "phase": "running",
        "pid": process.pid,
        "pgid": process.pid,
        "proc_start_ticks": identity[0],
        "started_at": _now_text(),
    }
    _write_atomic_json(
        generation_root / "candidate-process-state.json",
        process_state,
        label="candidate process state",
    )
    body_path = generation_root / f".probe-body-{secrets.token_hex(16)}.json"
    leaf_path = generation_root / f".probe-leaf-{secrets.token_hex(16)}.pem"
    process_stdout = b""
    process_stderr = b""
    try:
        _wait_for_listener(
            port,
            expected_open=True,
            timeout_seconds=10,
            sleep_fn=sleep_fn,
        )
        old_umask = os.umask(0o077)
        try:
            curl_command = [
                curl_bin,
                "--silent",
                "--show-error",
                "--fail",
                "--max-time",
                "10",
                "--cacert",
                ca_bundle,
                "--resolve",
                f"{PRODUCTION_HOSTNAME}:{port}:127.0.0.1",
                "--output",
                body_path,
                "--write-out",
                "%{http_code}",
                f"https://{PRODUCTION_HOSTNAME}:{port}/health/live",
            ]
            curl_result = _run(curl_command, timeout=20, run_fn=run_fn)
        finally:
            os.umask(old_umask)
        if curl_result.stdout != b"200":
            raise WebAppIrTlsError("candidate readiness probe did not return HTTP 200")
        body_path.chmod(0o600)
        body = read_secure_bytes(
            body_path,
            label="candidate readiness response",
            max_size=1024 * 1024,
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise WebAppIrTlsError("candidate readiness response is not JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "ok"
            or payload.get("physical_site") != "webapp_ir"
            or payload.get("logical_authority") != "webapp"
        ):
            raise WebAppIrTlsError("candidate readiness identity is not WebApp-IR")
        s_client_command = [
            openssl_bin,
            "s_client",
            "-connect",
            f"127.0.0.1:{port}",
            "-servername",
            PRODUCTION_HOSTNAME,
            "-verify_hostname",
            PRODUCTION_HOSTNAME,
            "-verify_return_error",
            "-CAfile",
            ca_bundle,
            "-showcerts",
        ]
        s_client = _run(
            s_client_command,
            input_bytes=b"",
            timeout=20,
            run_fn=run_fn,
        )
        peer_certificates = _pem_certificates(
            b"".join(
                re.findall(
                    rb"-----BEGIN CERTIFICATE-----\r?\n.+?-----END CERTIFICATE-----\r?\n?",
                    bytes(s_client.stdout),
                    flags=re.DOTALL,
                )
            )
        )
        write_secure_new_bytes(
            leaf_path,
            peer_certificates[0],
            label="candidate peer leaf",
            mode=0o600,
            max_size=MAX_CERTIFICATE_BYTES,
        )
        peer_der = _run(
            [openssl_bin, "x509", "-in", leaf_path, "-outform", "DER"],
            run_fn=run_fn,
        )
        peer_leaf_sha256 = _sha256_bytes(bytes(peer_der.stdout))
        peer_spki_sha256 = certificate_spki_sha256(
            leaf_path,
            openssl_bin=openssl_bin,
            run_fn=run_fn,
        )
        if peer_leaf_sha256 != installation.get("leaf_cert_sha256"):
            raise WebAppIrTlsError("candidate served a different leaf certificate")
        if peer_spki_sha256 != installation.get("public_key_spki_sha256"):
            raise WebAppIrTlsError("candidate served a different public key")
    finally:
        _terminate_candidate_process_group(
            pid=process.pid,
            expected_start_ticks=identity[0],
            command=command,
            sleep_fn=sleep_fn,
        )
        try:
            process_stdout, process_stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process_stdout, process_stderr = process.communicate(timeout=5)
        _unlink_private_file(body_path)
        _unlink_private_file(leaf_path)
        _wait_for_listener(
            port,
            expected_open=False,
            timeout_seconds=10,
            sleep_fn=sleep_fn,
        )
        sleep_fn(0.5)
        if _listener_accepting(port):
            raise WebAppIrTlsError("candidate listener cleanup is not stable")
        _unlink_private_file(generation_root / "candidate-nginx.pid")
        _unlink_private_file(generation_root / "candidate-process-state.json")
    if process.returncode not in (0, -15, -9):
        raise WebAppIrTlsError(
            f"isolated Nginx candidate exited unexpectedly: {process.returncode}"
        )
    receipt = {
        "schema": f"{SCHEMA_PREFIX}.candidate-probe-receipt.v1",
        "campaign_id": candidate["campaign_id"],
        "operation_id": candidate["operation_id"],
        "release_sha": candidate["release_sha"],
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "generation_id": candidate["generation_id"],
        "probed_at": _now_text(),
        "candidate_listener": listener,
        "listener_absent_before": True,
        "listener_bound_during_probe": True,
        "listener_absent_after_twice": True,
        "shadow_upstream": candidate["shadow_upstream"],
        "shadow_upstream_loopback": str(candidate["shadow_upstream"]).startswith(
            "127.0.0.1:"
        ),
        "readiness_path": candidate["readiness_path"],
        "readiness_http_status": 200,
        "readiness_body_sha256": _sha256_bytes(body),
        "readiness_identity": {
            "status": payload["status"],
            "physical_site": payload["physical_site"],
            "logical_authority": payload["logical_authority"],
        },
        "nginx_argv_sha256": _sha256_json([os.fspath(item) for item in command]),
        "nginx_exit": process.returncode,
        "nginx_stdout": _limited_digest(process_stdout),
        "nginx_stderr": _limited_digest(process_stderr),
        "curl_argv_sha256": _sha256_json([os.fspath(item) for item in curl_command]),
        "curl_stdout": _limited_digest(bytes(curl_result.stdout)),
        "curl_stderr": _limited_digest(bytes(curl_result.stderr)),
        "openssl_s_client_argv_sha256": _sha256_json(
            [os.fspath(item) for item in s_client_command]
        ),
        "openssl_s_client_stdout": _limited_digest(bytes(s_client.stdout)),
        "openssl_s_client_stderr": _limited_digest(bytes(s_client.stderr)),
        "peer_leaf_cert_sha256": peer_leaf_sha256,
        "peer_public_key_spki_sha256": peer_spki_sha256,
        "peer_hostname_verified": True,
        "peer_chain_verified": True,
        "ca_bundle": ca_attestation,
    }
    _write_new_json(output_path, receipt, label="Nginx candidate probe receipt")
    return receipt


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WebAppIrTlsError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _read_strict_json_document(
    path: Path,
    *,
    label: str,
    max_size: int = 16 * 1024 * 1024,
) -> tuple[dict[str, Any], str]:
    _assert_trusted_parent_chain(path)
    try:
        payload = read_secure_bytes(path, label=label, max_size=max_size)
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (SecureFileError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebAppIrTlsError(f"{label} is not strict secure JSON") from exc
    if not isinstance(decoded, dict):
        raise WebAppIrTlsError(f"{label} must be a JSON object")
    return decoded, _sha256_bytes(payload)


def _require_exact_fields(
    document: Any,
    fields: set[str],
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != fields:
        raise WebAppIrTlsError(f"{label} fields differ from the exact contract")
    return document


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validate_cutover_manifest_source(
    document: dict[str, Any],
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
) -> dict[str, Any]:
    manifest = _require_exact_fields(
        document,
        CUTOVER_MANIFEST_FIELDS,
        label="cutover manifest",
    )
    expected_identity = {
        "schema": CUTOVER_MANIFEST_SCHEMA,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
    }
    if any(manifest.get(key) != value for key, value in expected_identity.items()):
        raise WebAppIrTlsError("cutover manifest identity binding mismatch")
    if not _valid_timestamp(manifest.get("created_at")):
        raise WebAppIrTlsError("cutover manifest timestamp is invalid")
    release_tree_sha = validate_release_sha(str(manifest.get("release_tree_sha", "")))
    legacy_release_sha = validate_release_sha(
        str(manifest.get("legacy_release_sha", ""))
    )
    if release_tree_sha == legacy_release_sha or legacy_release_sha == release_sha:
        raise WebAppIrTlsError("cutover manifest release identities are not distinct")
    topology = _require_exact_fields(
        manifest.get("topology"),
        set(EXPECTED_CUTOVER_TOPOLOGY),
        label="cutover topology",
    )
    for role, expected in EXPECTED_CUTOVER_TOPOLOGY.items():
        actual = _require_exact_fields(
            topology.get(role),
            TOPOLOGY_FIELDS,
            label=f"cutover topology {role}",
        )
        if actual != expected:
            raise WebAppIrTlsError(
                f"cutover topology {role} differs from the canonical pin"
            )
    if len({entry["host"] for entry in topology.values()}) != len(topology):
        raise WebAppIrTlsError("cutover topology hosts are not physically distinct")
    deployment = _require_exact_fields(
        manifest.get("deployment"),
        CUTOVER_DEPLOYMENT_FIELDS,
        label="cutover deployment",
    )
    compact_operation = operation_id.replace("-", "")
    secure_root = (
        f"/root/secure-envs/trading-bot/production-cutover/{campaign_id}"
    )
    expected_deployment = {
        "production_hostname": PRODUCTION_HOSTNAME,
        "legacy_compose_project": "trading_bot",
        "shadow_compose_project": f"tb3p-{compact_operation}",
        "shadow_root": (
            f"/srv/trading-bot-three-site-production-shadow/{operation_id}"
        ),
        "controller_journal_path": f"{secure_root}/journal.json",
        "controller_evidence_root": f"{secure_root}/evidence",
    }
    if deployment != expected_deployment:
        raise WebAppIrTlsError("cutover deployment paths differ from the canonical pin")
    artifacts = _require_exact_fields(
        manifest.get("artifacts"),
        CUTOVER_ARTIFACT_FIELDS,
        label="cutover artifacts",
    )
    digest_fields = CUTOVER_ARTIFACT_FIELDS - {
        "release_bundle_bytes",
        "role_materials",
        "image_artifacts",
        "role_runtime_image_ids",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
        "postgres_image_ref",
    }
    for field in sorted(digest_fields):
        if not isinstance(artifacts[field], str):
            raise WebAppIrTlsError(
                f"cutover artifact {field} is not a string digest"
            )
        digest = validate_sha256(
            artifacts[field],
            label=f"artifacts.{field}",
        )
        if digest == "0" * 64:
            raise WebAppIrTlsError(f"cutover artifact {field} is zero")
    if (
        isinstance(artifacts["release_bundle_bytes"], bool)
        or not isinstance(artifacts["release_bundle_bytes"], int)
        or not 1
        <= artifacts["release_bundle_bytes"]
        <= 64 * 1024 * 1024 * 1024
    ):
        raise WebAppIrTlsError("cutover release bundle size is invalid")

    role_materials = _require_exact_fields(
        artifacts["role_materials"],
        set(EXPECTED_CUTOVER_TOPOLOGY),
        label="cutover role materials",
    )
    role_material_digests: set[str] = set()
    for role, topology_entry in EXPECTED_CUTOVER_TOPOLOGY.items():
        material = _require_exact_fields(
            role_materials[role],
            ROLE_MATERIAL_FIELDS,
            label=f"cutover role material {role}",
        )
        if not isinstance(material["sha256"], str):
            raise WebAppIrTlsError(
                f"cutover role material {role} digest is invalid"
            )
        digest = validate_sha256(
            material["sha256"],
            label=f"artifacts.role_materials.{role}.sha256",
        )
        expected_format = (
            "production-shadow-witness-material-tar"
            if role == "witness"
            else "production-shadow-role-material-tar"
        )
        if (
            digest == "0" * 64
            or isinstance(material["bytes"], bool)
            or not isinstance(material["bytes"], int)
            or not 1 <= material["bytes"] <= 64 * 1024 * 1024 * 1024
            or material["transport"] != topology_entry["transport"]
            or material["format"] != expected_format
        ):
            raise WebAppIrTlsError(
                f"cutover role material {role} is invalid"
            )
        role_material_digests.add(digest)
    if len(role_material_digests) != len(EXPECTED_CUTOVER_TOPOLOGY):
        raise WebAppIrTlsError("cutover role material digests are not distinct")

    image_artifacts = _require_exact_fields(
        artifacts["image_artifacts"],
        set(IMAGE_KINDS),
        label="cutover image artifacts",
    )
    distinct_image_fields: dict[str, set[str]] = {
        "archive_sha256": set(),
        "config_digest": set(),
        "content_identity": set(),
    }
    for kind in IMAGE_KINDS:
        image = _require_exact_fields(
            image_artifacts[kind],
            IMAGE_ARTIFACT_FIELDS,
            label=f"cutover image artifact {kind}",
        )
        if not isinstance(image["archive_sha256"], str):
            raise WebAppIrTlsError(
                f"cutover image artifact {kind} archive digest is invalid"
            )
        archive_sha256 = validate_sha256(
            image["archive_sha256"],
            label=f"artifacts.image_artifacts.{kind}.archive_sha256",
        )
        if (
            archive_sha256 == "0" * 64
            or isinstance(image["archive_bytes"], bool)
            or not isinstance(image["archive_bytes"], int)
            or not 1 <= image["archive_bytes"] <= 64 * 1024 * 1024 * 1024
            or not isinstance(image["config_digest"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image["config_digest"])
            is None
            or image["config_digest"] == f"sha256:{'0' * 64}"
            or not isinstance(image["content_identity"], str)
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                image["content_identity"],
            )
            is None
            or image["content_identity"] == f"sha256:{'0' * 64}"
        ):
            raise WebAppIrTlsError(
                f"cutover image artifact {kind} identity is invalid"
            )
        try:
            observed_content_identity = verify_content_descriptor(
                image["content_descriptor"]
            )
        except DockerImageIdentityError as exc:
            raise WebAppIrTlsError(
                f"cutover image artifact {kind} descriptor is invalid"
            ) from exc
        if (
            image["content_descriptor"]["architecture"] != "amd64"
            or image["content_descriptor"]["os"] != "linux"
            or observed_content_identity != image["content_identity"]
        ):
            raise WebAppIrTlsError(
                f"cutover image artifact {kind} content identity differs"
            )
        distinct_image_fields["archive_sha256"].add(archive_sha256)
        distinct_image_fields["config_digest"].add(image["config_digest"])
        distinct_image_fields["content_identity"].add(
            image["content_identity"]
        )
    if any(
        len(values) != len(IMAGE_KINDS)
        for values in distinct_image_fields.values()
    ):
        raise WebAppIrTlsError("cutover image artifact identities are not distinct")

    runtime_inventory = _require_exact_fields(
        artifacts["role_runtime_image_ids"],
        set(DOCKER_RUNTIME_ROLES),
        label="cutover runtime image inventory",
    )
    for role in DOCKER_RUNTIME_ROLES:
        role_inventory = _require_exact_fields(
            runtime_inventory[role],
            set(IMAGE_KINDS),
            label=f"cutover runtime image inventory {role}",
        )
        values = list(role_inventory.values())
        if (
            any(
                not isinstance(value, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                or value == f"sha256:{'0' * 64}"
                for value in values
            )
            or len(set(values)) != len(IMAGE_KINDS)
        ):
            raise WebAppIrTlsError(
                f"cutover runtime image inventory {role} is invalid"
            )
    if (
        artifacts["postgres_runtime_uid"] != 70
        or artifacts["postgres_runtime_gid"] != 70
    ):
        raise WebAppIrTlsError("cutover PostgreSQL runtime owner is invalid")
    if (
        artifacts["postgres_image_ref"]
        != f"trading_bot_postgres_boottime:15-{release_sha}"
    ):
        raise WebAppIrTlsError("cutover PostgreSQL image ref is invalid")
    policy = _require_exact_fields(
        manifest.get("policy"),
        CUTOVER_POLICY_FIELDS,
        label="cutover policy",
    )
    if any(value is not True for value in policy.values()):
        raise WebAppIrTlsError("cutover manifest contains a disabled safety policy")
    return manifest


def _validate_activation_precondition_source(
    document: dict[str, Any],
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    precondition = _require_exact_fields(
        document,
        ACTIVATION_PRECONDITION_FIELDS,
        label="activation precondition evidence",
    )
    expected = {
        "schema": ACTIVATION_PRECONDITION_SCHEMA,
        "status": "verified",
        "phase": ACTIVATION_PRECONDITION_PHASE,
        "operation": ACTIVATION_PRECONDITION_OPERATION,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "manifest_sha256": manifest_sha256,
        "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
        "phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "manifest_artifact_bindings_sha256": _sha256_json(
            manifest["artifacts"]
        ),
        "prior_phase_count": 20,
        "verified_roles": EXPECTED_ACTIVATION_ROLES,
        "production_contacted": False,
    }
    if any(precondition.get(key) != value for key, value in expected.items()):
        raise WebAppIrTlsError("activation precondition evidence binding mismatch")
    for field in (
        "plan_sha256",
        "approval_sha256",
        "phase_evidence_schema_sha256",
        "manifest_artifact_bindings_sha256",
        "prior_phase_evidence_closure_sha256",
        "phase_input_closure_sha256",
        "evidence_sha256",
    ):
        digest = validate_sha256(
            str(precondition.get(field, "")),
            label=f"activation precondition {field}",
        )
        if digest == "0" * 64:
            raise WebAppIrTlsError(
                f"activation precondition {field} must be nonzero"
            )
    claim_count = precondition.get("verified_claim_count")
    if (
        not isinstance(claim_count, int)
        or isinstance(claim_count, bool)
        or claim_count < 1
        or not _valid_timestamp(precondition.get("captured_at"))
        or not _valid_timestamp(precondition.get("verified_at"))
    ):
        raise WebAppIrTlsError(
            "activation precondition verification metadata is invalid"
        )
    return precondition


def _require_bound_documents(
    documents: Iterable[tuple[str, dict[str, Any]]],
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
) -> None:
    expected = {
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
    }
    for label, document in documents:
        if any(document.get(key) != value for key, value in expected.items()):
            raise WebAppIrTlsError(f"{label} identity binding mismatch")


def build_activation_documents(
    *,
    installation_receipt_path: Path,
    issuance_receipt_path: Path,
    dns_baseline_path: Path,
    nginx_baseline_before_path: Path,
    nginx_baseline_after_path: Path,
    candidate_receipt_path: Path,
    probe_receipt_path: Path,
    runtime_safety_attestation_path: Path,
    cutover_manifest_path: Path,
    activation_precondition_evidence_path: Path,
    runtime_source_binding: dict[str, Any],
    manifest_output_path: Path,
    evidence_output_path: Path,
    ca_bundle: Path = DEFAULT_CA_BUNDLE,
    openssl_bin: Path = DEFAULT_OPENSSL,
    run_fn: RunFn = subprocess.run,
) -> tuple[dict[str, Any], dict[str, Any]]:
    installation = _read_json_secure(
        installation_receipt_path,
        label="TLS installation receipt",
    )
    issuance = _read_json_secure(
        issuance_receipt_path,
        label="TLS issuance receipt",
    )
    dns = _read_json_secure(dns_baseline_path, label="DNS baseline")
    nginx_before = _read_json_secure(
        nginx_baseline_before_path,
        label="active Nginx baseline before",
    )
    nginx_after = _read_json_secure(
        nginx_baseline_after_path,
        label="active Nginx baseline after",
    )
    candidate = _read_json_secure(candidate_receipt_path, label="Nginx candidate receipt")
    probe = _read_json_secure(probe_receipt_path, label="Nginx candidate probe receipt")
    runtime = _read_json_secure(
        runtime_safety_attestation_path,
        label="WebApp-IR runtime safety attestation",
    )
    campaign_id = validate_campaign_id(str(installation.get("campaign_id", "")))
    operation_id = validate_operation_id(str(installation.get("operation_id", "")))
    release_sha = validate_release_sha(str(installation.get("release_sha", "")))
    cutover_source, cutover_manifest_sha256 = _read_strict_json_document(
        cutover_manifest_path,
        label="production cutover manifest",
    )
    cutover_source = _validate_cutover_manifest_source(
        cutover_source,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
    )
    precondition_source, activation_precondition_evidence_sha256 = (
        _read_strict_json_document(
            activation_precondition_evidence_path,
            label="activation precondition evidence",
        )
    )
    _validate_activation_precondition_source(
        precondition_source,
        manifest=cutover_source,
        manifest_sha256=cutover_manifest_sha256,
    )
    _require_bound_documents(
        (
            ("issuance receipt", issuance),
            ("DNS baseline", dns),
            ("Nginx baseline before", nginx_before),
            ("Nginx baseline after", nginx_after),
            ("candidate receipt", candidate),
            ("probe receipt", probe),
            ("runtime safety attestation", runtime),
        ),
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
    )
    if (
        runtime_source_binding.get("schema")
        != f"{SCHEMA_PREFIX}.runtime-source-binding.v1"
        or runtime_source_binding.get("release_sha") != release_sha
        or runtime_source_binding.get("git_head") != release_sha
        or runtime_source_binding.get("tracked_files_clean") is not True
    ):
        raise WebAppIrTlsError("TLS worker runtime source is not exact-release bound")
    for field in (
        "worker_sha256",
        "secure_file_helper_sha256",
        "docker_image_identity_helper_sha256",
        "repository_root_sha256",
    ):
        validate_sha256(str(runtime_source_binding.get(field, "")), label=field)
    if (
        runtime_source_binding.get("worker_path")
        != "scripts/prepare_webapp_ir_tls_dns.py"
        or runtime_source_binding.get("secure_file_helper_path")
        != "core/secure_file_io.py"
        or runtime_source_binding.get("docker_image_identity_helper_path")
        != "core/docker_image_identity.py"
    ):
        raise WebAppIrTlsError("TLS worker runtime source paths are not canonical")
    expected_schemas = {
        "installation receipt": (
            installation,
            f"{SCHEMA_PREFIX}.installation-receipt.v1",
        ),
        "issuance receipt": (
            issuance,
            f"{SCHEMA_PREFIX}.issuance-receipt.v1",
        ),
        "DNS baseline": (dns, f"{SCHEMA_PREFIX}.dns-baseline.v1"),
        "Nginx baseline before": (
            nginx_before,
            f"{SCHEMA_PREFIX}.nginx-baseline.v1",
        ),
        "Nginx baseline after": (
            nginx_after,
            f"{SCHEMA_PREFIX}.nginx-baseline.v1",
        ),
        "candidate receipt": (
            candidate,
            f"{SCHEMA_PREFIX}.candidate-nginx-receipt.v1",
        ),
        "probe receipt": (
            probe,
            f"{SCHEMA_PREFIX}.candidate-probe-receipt.v1",
        ),
    }
    for label, (document, schema) in expected_schemas.items():
        if document.get("schema") != schema:
            raise WebAppIrTlsError(f"{label} schema mismatch")
    if installation.get("role") != "webapp_ir" or candidate.get("role") != "webapp_ir":
        raise WebAppIrTlsError("TLS activation documents are not bound to WebApp-IR")
    if installation.get("expected_host") != WA_IR_PUBLIC_IP:
        raise WebAppIrTlsError("TLS installation expected_host is not WA-IR")
    if installation.get("production_hostname") != PRODUCTION_HOSTNAME:
        raise WebAppIrTlsError("TLS installation hostname mismatch")
    generation_root = Path(str(installation.get("generation_path", "")))
    _assert_private_directory(generation_root)
    expected_generation_files = {
        "private_key": generation_root / "private-key.pem",
        "csr": generation_root / "request.csr",
        "leaf": generation_root / "leaf.pem",
        "chain": generation_root / "chain.pem",
        "fullchain": generation_root / "fullchain.pem",
    }
    fresh_file_attestations = {
        name: _file_attestation(path, label=f"final installed {name}")
        for name, path in expected_generation_files.items()
    }
    if installation.get("files") != fresh_file_attestations:
        raise WebAppIrTlsError("installed TLS files changed after installation receipt")
    fresh_verification = verify_certificate_material(
        private_key_path=expected_generation_files["private_key"],
        csr_path=expected_generation_files["csr"],
        fullchain_path=expected_generation_files["fullchain"],
        chain_path=expected_generation_files["chain"],
        ca_bundle=ca_bundle,
        openssl_bin=openssl_bin,
        run_fn=run_fn,
    )
    if any(
        installation.get(field) != value
        for field, value in fresh_verification.items()
    ):
        raise WebAppIrTlsError(
            "installed TLS certificate no longer matches its verified receipt"
        )
    if issuance.get("ca_bundle") != fresh_verification["ca_bundle"]:
        raise WebAppIrTlsError("TLS issuance used a different CA trust bundle")
    if probe.get("ca_bundle") != fresh_verification["ca_bundle"]:
        raise WebAppIrTlsError("TLS candidate probe used a different CA trust bundle")
    candidate_config_path = generation_root / "candidate-nginx.conf"
    if candidate.get("candidate_nginx_generation_path") != str(
        candidate_config_path
    ):
        raise WebAppIrTlsError("candidate Nginx path is outside the TLS generation")
    fresh_candidate_attestation = _file_attestation(
        candidate_config_path,
        label="final Nginx candidate configuration",
    )
    if fresh_candidate_attestation["sha256"] != candidate.get(
        "candidate_nginx_generation_sha256"
    ):
        raise WebAppIrTlsError("candidate Nginx configuration changed after staging")
    if (
        installation.get("csr_sha256") != issuance.get("csr_sha256")
        or installation.get("leaf_cert_sha256") != issuance.get("leaf_cert_sha256")
        or installation.get("fullchain_sha256") != issuance.get("fullchain_sha256")
        or installation.get("public_key_spki_sha256")
        != issuance.get("public_key_spki_sha256")
    ):
        raise WebAppIrTlsError("installed TLS generation differs from controller issuance")
    if candidate.get("generation_id") != installation.get("generation_id"):
        raise WebAppIrTlsError("Nginx candidate generation differs from installed TLS")
    if probe.get("generation_id") != installation.get("generation_id"):
        raise WebAppIrTlsError("Nginx probe generation differs from installed TLS")
    if probe.get("peer_leaf_cert_sha256") != installation.get("leaf_cert_sha256"):
        raise WebAppIrTlsError("Nginx probe leaf fingerprint mismatch")
    if (
        probe.get("peer_public_key_spki_sha256")
        != installation.get("public_key_spki_sha256")
    ):
        raise WebAppIrTlsError("Nginx probe SPKI fingerprint mismatch")
    if not all(
        probe.get(field) is True
        for field in (
            "listener_absent_before",
            "listener_bound_during_probe",
            "listener_absent_after_twice",
            "shadow_upstream_loopback",
            "peer_hostname_verified",
            "peer_chain_verified",
        )
    ):
        raise WebAppIrTlsError("Nginx candidate evidence is incomplete")
    if probe.get("readiness_http_status") != 200:
        raise WebAppIrTlsError("Nginx candidate readiness did not return HTTP 200")
    expected_a_hash = dns.get("expected_pre_activation_dns_a_rrset_sha256")
    if (
        issuance.get("before_a_rrset_sha256") != expected_a_hash
        or issuance.get("after_a_rrset_sha256") != expected_a_hash
    ):
        raise WebAppIrTlsError("DNS-01 issuance did not preserve production A routing")
    active_nginx_hash = nginx_before.get("active_nginx_generation_sha256")
    if (
        active_nginx_hash != nginx_after.get("active_nginx_generation_sha256")
        or candidate.get("active_nginx_before_sha256") != active_nginx_hash
        or candidate.get("active_nginx_after_sha256") != active_nginx_hash
    ):
        raise WebAppIrTlsError("active Nginx changed during candidate preparation")
    if (
        runtime.get("physical_site") != "webapp_ir"
        or runtime.get("background_jobs_enabled") is not False
        or runtime.get("effects_started") is not False
        or runtime.get("shadow_upstream_loopback") is not True
    ):
        raise WebAppIrTlsError("runtime safety attestation does not prove an inert WA shadow")
    if dns.get("desired_dns_a_rrset", {}).get("value", [{}])[0].get("ip") != WA_IR_PUBLIC_IP:
        raise WebAppIrTlsError("desired production A RRset is not WA-IR")
    if (
        dns.get("rollback_dns_a_rrset_sha256")
        != dns.get("expected_pre_activation_dns_a_rrset_sha256")
    ):
        raise WebAppIrTlsError("rollback DNS RRset differs from the captured current route")
    if fresh_verification.get("key_csr_match") is not True or fresh_verification.get(
        "key_cert_match"
    ) is not True:
        raise WebAppIrTlsError("TLS key binding evidence is false")
    if (
        fresh_verification.get("chain_verified") is not True
        or fresh_verification.get("hostname_verified") is not True
        or fresh_verification.get("validity_verified") is not True
        or fresh_verification.get("exact_sans")
        != {"dns": [PRODUCTION_HOSTNAME], "ip": []}
        or fresh_verification.get("required_eku") != ["serverAuth"]
    ):
        raise WebAppIrTlsError("TLS certificate semantics are not fully verified")

    evidence_core = {
        "schema": f"{SCHEMA_PREFIX}.activation-evidence.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "cutover_manifest_sha256": cutover_manifest_sha256,
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "generation_id": installation["generation_id"],
        "generated_at": _now_text(),
        "key_csr_match": True,
        "key_cert_match": True,
        "csr_cert_match": True,
        "san_result": {
            "verified": True,
            "exact_sans": fresh_verification["exact_sans"],
        },
        "eku_result": {
            "verified": True,
            "required_eku": fresh_verification["required_eku"],
        },
        "chain_result": {
            "verified": True,
            "ca_bundle": fresh_verification["ca_bundle"],
        },
        "validity_result": {
            "verified": True,
            "not_before": fresh_verification["not_before"],
            "not_after": fresh_verification["not_after"],
        },
        "file_attestations": fresh_file_attestations,
        "dns01_receipts": {
            "journal_sha256": issuance["dns01_journal_sha256"],
            "event_hashes": issuance["dns01_event_hashes"],
            "record_receipts": issuance.get("dns01_record_receipts", []),
            "create_propagate_delete_verified": True,
        },
        "production_a_route_before_sha256": issuance["before_a_rrset_sha256"],
        "production_a_route_after_sha256": issuance["after_a_rrset_sha256"],
        "production_a_route_unchanged": True,
        "nginx_t": {
            "argv_sha256": candidate["nginx_t_argv_sha256"],
            "exit": candidate["nginx_t_exit"],
            "stdout": candidate["nginx_t_stdout"],
            "stderr": candidate["nginx_t_stderr"],
        },
        "candidate_listener_bind_proof": {
            "listener": probe["candidate_listener"],
            "absent_before": True,
            "bound_during_probe": True,
            "absent_after_twice": True,
        },
        "curl_resolve": {
            "argv_sha256": probe["curl_argv_sha256"],
            "http_status": probe["readiness_http_status"],
            "body_sha256": probe["readiness_body_sha256"],
            "peer_leaf_cert_sha256": probe["peer_leaf_cert_sha256"],
            "peer_public_key_spki_sha256": probe["peer_public_key_spki_sha256"],
            "peer_hostname_verified": True,
            "peer_chain_verified": True,
        },
        "active_nginx_before_sha256": active_nginx_hash,
        "active_nginx_after_sha256": active_nginx_hash,
        "active_nginx_unchanged": True,
        "public_route_before_sha256": issuance["before_a_rrset_sha256"],
        "public_route_after_sha256": issuance["after_a_rrset_sha256"],
        "public_route_unchanged": True,
        "effects_jobs": {
            "background_jobs_enabled": False,
            "effects_started": False,
            "attestation_sha256": sha256_secure_file(
                runtime_safety_attestation_path,
                label="runtime safety attestation",
            )[0],
        },
        "shadow_upstream": candidate["shadow_upstream"],
        "shadow_upstream_loopback": True,
        "activation_precondition_evidence_sha256": (
            activation_precondition_evidence_sha256
        ),
        "runtime_source_binding": runtime_source_binding,
    }
    evidence_core_sha256 = _sha256_json(evidence_core)
    manifest = {
        "schema": f"{SCHEMA_PREFIX}.activation-manifest.v1",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "cutover_manifest_sha256": cutover_manifest_sha256,
        "role": "webapp_ir",
        "expected_host": WA_IR_PUBLIC_IP,
        "production_hostname": PRODUCTION_HOSTNAME,
        "generation_id": installation["generation_id"],
        "generated_at": _now_text(),
        "not_before": fresh_verification["not_before"],
        "not_after": fresh_verification["not_after"],
        "csr_sha256": fresh_verification["csr_sha256"],
        "leaf_cert_sha256": fresh_verification["leaf_cert_sha256"],
        "fullchain_sha256": fresh_verification["fullchain_sha256"],
        "public_key_spki_sha256": fresh_verification["public_key_spki_sha256"],
        "private_key_path": fresh_file_attestations["private_key"]["path"],
        "cert_path": fresh_file_attestations["leaf"]["path"],
        "fullchain_path": fresh_file_attestations["fullchain"]["path"],
        "candidate_nginx_generation_path": candidate[
            "candidate_nginx_generation_path"
        ],
        "candidate_nginx_generation_sha256": candidate[
            "candidate_nginx_generation_sha256"
        ],
        "candidate_listener": candidate["candidate_listener"],
        "shadow_upstream": candidate["shadow_upstream"],
        "exact_sans": installation["exact_sans"],
        "required_eku": installation["required_eku"],
        "readiness_url": candidate["readiness_url"],
        "readiness_path": candidate["readiness_path"],
        "expected_pre_activation_dns_a_rrset": dns[
            "expected_pre_activation_dns_a_rrset"
        ],
        "expected_pre_activation_dns_a_rrset_sha256": dns[
            "expected_pre_activation_dns_a_rrset_sha256"
        ],
        "desired_dns_a_rrset": dns["desired_dns_a_rrset"],
        "desired_dns_a_rrset_sha256": dns["desired_dns_a_rrset_sha256"],
        "rollback_dns_a_rrset": dns["rollback_dns_a_rrset"],
        "rollback_dns_a_rrset_sha256": dns["rollback_dns_a_rrset_sha256"],
        "expected_active_nginx_generation_sha256": active_nginx_hash,
        "activation_precondition_evidence_sha256": (
            activation_precondition_evidence_sha256
        ),
        "tls_activation_evidence_core_sha256": evidence_core_sha256,
        "runtime_source_binding": runtime_source_binding,
        "active_nginx_mutated": False,
        "production_a_record_mutated": False,
    }
    _write_new_json(
        manifest_output_path,
        manifest,
        label="WA-IR TLS activation manifest",
    )
    manifest_sha256 = sha256_secure_file(
        manifest_output_path,
        label="WA-IR TLS activation manifest",
    )[0]
    evidence = {
        **evidence_core,
        "manifest_sha256": manifest_sha256,
        "evidence_core_sha256": evidence_core_sha256,
    }
    _write_new_json(
        evidence_output_path,
        evidence,
        label="WA-IR TLS activation evidence",
    )
    return manifest, evidence


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare WA-IR public TLS using CSR + Arvan DNS-01 without changing "
            "production A routing or active Nginx."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-csr")
    _add_identity_arguments(generate)
    generate.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    generate.add_argument("--openssl", type=Path, default=DEFAULT_OPENSSL)

    dns_auth = subparsers.add_parser("dns-auth")
    _add_identity_arguments(dns_auth)
    dns_auth.add_argument("--arvan-token-file", type=Path, required=True)
    dns_auth.add_argument("--state-dir", type=Path, required=True)
    dns_auth.add_argument("--journal", type=Path, required=True)
    dns_auth.add_argument("--dig", type=Path, default=DEFAULT_DIG)

    dns_cleanup = subparsers.add_parser("dns-cleanup")
    _add_identity_arguments(dns_cleanup)
    dns_cleanup.add_argument("--arvan-token-file", type=Path, required=True)
    dns_cleanup.add_argument("--state-dir", type=Path, required=True)
    dns_cleanup.add_argument("--journal", type=Path, required=True)

    dns_reconcile = subparsers.add_parser("dns-reconcile")
    _add_identity_arguments(dns_reconcile)
    dns_reconcile.add_argument("--arvan-token-file", type=Path, required=True)
    dns_reconcile.add_argument("--state-dir", type=Path, required=True)
    dns_reconcile.add_argument("--journal", type=Path, required=True)

    dns_baseline = subparsers.add_parser("capture-dns-baseline")
    _add_identity_arguments(dns_baseline)
    dns_baseline.add_argument("--arvan-token-file", type=Path, required=True)
    dns_baseline.add_argument("--output", type=Path, required=True)
    dns_baseline.add_argument("--dig", type=Path, default=DEFAULT_DIG)

    nginx_baseline = subparsers.add_parser("capture-nginx-baseline")
    _add_identity_arguments(nginx_baseline)
    nginx_baseline.add_argument("--output", type=Path, required=True)
    nginx_baseline.add_argument("--nginx", type=Path, default=DEFAULT_NGINX)

    issue = subparsers.add_parser("issue-certificate")
    _add_identity_arguments(issue)
    issue.add_argument("--csr", type=Path, required=True)
    issue.add_argument("--output-dir", type=Path, required=True)
    issue.add_argument("--email", required=True)
    issue.add_argument("--arvan-token-file", type=Path, required=True)
    issue.add_argument("--certbot", type=Path, default=DEFAULT_CERTBOT)
    issue.add_argument("--openssl", type=Path, default=DEFAULT_OPENSSL)
    issue.add_argument("--ca-bundle", type=Path, default=DEFAULT_CA_BUNDLE)

    install = subparsers.add_parser("install-certificate")
    _add_identity_arguments(install)
    install.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    install.add_argument("--fullchain", type=Path, required=True)
    install.add_argument("--chain", type=Path, required=True)
    install.add_argument("--openssl", type=Path, default=DEFAULT_OPENSSL)
    install.add_argument("--ca-bundle", type=Path, default=DEFAULT_CA_BUNDLE)

    stage = subparsers.add_parser("stage-candidate")
    stage.add_argument("--installation-receipt", type=Path, required=True)
    stage.add_argument("--candidate-port", type=int, required=True)
    stage.add_argument("--shadow-upstream-port", type=int, required=True)
    stage.add_argument("--nginx-baseline-before", type=Path, required=True)
    stage.add_argument("--nginx-baseline-after", type=Path, required=True)
    stage.add_argument("--nginx", type=Path, default=DEFAULT_NGINX)

    probe = subparsers.add_parser("probe-candidate")
    probe.add_argument("--candidate-receipt", type=Path, required=True)
    probe.add_argument("--installation-receipt", type=Path, required=True)
    probe.add_argument("--output", type=Path, required=True)
    probe.add_argument("--ca-bundle", type=Path, default=DEFAULT_CA_BUNDLE)
    probe.add_argument("--nginx", type=Path, default=DEFAULT_NGINX)
    probe.add_argument("--curl", type=Path, default=DEFAULT_CURL)
    probe.add_argument("--openssl", type=Path, default=DEFAULT_OPENSSL)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--installation-receipt", type=Path, required=True)
    finalize.add_argument("--issuance-receipt", type=Path, required=True)
    finalize.add_argument("--dns-baseline", type=Path, required=True)
    finalize.add_argument("--nginx-baseline-before", type=Path, required=True)
    finalize.add_argument("--nginx-baseline-after", type=Path, required=True)
    finalize.add_argument("--candidate-receipt", type=Path, required=True)
    finalize.add_argument("--probe-receipt", type=Path, required=True)
    finalize.add_argument("--runtime-safety-attestation", type=Path, required=True)
    finalize.add_argument("--cutover-manifest", type=Path, required=True)
    finalize.add_argument(
        "--activation-precondition-evidence",
        type=Path,
        required=True,
    )
    finalize.add_argument("--manifest-output", type=Path, required=True)
    finalize.add_argument("--evidence-output", type=Path, required=True)
    finalize.add_argument("--openssl", type=Path, default=DEFAULT_OPENSSL)
    finalize.add_argument("--ca-bundle", type=Path, default=DEFAULT_CA_BUNDLE)
    return parser.parse_args(argv)


def _certbot_environment_value(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise WebAppIrTlsError(f"{name} is required from the Certbot hook environment")
    return value


def _require_production_ca_bundle(path: Path) -> None:
    if path != DEFAULT_CA_BUNDLE:
        raise WebAppIrTlsError(
            "production CLI is pinned to the system CA bundle"
        )
    attest_trusted_ca_bundle(path)


def _run_command_unbound(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "generate-csr":
        lock_root = _operation_root(
            args.campaign_root,
            validate_campaign_id(args.campaign_id),
            validate_operation_id(args.operation_id),
        )
        with _exclusive_operation_lock(lock_root, name="csr-generation.lock"):
            return generate_wa_ir_key_and_csr(
                campaign_root=args.campaign_root,
                campaign_id=args.campaign_id,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                openssl_bin=args.openssl,
            )
    if args.command == "dns-auth":
        domain = _certbot_environment_value("CERTBOT_DOMAIN")
        if domain != PRODUCTION_HOSTNAME:
            raise WebAppIrTlsError("Certbot requested an unexpected domain")
        validation = _certbot_environment_value("CERTBOT_VALIDATION")
        with _exclusive_operation_lock(args.state_dir, name="dns01-provider.lock"):
            return create_dns01_challenge(
                campaign_id=args.campaign_id,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                validation=validation,
                token=load_arvan_token(args.arvan_token_file),
                state_dir=args.state_dir,
                journal_path=args.journal,
                propagation_fn=lambda **kwargs: wait_for_authoritative_txt(
                    **kwargs,
                    dig_bin=args.dig,
                ),
            )
    if args.command == "dns-cleanup":
        domain = _certbot_environment_value("CERTBOT_DOMAIN")
        if domain != PRODUCTION_HOSTNAME:
            raise WebAppIrTlsError("Certbot requested cleanup for an unexpected domain")
        with _exclusive_operation_lock(args.state_dir, name="dns01-provider.lock"):
            return delete_dns01_challenge(
                campaign_id=args.campaign_id,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                validation=_certbot_environment_value("CERTBOT_VALIDATION"),
                token=load_arvan_token(args.arvan_token_file),
                state_dir=args.state_dir,
                journal_path=args.journal,
            )
    if args.command == "dns-reconcile":
        with _exclusive_operation_lock(args.state_dir, name="dns01-provider.lock"):
            return reconcile_dns01_state(
                campaign_id=args.campaign_id,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                token=load_arvan_token(args.arvan_token_file),
                state_dir=args.state_dir,
                journal_path=args.journal,
            )
    if args.command == "capture-dns-baseline":
        return capture_dns_baseline(
            campaign_id=args.campaign_id,
            operation_id=args.operation_id,
            release_sha=args.release_sha,
            token=load_arvan_token(args.arvan_token_file),
            output_path=args.output,
            dig_bin=args.dig,
        )
    if args.command == "capture-nginx-baseline":
        return capture_active_nginx_baseline(
            campaign_id=args.campaign_id,
            operation_id=args.operation_id,
            release_sha=args.release_sha,
            output_path=args.output,
            nginx_bin=args.nginx,
        )
    if args.command == "issue-certificate":
        _require_production_ca_bundle(args.ca_bundle)
        with _exclusive_operation_lock(args.output_dir, name="certificate-issuance.lock"):
            return issue_certificate_from_csr(
                campaign_id=args.campaign_id,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                csr_path=args.csr,
                output_dir=args.output_dir,
                email=args.email,
                token_file=args.arvan_token_file,
                script_path=Path(__file__).resolve(),
                certbot_bin=args.certbot,
                openssl_bin=args.openssl,
                ca_bundle=args.ca_bundle,
            )
    if args.command == "install-certificate":
        _require_production_ca_bundle(args.ca_bundle)
        lock_root = _operation_root(
            args.campaign_root,
            validate_campaign_id(args.campaign_id),
            validate_operation_id(args.operation_id),
        )
        with _exclusive_operation_lock(lock_root, name="certificate-install.lock"):
            return install_wa_ir_certificate_generation(
                campaign_root=args.campaign_root,
                campaign_id=args.campaign_id,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                transported_fullchain_path=args.fullchain,
                transported_chain_path=args.chain,
                openssl_bin=args.openssl,
                ca_bundle=args.ca_bundle,
            )
    if args.command == "stage-candidate":
        generation_root = args.installation_receipt.parent
        with _exclusive_operation_lock(generation_root, name="candidate-stage.lock"):
            return stage_loopback_candidate_nginx(
                installation_receipt_path=args.installation_receipt,
                candidate_port=args.candidate_port,
                shadow_upstream_port=args.shadow_upstream_port,
                nginx_baseline_before_path=args.nginx_baseline_before,
                nginx_baseline_after_path=args.nginx_baseline_after,
                nginx_bin=args.nginx,
            )
    if args.command == "probe-candidate":
        _require_production_ca_bundle(args.ca_bundle)
        generation_root = args.candidate_receipt.parent
        with _exclusive_operation_lock(generation_root, name="candidate-probe.lock"):
            return probe_loopback_candidate_nginx(
                candidate_receipt_path=args.candidate_receipt,
                installation_receipt_path=args.installation_receipt,
                output_path=args.output,
                ca_bundle=args.ca_bundle,
                nginx_bin=args.nginx,
                curl_bin=args.curl,
                openssl_bin=args.openssl,
            )
    if args.command == "finalize":
        _require_production_ca_bundle(args.ca_bundle)
        with _exclusive_operation_lock(
            args.manifest_output.parent,
            name="activation-finalize.lock",
        ):
            manifest, evidence = build_activation_documents(
                installation_receipt_path=args.installation_receipt,
                issuance_receipt_path=args.issuance_receipt,
                dns_baseline_path=args.dns_baseline,
                nginx_baseline_before_path=args.nginx_baseline_before,
                nginx_baseline_after_path=args.nginx_baseline_after,
                candidate_receipt_path=args.candidate_receipt,
                probe_receipt_path=args.probe_receipt,
                runtime_safety_attestation_path=args.runtime_safety_attestation,
                cutover_manifest_path=args.cutover_manifest,
                activation_precondition_evidence_path=(
                    args.activation_precondition_evidence
                ),
                runtime_source_binding=args.runtime_source_binding,
                manifest_output_path=args.manifest_output,
                evidence_output_path=args.evidence_output,
                ca_bundle=args.ca_bundle,
                openssl_bin=args.openssl,
            )
        return {
            "status": "finalized",
            "manifest_sha256": sha256_secure_file(
                args.manifest_output,
                label="WA-IR TLS activation manifest",
            )[0],
            "evidence_sha256": sha256_secure_file(
                args.evidence_output,
                label="WA-IR TLS activation evidence",
            )[0],
            "generation_id": manifest["generation_id"],
            "production_a_record_mutated": manifest["production_a_record_mutated"],
            "active_nginx_mutated": manifest["active_nginx_mutated"],
            "evidence_core_sha256": evidence["evidence_core_sha256"],
        }
    raise WebAppIrTlsError(f"unsupported command: {args.command}")


def _command_release_sha(args: argparse.Namespace) -> str:
    direct = getattr(args, "release_sha", None)
    if direct:
        return validate_release_sha(str(direct))
    receipt_path: Path
    if args.command == "stage-candidate":
        receipt_path = args.installation_receipt
    elif args.command == "probe-candidate":
        receipt_path = args.installation_receipt
    elif args.command == "finalize":
        receipt_path = args.installation_receipt
    else:
        raise WebAppIrTlsError("cannot derive release_sha for worker runtime binding")
    receipt = _read_json_secure(receipt_path, label="release-bound operation receipt")
    return validate_release_sha(str(receipt.get("release_sha", "")))


def _run_command(args: argparse.Namespace) -> dict[str, Any]:
    release_sha = _command_release_sha(args)
    source_binding = validate_exact_release_runtime(release_sha)
    args.runtime_source_binding = source_binding
    result = _run_command_unbound(args)
    if not isinstance(result, dict):
        raise WebAppIrTlsError("worker command returned an invalid result")
    return {**result, "runtime_source_binding": source_binding}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = _run_command(parse_args(argv))
    except (WebAppIrTlsError, SecureFileError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
