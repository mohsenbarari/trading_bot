#!/usr/bin/env python3
"""Execute one allowlisted production-shadow control request from Object Storage.

This is a target-host receiver, not a generic remote command runner.  The only
currently supported request type is the fenced WebApp-IR Witness-lease
readback.  A root-only policy binds the exact operation release, this receiver
source, one age identity, the controller age recipient and the fixed worker
source.  The request itself arrives only as one age-encrypted private/versioned
object.  Its short-lived URL is accepted only as a typed base64url SSH command
argument, immediately validated against its exact object version, and never
written to a file, result, log, or attestation.

The worker's existing stdio authority frames are relayed verbatim between the
controller and the local fixed worker.  That preserves controller liveness
without sending request/result payload bytes over SSH.  The final result is
written create-only locally, age-encrypted for the controller, uploaded through
one pre-signed create-only PUT URL, and represented only by redacted evidence.

Default CLI mode is a local plan.  Receiving/executing requires explicit
``--apply`` and a digest-bound confirmation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, BinaryIO, Iterator, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener
import ssl


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import production_shadow_startup_normalization_worker as CONTROL  # noqa: E402
from scripts import production_shadow_witness_lease_worker as LEASE  # noqa: E402
from scripts.receive_wa_ir_production_artifact import (  # noqa: E402
    ReceiveDescriptor,
    ProductionReceiveError,
    parse_descriptor,
    receive_one,
)
from scripts.production_shadow_object_storage_control import (  # noqa: E402
    MAX_CONTROL_REQUEST_BYTES,
    MAX_RESULT_CIPHERTEXT_BYTES,
    REQUEST_ARTIFACT_KIND,
    REQUEST_ENVELOPE_SCHEMA,
    RESULT_ARTIFACT_KIND,
    RESULT_METADATA_SCHEMA,
    ResultObject,
    ResultUploadGrant,
    ControlTransportError,
    decode_control_url_argument,
    request_destination_name,
    validate_request_envelope,
    validate_result_upload_grant,
    validate_result_upload_url,
)
from scripts.wa_ir_production_transport_contract import (  # noqa: E402
    AGE_EXECUTABLE,
    ARVAN_HOST,
    PRODUCTION_BUCKET,
    SHA256_RE,
    validate_operation_id,
    validate_prefix,
)


POLICY_SCHEMA = "production-shadow-object-storage-control-receiver-policy-v1"
RESULT_SCHEMA = "production-shadow-object-storage-control-result-v1"
ATTESTATION_SCHEMA = "production-shadow-object-storage-control-receive-attestation-v1"
PLAN_SCHEMA = "production-shadow-object-storage-control-receiver-plan-v1"
ERROR_SCHEMA = "production-shadow-object-storage-control-receiver-error-v1"
CIPHERTEXT_BINDING_SCHEMA = "production-shadow-object-storage-control-ciphertext-binding-v1"

POLICY_FIELDS = frozenset(
    {
        "schema",
        "role",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "release_root",
        "receiver_relative_path",
        "receiver_sha256",
        "operations_root",
        "age_identity_path",
        "controller_age_recipient",
        "object_storage_prefix",
        "allowed_request_types",
    }
)
POLICY_REQUEST_TYPE_FIELDS = frozenset(
    {
        "request_type",
        "worker_relative_path",
        "worker_sha256",
        "worker_request_schema",
        "worker_result_schema",
        "required_role",
        "required_action",
        "max_result_bytes",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "request_type",
        "request_sha256",
        "worker_request_sha256",
        "worker_result",
        "worker_result_sha256",
        "receiver_sha256",
        "result_sha256",
    }
)
ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "request_type",
        "request_sha256",
        "request_artifact",
        "local_request_installation",
        "local_result",
        "result_artifact",
        "presigned_url_persisted",
        "presigned_url_logged",
        "payload_bytes_over_ssh",
        "generic_shell_execution_used",
        "current_mutated",
        "service_mutated",
        "volume_mutated",
    }
)
CIPHERTEXT_BINDING_FIELDS = frozenset(
    {
        "schema",
        "request_sha256",
        "result_sha256",
        "result_bytes",
        "ciphertext_name",
        "ciphertext_sha256",
        "ciphertext_bytes",
    }
)
REQUEST_TYPE = "witness-lease-readback-v1"
SUPPORTED_TARGET_ROLES = frozenset({"webapp_ir"})
WORKER_RELATIVE_PATH = "scripts/production_shadow_witness_lease_worker.py"
WORKER_REQUEST_SCHEMA = LEASE.REQUEST_SCHEMA
WORKER_RESULT_SCHEMA = LEASE.RESULT_SCHEMA
RECEIVER_RELATIVE_PATH = "scripts/production_shadow_object_storage_control_receiver.py"

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}
SYSTEM_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
MAX_POLICY_BYTES = 256 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_AUTHORITY_FRAME_BYTES = 64 * 1024
PROCESS_TIMEOUT_SECONDS = 15 * 60
PROCESS_KILL_GRACE_SECONDS = 3.0
POLL_SECONDS = 0.05
AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{20,100}$")
ROLE_RE = re.compile(r"^(?:webapp_fi|webapp_ir|witness)$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ControlReceiverError(RuntimeError):
    """The receiver cannot prove its fixed control operation is safe."""


class _StrictObjectError(ValueError):
    pass


@dataclass(frozen=True)
class RequestTypePolicy:
    request_type: str
    worker_relative_path: str
    worker_sha256: str
    worker_request_schema: str
    worker_result_schema: str
    required_role: str
    required_action: str
    max_result_bytes: int


@dataclass(frozen=True)
class ReceiverPolicy:
    role: str
    campaign_id: str
    operation_id: str
    release_sha: str
    release_tree_sha: str
    release_root: Path
    receiver_relative_path: str
    receiver_sha256: str
    operations_root: Path
    age_identity_path: Path
    controller_age_recipient: str
    object_storage_prefix: str
    allowed_request_types: Mapping[str, RequestTypePolicy]


@dataclass(frozen=True)
class ReceivedRequest:
    descriptor: ReceiveDescriptor
    installation_result: str
    payload: bytes
    envelope: Mapping[str, Any]
    policy_request: RequestTypePolicy


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ControlReceiverError("receiver document is not canonical JSON") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise _StrictObjectError("duplicate JSON field")
        document[key] = value
    return document


def _absolute_path(value: Any, *, label: str) -> Path:
    path = Path(str(value))
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise ControlReceiverError(f"{label} must be an absolute normalized path")
    return path


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise ControlReceiverError(f"{label} SHA-256 is invalid")
    return value


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or UUID_RE.fullmatch(value) is None:
        raise ControlReceiverError(f"{label} must be a canonical UUID")
    try:
        canonical = validate_operation_id(value)
    except Exception as exc:
        raise ControlReceiverError(f"{label} must be a nonzero canonical UUID") from exc
    return canonical


def _require_root_only_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    try:
        payload = read_secure_bytes(path, label=label, owner_uid=0, max_size=max_bytes)
    except SecureFileError as exc:
        raise ControlReceiverError(f"{label} is unavailable or unsafe") from exc
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ControlReceiverError(f"{label} is unavailable or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ControlReceiverError(f"{label} is not a root-only 0600 file")
    return payload


def _require_private_directory(path: Path, *, label: str, create: bool = False) -> None:
    path = _absolute_path(path, label=label)
    if create and not path.exists():
        parent = path.parent
        _require_private_directory(parent, label=f"{label} parent", create=False)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ControlReceiverError(f"{label} cannot be created") from exc
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ControlReceiverError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ControlReceiverError(f"{label} is not root-only 0700")


def _hash_root_owned_file(path: Path, *, label: str, maximum: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not 1 <= metadata.st_size <= maximum
        ):
            raise ValueError
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    except (OSError, ValueError) as exc:
        raise ControlReceiverError(f"{label} source is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if (
        len(payload) != metadata.st_size
        or any(getattr(metadata, field) != getattr(after, field) for field in stable)
    ):
        raise ControlReceiverError(f"{label} source changed during hashing")
    return _sha256(payload)


def _verify_exact_release(policy: ReceiverPolicy) -> None:
    root = policy.release_root
    if root.name != policy.release_sha:
        raise ControlReceiverError("receiver release root does not name the exact release")
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", os.fspath(root), "rev-parse", "HEAD"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlReceiverError("receiver exact release identity is unavailable") from exc
    if result.returncode != 0 or result.stderr or result.stdout != f"{policy.release_sha}\n".encode("ascii"):
        raise ControlReceiverError("receiver exact release HEAD differs")
    try:
        status = subprocess.run(
            ["/usr/bin/git", "-C", os.fspath(root), "status", "--porcelain=v1", "--untracked-files=no"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlReceiverError("receiver exact release cleanliness is unavailable") from exc
    if status.returncode != 0 or status.stderr or status.stdout:
        raise ControlReceiverError("receiver exact release is not clean")


def _policy_request_type(value: Any, *, role: str) -> RequestTypePolicy:
    if not isinstance(value, Mapping) or set(value) != POLICY_REQUEST_TYPE_FIELDS:
        raise ControlReceiverError("receiver request-type policy fields are not exact")
    request_type = value.get("request_type")
    if request_type != REQUEST_TYPE:
        raise ControlReceiverError("receiver request type is not allowlisted")
    if (
        value.get("worker_relative_path") != WORKER_RELATIVE_PATH
        or value.get("worker_request_schema") != WORKER_REQUEST_SCHEMA
        or value.get("worker_result_schema") != WORKER_RESULT_SCHEMA
        or value.get("required_role") != role
        or value.get("required_action") != "readback"
    ):
        raise ControlReceiverError("receiver worker mapping differs")
    worker_sha256 = _nonzero_sha256(value.get("worker_sha256"), label="receiver worker")
    maximum = value.get("max_result_bytes")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 1 <= maximum <= MAX_CONTROL_REQUEST_BYTES
    ):
        raise ControlReceiverError("receiver result bound is invalid")
    return RequestTypePolicy(
        request_type=request_type,
        worker_relative_path=WORKER_RELATIVE_PATH,
        worker_sha256=worker_sha256,
        worker_request_schema=WORKER_REQUEST_SCHEMA,
        worker_result_schema=WORKER_RESULT_SCHEMA,
        required_role=role,
        required_action="readback",
        max_result_bytes=maximum,
    )


def load_policy(path: Path) -> ReceiverPolicy:
    """Load a root-only target policy and prove exact release source bindings."""

    if os.geteuid() != 0 or os.getegid() != 0:
        raise ControlReceiverError("control receiver must run as root:root")
    path = _absolute_path(path, label="receiver policy")
    payload = _require_root_only_file(path, label="receiver policy", max_bytes=MAX_POLICY_BYTES)
    try:
        value = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ControlReceiverError("receiver policy is not strict JSON") from exc
    if not isinstance(value, dict) or set(value) != POLICY_FIELDS or value.get("schema") != POLICY_SCHEMA:
        raise ControlReceiverError("receiver policy fields are not exact")
    role = value.get("role")
    if not isinstance(role, str) or ROLE_RE.fullmatch(role) is None:
        raise ControlReceiverError("receiver policy role is invalid")
    if role not in SUPPORTED_TARGET_ROLES:
        raise ControlReceiverError("receiver policy role has no allowlisted worker")
    campaign_id = _canonical_uuid(value.get("campaign_id"), label="campaign id")
    operation_id = _canonical_uuid(value.get("operation_id"), label="operation id")
    if campaign_id == operation_id:
        raise ControlReceiverError("receiver campaign and operation ids must differ")
    release_sha = value.get("release_sha")
    release_tree_sha = value.get("release_tree_sha")
    if (
        not isinstance(release_sha, str)
        or SHA40_RE.fullmatch(release_sha) is None
        or not isinstance(release_tree_sha, str)
        or SHA40_RE.fullmatch(release_tree_sha) is None
    ):
        raise ControlReceiverError("receiver release identity is invalid")
    release_root = _absolute_path(value.get("release_root"), label="receiver release root")
    receiver_relative_path = value.get("receiver_relative_path")
    if receiver_relative_path != RECEIVER_RELATIVE_PATH:
        raise ControlReceiverError("receiver script path is not allowlisted")
    receiver_sha256 = _nonzero_sha256(value.get("receiver_sha256"), label="receiver")
    operations_root = _absolute_path(value.get("operations_root"), label="receiver operations root")
    age_identity_path = _absolute_path(value.get("age_identity_path"), label="receiver age identity")
    recipient = value.get("controller_age_recipient")
    if not isinstance(recipient, str) or AGE_RECIPIENT_RE.fullmatch(recipient) is None:
        raise ControlReceiverError("controller age recipient is invalid")
    try:
        prefix = validate_prefix(str(value.get("object_storage_prefix")))
    except Exception as exc:
        raise ControlReceiverError("receiver Object Storage prefix is invalid") from exc
    raw_types = value.get("allowed_request_types")
    if not isinstance(raw_types, list) or len(raw_types) != 1:
        raise ControlReceiverError("receiver allowlist must contain exactly one request type")
    request_policy = _policy_request_type(raw_types[0], role=role)
    policy = ReceiverPolicy(
        role=role,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        release_root=release_root,
        receiver_relative_path=receiver_relative_path,
        receiver_sha256=receiver_sha256,
        operations_root=operations_root,
        age_identity_path=age_identity_path,
        controller_age_recipient=recipient,
        object_storage_prefix=prefix,
        allowed_request_types={request_policy.request_type: request_policy},
    )
    _require_private_directory(policy.operations_root, label="receiver operations root")
    _verify_exact_release(policy)
    receiver_path = policy.release_root / policy.receiver_relative_path
    if _hash_root_owned_file(receiver_path, label="receiver", maximum=MAX_CONTROL_REQUEST_BYTES) != policy.receiver_sha256:
        raise ControlReceiverError("receiver source digest differs from policy")
    if Path(__file__).resolve() != receiver_path.resolve():
        raise ControlReceiverError("receiver was not executed from its exact release path")
    worker = policy.release_root / request_policy.worker_relative_path
    if _hash_root_owned_file(worker, label="allowlisted worker", maximum=MAX_CONTROL_REQUEST_BYTES) != request_policy.worker_sha256:
        raise ControlReceiverError("allowlisted worker source digest differs")
    return policy


def receiver_confirmation(policy: ReceiverPolicy, *, request_sha256: str) -> str:
    return (
        "production-shadow-object-storage-control:"
        f"{policy.operation_id}:{policy.role}:{request_sha256}"
    )


def plan(policy: ReceiverPolicy) -> dict[str, Any]:
    if not isinstance(policy, ReceiverPolicy):
        raise ControlReceiverError("receiver policy is invalid")
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "role": policy.role,
        "request_types": sorted(policy.allowed_request_types),
        "request_transport": "private-versioned-age-object-storage",
        "result_transport": "private-versioned-age-object-storage",
        "payload_bytes_over_ssh": 0,
        "generic_shell_execution_used": False,
        "apply_requires": "exact request digest and live controller authority relay",
        "production_contacted": False,
    }


def _descriptor_from_arguments(args: argparse.Namespace) -> ReceiveDescriptor:
    try:
        request_url = decode_control_url_argument(
            args.request_url_b64,
            label="control request",
        )
    except ControlTransportError as exc:
        raise ControlReceiverError("control request URL encoding is invalid") from exc
    document = {
        "schema": "wa-ir-production-artifact-receive-v1",
        "operation_id": args.operation_id,
        "artifact_kind": REQUEST_ARTIFACT_KIND,
        "destination_name": args.request_destination_name,
        "bucket": PRODUCTION_BUCKET,
        "object_key": args.request_object_key,
        "version_id": args.request_version_id,
        "url": request_url,
        "ciphertext_sha256": args.request_ciphertext_sha256,
        "ciphertext_bytes": args.request_ciphertext_bytes,
        "plaintext_sha256": args.request_plaintext_sha256,
        "plaintext_bytes": args.request_plaintext_bytes,
    }
    try:
        return parse_descriptor(_canonical_json(document))
    except ProductionReceiveError as exc:
        raise ControlReceiverError("control request descriptor is invalid") from exc


def _result_url_from_arguments(
    args: argparse.Namespace,
    *,
    grant: ResultUploadGrant,
) -> str:
    """Decode and immediately prove the exact create-only PUT URL binding."""

    try:
        result_url = decode_control_url_argument(
            args.result_url_b64,
            label="control result",
        )
        validate_result_upload_url(result_url, grant=grant)
    except ControlTransportError as exc:
        raise ControlReceiverError("control result URL is invalid") from exc
    return result_url


def _request_path(policy: ReceiverPolicy, request_sha256: str) -> Path:
    return policy.operations_root / policy.operation_id / "incoming" / request_destination_name(request_sha256)


def _result_directory(policy: ReceiverPolicy) -> Path:
    return policy.operations_root / policy.operation_id / "control-results"


def _result_path(policy: ReceiverPolicy, request_sha256: str) -> Path:
    return _result_directory(policy) / f"{request_sha256}.json"


def _request_sha256_argument(value: Any) -> str:
    return _nonzero_sha256(value, label="control request")


def _ciphertext_path(result_path: Path, *, result_sha256: str) -> Path:
    result_sha256 = _nonzero_sha256(result_sha256, label="control result")
    return result_path.with_name(f"{result_path.stem}.{result_sha256}.age")


def _ciphertext_binding_path(result_path: Path, *, result_sha256: str) -> Path:
    result_sha256 = _nonzero_sha256(result_sha256, label="control result")
    return result_path.with_name(f"{result_path.stem}.{result_sha256}.ciphertext.json")


def _validate_received_request(
    policy: ReceiverPolicy,
    *,
    descriptor: ReceiveDescriptor,
    request_bytes: bytes,
) -> ReceivedRequest:
    if not request_bytes.endswith(b"\n") or len(request_bytes) > MAX_CONTROL_REQUEST_BYTES:
        raise ControlReceiverError("received control request is empty or oversized")
    try:
        value = json.loads(request_bytes[:-1].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ControlReceiverError("received control request is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ControlReceiverError("received control request is not an object")
    try:
        envelope = validate_request_envelope(value)
    except ControlTransportError as exc:
        raise ControlReceiverError("received control request envelope is invalid") from exc
    if _canonical_json(envelope) + b"\n" != request_bytes:
        raise ControlReceiverError("received control request is not canonical")
    if (
        envelope["campaign_id"] != policy.campaign_id
        or envelope["operation_id"] != policy.operation_id
        or envelope["release_sha"] != policy.release_sha
        or envelope["release_tree_sha"] != policy.release_tree_sha
        or envelope["role"] != policy.role
        or descriptor.operation_id != policy.operation_id
        or descriptor.artifact_kind != REQUEST_ARTIFACT_KIND
        or descriptor.destination_name != request_destination_name(envelope["request_sha256"])
        or not descriptor.object_key.startswith(
            f"{policy.object_storage_prefix}/{policy.operation_id}/{REQUEST_ARTIFACT_KIND}/"
        )
    ):
        raise ControlReceiverError("received control request binding differs from policy")
    request_policy = policy.allowed_request_types.get(envelope["request_type"])
    if request_policy is None:
        raise ControlReceiverError("received control request type is not allowlisted")
    worker_request = envelope["worker_request"]
    if (
        worker_request.get("schema") != request_policy.worker_request_schema
        or worker_request.get("role") != request_policy.required_role
        or worker_request.get("action") != request_policy.required_action
        or worker_request.get("worker_path")
        != os.fspath(policy.release_root / request_policy.worker_relative_path)
        or worker_request.get("worker_sha256") != request_policy.worker_sha256
    ):
        raise ControlReceiverError("allowlisted worker request binding differs")
    try:
        validated_worker = LEASE.validate_request(worker_request)
    except LEASE.WitnessLeaseWorkerError as exc:
        raise ControlReceiverError("allowlisted worker request is invalid") from exc
    if validated_worker != worker_request:
        raise ControlReceiverError("allowlisted worker request canonicalization differs")
    return ReceivedRequest(
        descriptor=descriptor,
        installation_result="pending",
        payload=request_bytes,
        envelope=envelope,
        policy_request=request_policy,
    )


def receive_request(
    policy: ReceiverPolicy,
    args: argparse.Namespace,
    *,
    expected_request_sha256: str,
) -> ReceivedRequest:
    """Directly pull/decrypt one exact request object into a create-only path."""

    descriptor = _descriptor_from_arguments(args)
    if (
        descriptor.operation_id != policy.operation_id
        or descriptor.artifact_kind != REQUEST_ARTIFACT_KIND
        or descriptor.destination_name != request_destination_name(expected_request_sha256)
        or not descriptor.object_key.startswith(
            f"{policy.object_storage_prefix}/{policy.operation_id}/{REQUEST_ARTIFACT_KIND}/"
        )
    ):
        raise ControlReceiverError("request descriptor operation differs from policy")
    try:
        installation_result = receive_one(
            descriptor,
            operations_root=policy.operations_root,
            identity_file=policy.age_identity_path,
            required_uid=0,
        )["installation_result"]
    except (ProductionReceiveError, KeyError) as exc:
        raise ControlReceiverError("control request Object Storage receive failed") from exc
    candidate = policy.operations_root / policy.operation_id / "incoming" / descriptor.destination_name
    try:
        request_bytes = _require_root_only_file(
            candidate,
            label="received control request",
            max_bytes=MAX_CONTROL_REQUEST_BYTES,
        )
    except ControlReceiverError:
        raise
    received = _validate_received_request(policy, descriptor=descriptor, request_bytes=request_bytes)
    if received.envelope["request_sha256"] != expected_request_sha256:
        raise ControlReceiverError("received control request digest differs from confirmed request")
    return ReceivedRequest(
        descriptor=received.descriptor,
        installation_result=installation_result,
        payload=received.payload,
        envelope=received.envelope,
        policy_request=received.policy_request,
    )


def _write_create_only_or_same(path: Path, payload: bytes, *, label: str) -> bool:
    _require_private_directory(path.parent, label=f"{label} directory", create=True)
    try:
        write_secure_new_bytes(path, payload, label=label, mode=0o600, max_size=MAX_CONTROL_REQUEST_BYTES)
        created = True
    except SecureFileError:
        try:
            observed = _require_root_only_file(path, label=label, max_bytes=MAX_CONTROL_REQUEST_BYTES)
        except ControlReceiverError as exc:
            raise ControlReceiverError(f"{label} could not be persisted safely") from exc
        if observed != payload:
            raise ControlReceiverError(f"existing {label} differs")
        created = False
    observed = _require_root_only_file(path, label=label, max_bytes=MAX_CONTROL_REQUEST_BYTES)
    if observed != payload:
        raise ControlReceiverError(f"{label} create-only readback differs")
    return created


def _require_result_within_policy(payload: bytes, policy: RequestTypePolicy) -> None:
    if (
        not isinstance(payload, bytes)
        or not 1 <= len(payload) <= policy.max_result_bytes
    ):
        raise ControlReceiverError("control result exceeds its policy-specific bound")


def _validate_authority_response(raw: bytes, *, request_sha256: str, expected: Mapping[str, Any]) -> bytes:
    if (
        not raw
        or len(raw) > MAX_AUTHORITY_FRAME_BYTES
        or not raw.endswith(b"\n")
    ):
        raise ControlReceiverError("controller authority response is missing or oversized")
    try:
        response = json.loads(raw[:-1].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ControlReceiverError("controller authority response is not strict JSON") from exc
    expected_response = {
        "schema": CONTROL.AUTHORITY_RESPONSE_SCHEMA,
        "status": "authorized",
        "sequence": expected["sequence"],
        "checkpoint": expected["checkpoint"],
        "challenge": expected["challenge"],
        "request_binding_sha256": request_sha256,
    }
    if response != expected_response:
        raise ControlReceiverError("controller authority response differs")
    return raw


@contextmanager
def _bounded_worker_process(argv: Sequence[str], *, initial_request: bytes) -> Iterator[subprocess.Popen[bytes]]:
    if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
        raise ControlReceiverError("allowlisted worker command is invalid")
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=SAFE_ENV,
        )
    except OSError as exc:
        raise ControlReceiverError("allowlisted worker could not start") from exc
    try:
        if process.stdin is None:
            raise ControlReceiverError("allowlisted worker stdin is unavailable")
        process.stdin.write(initial_request)
        process.stdin.flush()
        yield process
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _worker_argv(policy: ReceiverPolicy, received: ReceivedRequest) -> tuple[str, ...]:
    worker = policy.release_root / received.policy_request.worker_relative_path
    request = received.envelope["worker_request"]
    return (
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        "HOME=/nonexistent",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/bin/python3",
        "-I",
        "-B",
        os.fspath(worker),
        "--host-stdio",
        "--apply",
        "--confirm",
        LEASE.confirmation_phrase(request),
    )


def _read_line(stream: BinaryIO, buffer: bytearray, *, maximum: int) -> bytes | None:
    while True:
        newline = buffer.find(b"\n")
        if newline >= 0:
            raw = bytes(buffer[: newline + 1])
            del buffer[: newline + 1]
            return raw
        chunk = os.read(stream.fileno(), min(8192, maximum + 1))
        if not chunk:
            if buffer:
                raise ControlReceiverError("worker stdout ended with a partial frame")
            return None
        buffer.extend(chunk)
        if len(buffer) > maximum:
            raise ControlReceiverError("worker stdout frame exceeds its bound")


def _run_allowlisted_worker(
    policy: ReceiverPolicy,
    received: ReceivedRequest,
    *,
    controller_input: BinaryIO,
    controller_output: BinaryIO,
    now: float | None = None,
) -> Mapping[str, Any]:
    """Relay only standard authority frames; final result never crosses SSH."""

    request = received.envelope["worker_request"]
    request_payload = _canonical_json(request) + b"\n"
    argv = _worker_argv(policy, received)
    deadline = (time.monotonic() if now is None else now) + PROCESS_TIMEOUT_SECONDS
    result: Mapping[str, Any] | None = None
    stderr = bytearray()
    expected_authority: Mapping[str, Any] | None = None
    stdout_buffer = bytearray()
    with _bounded_worker_process(argv, initial_request=request_payload) as process:
        if process.stdout is None or process.stderr is None or process.stdin is None:
            raise ControlReceiverError("allowlisted worker streams are unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "worker-out")
        selector.register(process.stderr, selectors.EVENT_READ, "worker-err")
        controller_registered = False
        worker_stdout_registered = True
        try:
            while True:
                if time.monotonic() > deadline:
                    raise ControlReceiverError("allowlisted worker exceeded its deadline")
                if result is not None and process.poll() is not None:
                    break
                events = selector.select(POLL_SECONDS)
                if (
                    not events
                    and process.poll() is not None
                    and result is None
                    and worker_stdout_registered
                ):
                    # Drain any final buffered output before declaring failure.
                    events = [(type("K", (), {"fileobj": process.stdout, "data": "worker-out"})(), selectors.EVENT_READ)]
                elif not events and process.poll() is not None and result is None:
                    raise ControlReceiverError("allowlisted worker ended without a final response")
                for key, _mask in events:
                    if key.data == "worker-err":
                        chunk = os.read(process.stderr.fileno(), MAX_STDERR_BYTES + 1)
                        if chunk:
                            stderr.extend(chunk)
                            if len(stderr) > MAX_STDERR_BYTES:
                                raise ControlReceiverError("allowlisted worker stderr exceeds its bound")
                        else:
                            selector.unregister(process.stderr)
                    elif key.data == "controller-in":
                        if expected_authority is None:
                            # Any unsolicited byte is an authorization failure, not a data channel.
                            raw = os.read(controller_input.fileno(), 1)
                            if raw:
                                raise ControlReceiverError("controller sent unsolicited control data")
                            raise ControlReceiverError("controller control channel reached EOF")
                        raw = controller_input.readline(MAX_AUTHORITY_FRAME_BYTES + 1)
                        raw = _validate_authority_response(
                            raw,
                            request_sha256=request["request_sha256"],
                            expected=expected_authority,
                        )
                        process.stdin.write(raw)
                        process.stdin.flush()
                        expected_authority = None
                        selector.unregister(controller_input)
                        controller_registered = False
                    else:
                        raw = _read_line(process.stdout, stdout_buffer, maximum=MAX_AUTHORITY_FRAME_BYTES)
                        if raw is None:
                            selector.unregister(process.stdout)
                            worker_stdout_registered = False
                            continue
                        try:
                            document = json.loads(raw[:-1].decode("ascii"), object_pairs_hook=_strict_object)
                        except (UnicodeError, ValueError, json.JSONDecodeError, _StrictObjectError) as exc:
                            raise ControlReceiverError("allowlisted worker returned invalid JSON") from exc
                        if not isinstance(document, Mapping):
                            raise ControlReceiverError("allowlisted worker returned non-object JSON")
                        if document.get("schema") == CONTROL.AUTHORITY_REQUEST_SCHEMA:
                            if result is not None or expected_authority is not None:
                                raise ControlReceiverError("allowlisted worker authority sequence is invalid")
                            expected = {
                                "schema": CONTROL.AUTHORITY_REQUEST_SCHEMA,
                                "sequence": document.get("sequence"),
                                "checkpoint": document.get("checkpoint"),
                                "challenge": document.get("challenge"),
                                "request_binding_sha256": request["request_sha256"],
                            }
                            if document != expected:
                                raise ControlReceiverError("allowlisted worker authority request differs")
                            controller_output.write(raw)
                            controller_output.flush()
                            expected_authority = expected
                            selector.register(controller_input, selectors.EVENT_READ, "controller-in")
                            controller_registered = True
                        elif document.get("schema") == LEASE.FINAL_SCHEMA:
                            if result is not None or expected_authority is not None:
                                raise ControlReceiverError("allowlisted worker final response ordering differs")
                            if set(document) != {"schema", "result"} or not isinstance(document["result"], Mapping):
                                raise ControlReceiverError("allowlisted worker final response is invalid")
                            try:
                                result = LEASE.validate_result(document["result"], request=request)
                            except LEASE.WitnessLeaseWorkerError as exc:
                                raise ControlReceiverError("allowlisted worker result failed validation") from exc
                        elif document.get("schema") == LEASE.ERROR_SCHEMA:
                            raise ControlReceiverError("allowlisted worker failed closed")
                        else:
                            raise ControlReceiverError("allowlisted worker emitted an unexpected frame")
            if expected_authority is not None:
                raise ControlReceiverError("allowlisted worker ended during authority relay")
            if controller_registered:
                raise ControlReceiverError("controller authority relay registration differs")
            if process.returncode != 0 or stderr:
                raise ControlReceiverError("allowlisted worker exited unsuccessfully")
        finally:
            selector.close()
    if result is None:
        raise ControlReceiverError("allowlisted worker did not publish a result")
    return result


def _result_document(
    policy: ReceiverPolicy,
    received: ReceivedRequest,
    worker_result: Mapping[str, Any],
) -> dict[str, Any]:
    worker_bytes = _canonical_json(dict(worker_result))
    document: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": "verified",
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "release_tree_sha": policy.release_tree_sha,
        "role": policy.role,
        "request_type": received.envelope["request_type"],
        "request_sha256": received.envelope["request_sha256"],
        "worker_request_sha256": received.envelope["worker_request_sha256"],
        "worker_result": dict(worker_result),
        "worker_result_sha256": _sha256(worker_bytes),
        "receiver_sha256": policy.receiver_sha256,
        "result_sha256": "0" * 64,
    }
    document["result_sha256"] = _sha256(
        _canonical_json({key: item for key, item in document.items() if key != "result_sha256"})
    )
    if set(document) != RESULT_FIELDS:
        raise ControlReceiverError("control result fields are not exact")
    return document


def validate_result_document(
    value: Mapping[str, Any],
    *,
    policy: ReceiverPolicy,
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != RESULT_FIELDS:
        raise ControlReceiverError("control result fields are not exact")
    try:
        document = json.loads(_canonical_json(dict(value)).decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ControlReceiverError("control result is not strict JSON") from exc
    expected = {
        "schema": RESULT_SCHEMA,
        "status": "verified",
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "release_tree_sha": policy.release_tree_sha,
        "role": policy.role,
        "request_type": envelope["request_type"],
        "request_sha256": envelope["request_sha256"],
        "worker_request_sha256": envelope["worker_request_sha256"],
        "receiver_sha256": policy.receiver_sha256,
    }
    if any(document.get(field) != item for field, item in expected.items()):
        raise ControlReceiverError("control result binding differs")
    worker_result = document["worker_result"]
    if not isinstance(worker_result, dict) or document["worker_result_sha256"] != _sha256(_canonical_json(worker_result)):
        raise ControlReceiverError("control result worker payload differs")
    try:
        if LEASE.validate_result(worker_result, request=envelope["worker_request"]) != worker_result:
            raise ValueError
    except (LEASE.WitnessLeaseWorkerError, ValueError) as exc:
        raise ControlReceiverError("control result worker validation differs") from exc
    digest = _sha256(_canonical_json({key: item for key, item in document.items() if key != "result_sha256"}))
    if document["result_sha256"] != digest:
        raise ControlReceiverError("control result digest differs")
    return document


def _ciphertext_binding_document(
    *,
    request_sha256: str,
    result_sha256: str,
    result_bytes: int,
    ciphertext: Path,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> dict[str, Any]:
    document = {
        "schema": CIPHERTEXT_BINDING_SCHEMA,
        "request_sha256": _nonzero_sha256(request_sha256, label="control request"),
        "result_sha256": _nonzero_sha256(result_sha256, label="control result"),
        "result_bytes": result_bytes,
        "ciphertext_name": ciphertext.name,
        "ciphertext_sha256": _nonzero_sha256(
            ciphertext_sha256, label="control result ciphertext"
        ),
        "ciphertext_bytes": ciphertext_bytes,
    }
    if (
        set(document) != CIPHERTEXT_BINDING_FIELDS
        or not 1 <= result_bytes <= MAX_CONTROL_REQUEST_BYTES
        or not 1 <= ciphertext_bytes <= MAX_RESULT_CIPHERTEXT_BYTES
    ):
        raise ControlReceiverError("encrypted control result binding is invalid")
    return document


def _validate_ciphertext_binding(
    path: Path,
    *,
    request_sha256: str,
    result_sha256: str,
    result_bytes: int,
    ciphertext: Path,
) -> tuple[str, int]:
    payload = _require_root_only_file(
        path,
        label="encrypted control result binding",
        max_bytes=MAX_POLICY_BYTES,
    )
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ControlReceiverError("encrypted control result binding is invalid") from exc
    if not isinstance(document, dict) or set(document) != CIPHERTEXT_BINDING_FIELDS:
        raise ControlReceiverError("encrypted control result binding fields differ")
    expected_prefix = {
        "schema": CIPHERTEXT_BINDING_SCHEMA,
        "request_sha256": request_sha256,
        "result_sha256": result_sha256,
        "result_bytes": result_bytes,
        "ciphertext_name": ciphertext.name,
    }
    if any(document.get(key) != value for key, value in expected_prefix.items()):
        raise ControlReceiverError("encrypted control result binding differs")
    ciphertext_sha256 = _nonzero_sha256(
        document.get("ciphertext_sha256"), label="control result ciphertext"
    )
    ciphertext_bytes = document.get("ciphertext_bytes")
    if (
        isinstance(ciphertext_bytes, bool)
        or not isinstance(ciphertext_bytes, int)
        or not 1 <= ciphertext_bytes <= MAX_RESULT_CIPHERTEXT_BYTES
    ):
        raise ControlReceiverError("encrypted control result binding size differs")
    observed = _require_root_only_file(
        ciphertext,
        label="encrypted control result",
        max_bytes=MAX_RESULT_CIPHERTEXT_BYTES,
    )
    if (
        len(observed) != ciphertext_bytes
        or _sha256(observed) != ciphertext_sha256
    ):
        raise ControlReceiverError("encrypted control result binding readback differs")
    return ciphertext_sha256, ciphertext_bytes


def _seal_root_only_file(path: Path, *, label: str, maximum: int) -> bytes:
    descriptor = -1
    directory_descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= maximum
        ):
            raise ControlReceiverError(f"{label} is unavailable or unsafe")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise ControlReceiverError(f"{label} could not be sealed root-only") from exc
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
    return _require_root_only_file(path, label=label, max_bytes=maximum)


def _encrypt_result(
    result_path: Path,
    *,
    request_sha256: str,
    recipient: str,
) -> tuple[Path, str, int]:
    if not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise ControlReceiverError("controller age recipient is invalid")
    result_payload = _require_root_only_file(
        result_path,
        label="control result",
        max_bytes=MAX_CONTROL_REQUEST_BYTES,
    )
    result_sha256 = _sha256(result_payload)
    ciphertext = _ciphertext_path(result_path, result_sha256=result_sha256)
    binding_path = _ciphertext_binding_path(result_path, result_sha256=result_sha256)
    ciphertext_exists = ciphertext.exists() or ciphertext.is_symlink()
    binding_exists = binding_path.exists() or binding_path.is_symlink()
    if ciphertext_exists != binding_exists:
        raise ControlReceiverError("encrypted control result has incomplete create-only state")
    if ciphertext_exists:
        digest, size = _validate_ciphertext_binding(
            binding_path,
            request_sha256=request_sha256,
            result_sha256=result_sha256,
            result_bytes=len(result_payload),
            ciphertext=ciphertext,
        )
        return ciphertext, digest, size
    try:
        metadata = AGE_EXECUTABLE.stat(follow_symlinks=False)
    except OSError as exc:
        raise ControlReceiverError("age executable is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ControlReceiverError("age executable is unsafe")
    try:
        completed = subprocess.run(
            [
                os.fspath(AGE_EXECUTABLE),
                "--encrypt",
                "--recipient",
                recipient,
                "--output",
                os.fspath(ciphertext),
                os.fspath(result_path),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlReceiverError("control result age encryption failed") from exc
    if completed.returncode != 0 or completed.stderr:
        raise ControlReceiverError("control result age encryption failed closed")
    payload = _seal_root_only_file(
        ciphertext,
        label="encrypted control result",
        maximum=MAX_RESULT_CIPHERTEXT_BYTES,
    )
    binding = _ciphertext_binding_document(
        request_sha256=request_sha256,
        result_sha256=result_sha256,
        result_bytes=len(result_payload),
        ciphertext=ciphertext,
        ciphertext_sha256=_sha256(payload),
        ciphertext_bytes=len(payload),
    )
    try:
        write_secure_new_bytes(
            binding_path,
            _canonical_json(binding) + b"\n",
            label="encrypted control result binding",
            mode=0o600,
            max_size=MAX_POLICY_BYTES,
        )
    except SecureFileError as exc:
        raise ControlReceiverError("encrypted control result binding could not be persisted") from exc
    digest, size = _validate_ciphertext_binding(
        binding_path,
        request_sha256=request_sha256,
        result_sha256=result_sha256,
        result_bytes=len(result_payload),
        ciphertext=ciphertext,
    )
    return ciphertext, digest, size


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, ARG002
        return None


def _upload_result(
    ciphertext: Path,
    *,
    result_url: str,
    grant: ResultUploadGrant,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> ResultObject:
    """PUT one ciphertext; require the provider to return one VersionId."""

    try:
        validate_result_upload_url(result_url, grant=grant)
    except ControlTransportError as exc:
        raise ControlReceiverError("control result URL is invalid") from exc

    try:
        ca = SYSTEM_CA_BUNDLE.stat(follow_symlinks=False)
    except OSError as exc:
        raise ControlReceiverError("system TLS trust store is unavailable") from exc
    if not stat.S_ISREG(ca.st_mode) or ca.st_uid != 0 or stat.S_IMODE(ca.st_mode) & 0o022:
        raise ControlReceiverError("system TLS trust store is unsafe")
    try:
        parsed = urlsplit(result_url)
    except (TypeError, ValueError) as exc:
        raise ControlReceiverError("control result URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARVAN_HOST
        or parsed.path != f"/{grant.bucket}/{grant.object_key}"
    ):
        raise ControlReceiverError("control result URL is outside its exact object")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cafile=os.fspath(SYSTEM_CA_BUNDLE))
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
        payload = _require_root_only_file(ciphertext, label="encrypted control result", max_bytes=MAX_RESULT_CIPHERTEXT_BYTES)
        request = Request(
            result_url,
            data=payload,
            method="PUT",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(payload)),
                "If-None-Match": "*",
                **{f"x-amz-meta-{key}": value for key, value in grant.metadata().items()},
            },
        )
        response = opener.open(request, timeout=120)
    except (HTTPError, URLError, OSError, ssl.SSLError, ValueError, ControlReceiverError) as exc:
        if isinstance(exc, ControlReceiverError):
            raise
        raise ControlReceiverError("control result upload failed closed") from exc
    try:
        status = getattr(response, "status", response.getcode())
        final_url = response.geturl()
        version_id = str(response.headers.get("x-amz-version-id") or "")
    finally:
        response.close()
    if (
        status not in {200, 201}
        or final_url != result_url
        or not version_id
        or version_id != version_id.strip()
        or len(version_id) > 1024
    ):
        raise ControlReceiverError("control result upload lacks an exact VersionId")
    return ResultObject(
        bucket=grant.bucket,
        object_key=grant.object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        metadata=grant.metadata(),
    )


def _attestation(
    policy: ReceiverPolicy,
    received: ReceivedRequest,
    *,
    result_path: Path,
    result_document: Mapping[str, Any],
    result_object: ResultObject,
) -> dict[str, Any]:
    result_payload = _canonical_json(result_document) + b"\n"
    document = {
        "schema": ATTESTATION_SCHEMA,
        "status": "uploaded",
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "role": policy.role,
        "request_type": received.envelope["request_type"],
        "request_sha256": received.envelope["request_sha256"],
        "request_artifact": {
            "bucket": received.descriptor.bucket,
            "object_key": received.descriptor.object_key,
            "version_id": received.descriptor.version_id,
            "ciphertext_sha256": received.descriptor.ciphertext_sha256,
            "ciphertext_bytes": received.descriptor.ciphertext_bytes,
            "plaintext_sha256": received.descriptor.plaintext_sha256,
            "plaintext_bytes": received.descriptor.plaintext_bytes,
        },
        "local_request_installation": received.installation_result,
        "local_result": {
            "relative_path": os.fspath(result_path.relative_to(policy.operations_root)),
            "sha256": _sha256(result_payload),
            "bytes": len(result_payload),
            "mode": "0600",
        },
        "result_artifact": result_object.evidence(),
        "presigned_url_persisted": False,
        "presigned_url_logged": False,
        "payload_bytes_over_ssh": 0,
        "generic_shell_execution_used": False,
        "current_mutated": False,
        "service_mutated": False,
        "volume_mutated": False,
    }
    if set(document) != ATTESTATION_FIELDS:
        raise ControlReceiverError("control receive attestation fields are not exact")
    return document


def apply(
    policy: ReceiverPolicy,
    args: argparse.Namespace,
    *,
    controller_input: BinaryIO | None = None,
    controller_output: BinaryIO | None = None,
) -> dict[str, Any]:
    """Receive, execute, and return redacted evidence for one fixed request type."""

    expected_request_sha256 = _request_sha256_argument(args.request_sha256)
    if args.confirm != receiver_confirmation(policy, request_sha256=expected_request_sha256):
        raise ControlReceiverError("receiver apply confirmation differs")
    try:
        grant = validate_result_upload_grant(
            {
                "schema": args.result_grant_schema,
                "bucket": PRODUCTION_BUCKET,
                "object_key": args.result_object_key,
                "upload_id": args.result_upload_id,
                "operation_id": policy.operation_id,
                "role": policy.role,
                "request_sha256": expected_request_sha256,
                "ttl_seconds": args.result_ttl_seconds,
            },
            prefix=policy.object_storage_prefix,
        )
    except ControlTransportError as exc:
        raise ControlReceiverError("control result grant is invalid") from exc
    result_url = _result_url_from_arguments(args, grant=grant)
    received = receive_request(
        policy,
        args,
        expected_request_sha256=expected_request_sha256,
    )
    worker_result = _run_allowlisted_worker(
        policy,
        received,
        controller_input=sys.stdin.buffer if controller_input is None else controller_input,
        controller_output=sys.stdout.buffer if controller_output is None else controller_output,
    )
    result_document = _result_document(policy, received, worker_result)
    validate_result_document(result_document, policy=policy, envelope=received.envelope)
    result_payload = _canonical_json(result_document) + b"\n"
    _require_result_within_policy(result_payload, received.policy_request)
    result_path = _result_path(policy, received.envelope["request_sha256"])
    _write_create_only_or_same(result_path, result_payload, label="control result")
    ciphertext, ciphertext_sha256, ciphertext_bytes = _encrypt_result(
        result_path,
        request_sha256=received.envelope["request_sha256"],
        recipient=policy.controller_age_recipient,
    )
    result_object = _upload_result(
        ciphertext,
        result_url=result_url,
        grant=grant,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
    )
    return _attestation(
        policy,
        received,
        result_path=result_path,
        result_document=result_document,
        result_object=result_object,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--request-sha256")
    parser.add_argument("--request-url-b64")
    parser.add_argument("--operation-id")
    parser.add_argument("--request-object-key")
    parser.add_argument("--request-version-id")
    parser.add_argument("--request-destination-name")
    parser.add_argument("--request-ciphertext-sha256")
    parser.add_argument("--request-ciphertext-bytes", type=int)
    parser.add_argument("--request-plaintext-sha256")
    parser.add_argument("--request-plaintext-bytes", type=int)
    parser.add_argument("--result-url-b64")
    parser.add_argument("--result-grant-schema")
    parser.add_argument("--result-object-key")
    parser.add_argument("--result-upload-id")
    parser.add_argument("--result-ttl-seconds", type=int)
    return parser


def _require_apply_arguments(args: argparse.Namespace) -> None:
    required = (
        "confirm",
        "request_sha256",
        "request_url_b64",
        "operation_id",
        "request_object_key",
        "request_version_id",
        "request_destination_name",
        "request_ciphertext_sha256",
        "request_ciphertext_bytes",
        "request_plaintext_sha256",
        "request_plaintext_bytes",
        "result_url_b64",
        "result_grant_schema",
        "result_object_key",
        "result_upload_id",
        "result_ttl_seconds",
    )
    if not args.apply:
        if any(getattr(args, field) is not None for field in required):
            raise ControlReceiverError("plan mode does not accept Object Storage control arguments")
        return
    if any(getattr(args, field) is None for field in required):
        raise ControlReceiverError("apply requires every exact Object Storage control binding")
    if args.result_grant_schema != "production-shadow-object-storage-control-result-upload-v1":
        raise ControlReceiverError("result grant schema differs")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_apply_arguments(args)
        policy = load_policy(args.policy)
        if not args.apply:
            payload: Mapping[str, Any] = plan(policy)
        else:
            payload = apply(policy, args)
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except (ControlReceiverError, ControlTransportError) as exc:
        print(
            json.dumps(
                {
                    "schema": ERROR_SCHEMA,
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "payload_bytes_over_ssh": 0,
                    "generic_shell_execution_used": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
