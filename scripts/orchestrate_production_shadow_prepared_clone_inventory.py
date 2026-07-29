#!/usr/bin/env python3
"""Collect one fresh, exact three-role prepared-clone Docker inventory.

Planning never contacts a host.  Apply generates controller-owned entropy,
requires a live external EOF pipe, invokes the immutable host agent through a
persistent-pidfd bounded runner, and returns a self-verifying aggregate.  The
host response contains only identifiers, counts, and digests.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import threading
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import SecureFileError, read_secure_bytes  # noqa: E402
from scripts import orchestrate_production_shadow_finland_artifacts as FINLAND  # noqa: E402
from scripts import orchestrate_production_shadow_restore_phase as RUNNER  # noqa: E402
from scripts import produce_production_shadow_prepare_material as PREPARE  # noqa: E402
from scripts import production_shadow_convergence_runtime_targets as runtime_targets  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import production_shadow_global_docker_inventory_agent as INVENTORY  # noqa: E402
from scripts import seal_production_shadow_release_artifacts as RELEASE  # noqa: E402


ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
AGGREGATE_SCHEMA = (
    "production-shadow-prepared-clone-inventory-three-role-aggregate-v1"
)
PRE_FREEZE_CURRENT_OPERATION_RECEIPT_SCHEMA = AGGREGATE_SCHEMA
PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME = (
    "pre-freeze-current-operation-receipt.json"
)
PLAN_SCHEMA = "production-shadow-prepared-clone-inventory-plan-v1"
RESULT_SCHEMA = "production-shadow-prepared-clone-inventory-result-v1"
CONTROLLER_PLAN_SCHEMA = (
    "production-shadow-prepared-clone-inventory-controller-plan-v1"
)
PUBLICATION_SCHEMA = (
    "production-shadow-prepared-clone-inventory-publication-v1"
)
LOADED_RECEIPT_SCHEMA = (
    "production-shadow-prepared-clone-inventory-loaded-receipt-v1"
)
HISTORICAL_BASELINE_LOADED_RECEIPT_SCHEMA = (
    "production-shadow-prepared-clone-inventory-"
    "loaded-historical-running-baseline-v1"
)
REQUEST_FILENAMES = {
    role: f"{role}.request.json" for role in ROLES
}
RESPONSE_FILENAMES = {
    role: f"{role}.response.json" for role in ROLES
}
ROLE_AGGREGATE_FIELDS = frozenset(
    {
        "role",
        "expected_host",
        "contract_kind",
        "request_sha256",
        "request_bytes",
        "response_sha256",
        "response_document_sha256",
        "response_bytes",
        "request_binding_sha256",
        "command_started_at",
        "command_completed_at",
        "captured_at",
        "prepared_container_id",
        "prepared_network_id",
        "prepared_redis_identity_sha256",
        "prepared_redis_chain_metadata_sha256",
        "prepared_redis_metadata_sha256",
        "prepared_redis_target_count",
        "prepared_redis_unsafe_path_count",
        "prepared_redis_entry_count",
        "prepared_redis_pristine",
        "inventory_root_sha256",
        "non_operation_inventory_root_sha256",
        "operation_resource_root_sha256",
    }
)
AGGREGATE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "expected_database_state",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "captured_at",
        "controller_observed_at",
        "role_captured_at",
        "role_capture_skew_seconds",
        "request_lifetime_seconds",
        "roles",
        "collection_performed",
        "production_contacted",
        "docker_read_only",
        "application_payload_bytes_over_ssh",
        "aggregate_sha256",
    }
)
MAX_ROLE_CAPTURE_SKEW_SECONDS = 30.0
COMMAND_CLOCK_SKEW_SECONDS = 5.0
REQUEST_LIFETIME_SECONDS = 90.0
MAX_STDERR_BYTES = 128 * 1024
CONTROL_TIMEOUT_SECONDS = 300.0
PROCESS_TERM_GRACE_SECONDS = 1.0
ZERO_SHA256 = "0" * 64
REQUIRED_CONFIRMATION_PREFIX = "collect-prepared-clone-inventory"
CONTROLLER_CONFIRMATION_PREFIX = (
    "execute-production-shadow-prepared-clone-inventory"
)
MAX_SSH_TRUST_BYTES = 256 * 1024
MAX_CONTROLLER_JSON_BYTES = 16 * 1024 * 1024
MAX_RELEASE_ARTIFACT_BYTES = 16 * 1024 * 1024
PREPARE_METADATA_FIELDS = frozenset(
    {
        "schema",
        "capabilities",
        "operation_id",
        "release_sha",
        "canonical_compose_sha256",
        "dr_ca_sha256",
        "dr_tls_attestation_sha256",
        "dr_tls_attested_at_epoch",
        "roles",
        "controller_bindings",
        "activation_secrets_included",
        "precommit_manifest_bound",
    }
)
PREPARE_ROLE_FIELDS = frozenset(
    {
        "filename",
        "sha256",
        "bytes",
        "format",
        "transport",
        "internal_manifest_sha256",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
    }
)
RELEASE_ARTIFACT_RELATIVE_PATHS = {
    "inventory_agent": INVENTORY.AGENT_RELATIVE,
    "finland_precommit_worker": INVENTORY.PREPARED_WORKER_RELATIVES[
        "finland-precommit"
    ],
    "wa_ir_operation_worker": INVENTORY.PREPARED_WORKER_RELATIVES[
        "wa-ir-operation"
    ],
}


class PreparedCloneInventoryError(RuntimeError):
    """A redacted, fail-closed collection error."""


class PreparedCloneInventoryCancellation(BaseException):
    """Raised when controller liveness or a termination signal is lost."""


@dataclass(frozen=True)
class RoleBinding:
    contract_worker_sha256: str
    role_manifest_sha256: str


@dataclass(frozen=True)
class CollectionInputs:
    campaign_id: str
    operation_id: str
    release_sha: str
    release_tree_sha: str
    agent_sha256: str
    roles: Mapping[str, RoleBinding]
    expected_database_state: str = "running-healthy"
    prior_requests: Mapping[str, Mapping[str, Any]] | None = None
    prior_responses: Mapping[str, Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class ControllerContext:
    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    release_closure_path: Path
    prepare_metadata_path: Path
    ssh_identity: Path
    known_hosts: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    cutover_plan_sha256: str
    approval_sha256: str
    approval_policy_sha256: str
    release_closure_sha256: str
    prepare_metadata_sha256: str
    release_artifact_sha256: Mapping[str, str]
    release_artifact_git_blob: Mapping[str, str]
    ssh_identity_sha256: str
    known_hosts_sha256: str
    output_root: Path
    collection_inputs: CollectionInputs
    prior_running_receipt: Mapping[str, str] | None = None


@dataclass(frozen=True)
class InvocationResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    stdout_limit_exceeded: bool = False
    stderr_limit_exceeded: bool = False
    process_group_cleanup_performed: bool = True


class InventoryInvoker(Protocol):
    def __call__(
        self,
        role: str,
        request: Mapping[str, Any],
    ) -> InvocationResult:
        """Invoke one immutable host agent and return bounded raw bytes."""


class AuthorizationCheck(Protocol):
    def __call__(self) -> None:
        """Fail unless the operation authorization is fresh right now."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or INVENTORY.SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise PreparedCloneInventoryError(f"{label} is invalid")
    return value


def _timestamp(value: datetime) -> str:
    try:
        return INVENTORY.canonical_utc_timestamp(value)
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(
            "collection timestamp is invalid"
        ) from exc


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    try:
        return INVENTORY._parse_utc_timestamp(value, label=label)  # noqa: SLF001
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(f"{label} is invalid") from exc


def _utc_datetime(value: Any, *, label: str) -> datetime:
    try:
        serialized = INVENTORY.canonical_utc_timestamp(value)
        return INVENTORY._parse_utc_timestamp(  # noqa: SLF001
            serialized,
            label=label,
        )
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(f"{label} is invalid") from exc


def _validate_inputs(value: CollectionInputs) -> CollectionInputs:
    if not isinstance(value, CollectionInputs):
        raise PreparedCloneInventoryError("collection inputs are invalid")
    if (
        not isinstance(value.roles, Mapping)
        or set(value.roles) != set(ROLES)
        or value.expected_database_state not in {"running-healthy", "stopped"}
    ):
        raise PreparedCloneInventoryError(
            "collection role or state inputs differ"
        )
    try:
        campaign_id = INVENTORY._canonical_uuid4(  # noqa: SLF001
            value.campaign_id,
            label="campaign ID",
        )
        operation_id = INVENTORY._canonical_uuid4(  # noqa: SLF001
            value.operation_id,
            label="operation ID",
        )
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(
            "collection campaign or operation identity is invalid"
        ) from exc
    if (
        campaign_id == operation_id
        or not isinstance(value.release_sha, str)
        or INVENTORY.SHA40_RE.fullmatch(value.release_sha) is None
        or not isinstance(value.release_tree_sha, str)
        or INVENTORY.SHA40_RE.fullmatch(value.release_tree_sha) is None
    ):
        raise PreparedCloneInventoryError(
            "collection release identity is invalid"
        )
    for role in ROLES:
        binding = value.roles[role]
        if not isinstance(binding, RoleBinding):
            raise PreparedCloneInventoryError(
                f"{role} collection binding is invalid"
            )
        _nonzero_sha256(
            binding.contract_worker_sha256,
            label=f"{role} contract worker",
        )
        _nonzero_sha256(
            binding.role_manifest_sha256,
            label=f"{role} role manifest",
        )
    _nonzero_sha256(value.agent_sha256, label="inventory agent")
    if value.expected_database_state == "running-healthy":
        if value.prior_requests is not None or value.prior_responses is not None:
            raise PreparedCloneInventoryError(
                "running collection unexpectedly has stopped-state baselines"
            )
    elif (
        value.prior_requests is None
        or value.prior_responses is None
        or not isinstance(value.prior_requests, Mapping)
        or not isinstance(value.prior_responses, Mapping)
        or set(value.prior_requests) != set(ROLES)
        or set(value.prior_responses) != set(ROLES)
    ):
        raise PreparedCloneInventoryError(
            "stopped collection lacks exact three-role baselines"
        )
    return value


def _request_matches_inputs(
    inputs: CollectionInputs,
    role: str,
    request: Mapping[str, Any],
) -> bool:
    binding = inputs.roles[role]
    return (
        request["role"] == role
        and request["campaign_id"] == inputs.campaign_id
        and request["operation_id"] == inputs.operation_id
        and request["release_sha"] == inputs.release_sha
        and request["release_tree_sha"] == inputs.release_tree_sha
        and request["expected_database_state"]
        == inputs.expected_database_state
        and request["agent_sha256"] == inputs.agent_sha256
        and request["contract_worker_sha256"]
        == binding.contract_worker_sha256
        and request["role_manifest_sha256"]
        == binding.role_manifest_sha256
    )


def build_plan(inputs: CollectionInputs) -> dict[str, Any]:
    inputs = _validate_inputs(inputs)
    body: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "campaign_id": inputs.campaign_id,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "expected_database_state": inputs.expected_database_state,
        "agent_sha256": inputs.agent_sha256,
        "roles": {
            role: {
                "expected_host": INVENTORY.ROLE_HOSTS[role],
                "contract_kind": (
                    "wa-ir-operation"
                    if role == "webapp_ir"
                    else "finland-precommit"
                ),
                "contract_worker_sha256": inputs.roles[
                    role
                ].contract_worker_sha256,
                "role_manifest_sha256": inputs.roles[
                    role
                ].role_manifest_sha256,
            }
            for role in ROLES
        },
        "controller_challenge_generated_only_at_apply": True,
        "request_lifetime_seconds": REQUEST_LIFETIME_SECONDS,
        "authorization_required": True,
        "collection_performed": False,
        "production_contacted": False,
    }
    plan_sha256 = _sha256(canonical_json(body))
    body["plan_sha256"] = plan_sha256
    body["required_confirmation"] = (
        f"{REQUIRED_CONFIRMATION_PREFIX}:"
        f"{inputs.operation_id}:{inputs.expected_database_state}:"
        f"{plan_sha256}"
    )
    return body


def _verify_authorization(
    inputs: CollectionInputs,
    check: AuthorizationCheck | None,
) -> None:
    del inputs
    if check is None:
        raise PreparedCloneInventoryError(
            "fresh collection authorization is required"
        )
    try:
        check()
    except PreparedCloneInventoryCancellation:
        raise
    except Exception as exc:
        raise PreparedCloneInventoryError(
            "fresh collection authorization is invalid"
        ) from exc


def _request_set(
    inputs: CollectionInputs,
    *,
    challenge: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        binding = inputs.roles[role]
        try:
            if inputs.expected_database_state == "running-healthy":
                request = INVENTORY.build_prepared_request(
                    campaign_id=inputs.campaign_id,
                    operation_id=inputs.operation_id,
                    release_sha=inputs.release_sha,
                    release_tree_sha=inputs.release_tree_sha,
                    role=role,
                    agent_sha256=inputs.agent_sha256,
                    contract_worker_sha256=(
                        binding.contract_worker_sha256
                    ),
                    role_manifest_sha256=binding.role_manifest_sha256,
                    controller_challenge_sha256=challenge,
                    issued_at=issued_at,
                    expires_at=expires_at,
                )
            else:
                request = (
                    INVENTORY.build_stopped_request_from_prepared_response(
                        prepared_request=inputs.prior_requests[role],
                        prepared_response=inputs.prior_responses[role],
                        controller_challenge_sha256=challenge,
                        issued_at=issued_at,
                        expires_at=expires_at,
                    )
                )
                if (
                    request["agent_sha256"] != inputs.agent_sha256
                    or request["contract_worker_sha256"]
                    != binding.contract_worker_sha256
                    or request["role_manifest_sha256"]
                    != binding.role_manifest_sha256
                ):
                    raise PreparedCloneInventoryError(
                        f"{role} stopped baseline differs from inputs"
                    )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise PreparedCloneInventoryError(
                f"{role} fresh request could not be built"
            ) from exc
        requests[role] = request
    if (
        {request["controller_challenge_sha256"] for request in requests.values()}
        != {challenge}
        or {request["issued_at"] for request in requests.values()}
        != {_timestamp(issued_at)}
        or {request["expires_at"] for request in requests.values()}
        != {_timestamp(expires_at)}
    ):
        raise PreparedCloneInventoryError(
            "three-role fresh request binding differs"
        )
    return requests


def _strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=lambda pairs: _strict_object(pairs, label=label),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PreparedCloneInventoryError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict):
        raise PreparedCloneInventoryError(f"{label} is not an object")
    return document


def _strict_object(
    pairs: list[tuple[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError(f"duplicate {label} field")
        result[key] = item
    return result


def _absolute_normalized_path(value: Path, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or path != Path(os.path.abspath(path))
        or ".." in path.parts
    ):
        raise PreparedCloneInventoryError(
            f"{label} path is not absolute and normalized"
        )
    return path


def _read_root_private_bytes(
    path: Path,
    *,
    label: str,
    maximum: int,
    require_private_parent: bool = False,
) -> bytes:
    path = _absolute_normalized_path(path, label=label)
    if require_private_parent:
        try:
            parent = path.parent
            metadata = parent.stat(follow_symlinks=False)
            if (
                parent.resolve(strict=True) != parent
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise PreparedCloneInventoryError(
                    f"{label} parent is not root-private mode 0700"
                )
        except PreparedCloneInventoryError:
            raise
        except OSError as exc:
            raise PreparedCloneInventoryError(
                f"{label} parent is unavailable or unsafe"
            ) from exc
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=maximum,
        )
    except SecureFileError as exc:
        raise PreparedCloneInventoryError(
            f"{label} is unavailable or unsafe"
        ) from exc
    if not payload:
        raise PreparedCloneInventoryError(f"{label} is empty")
    return payload


def _read_private_canonical_json(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    payload = _read_root_private_bytes(
        path,
        label=label,
        maximum=MAX_CONTROLLER_JSON_BYTES,
        require_private_parent=True,
    )
    if payload.endswith(b"\n"):
        if payload.count(b"\n") != 1:
            raise PreparedCloneInventoryError(
                f"{label} is not canonical JSON"
            )
        encoded = payload[:-1]
    else:
        encoded = payload
    document = _strict_json(encoded, label=label)
    if payload not in {canonical_json(document), canonical_json(document) + b"\n"}:
        raise PreparedCloneInventoryError(
            f"{label} is not canonical JSON"
        )
    return document, payload, _sha256(payload)


def _read_release_artifact(path: Path, *, label: str) -> str:
    path = _absolute_normalized_path(path, label=label)
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
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
            or not 1 <= before.st_size <= MAX_RELEASE_ARTIFACT_BYTES
        ):
            raise PreparedCloneInventoryError(
                f"{label} metadata is unsafe"
            )
        digest = hashlib.sha256()
        observed = 0
        while observed <= MAX_RELEASE_ARTIFACT_BYTES:
            chunk = os.read(
                descriptor,
                min(
                    1024 * 1024,
                    MAX_RELEASE_ARTIFACT_BYTES + 1 - observed,
                ),
            )
            if not chunk:
                break
            observed += len(chunk)
            digest.update(chunk)
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
        if (
            observed != before.st_size
            or observed > MAX_RELEASE_ARTIFACT_BYTES
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
        ):
            raise PreparedCloneInventoryError(
                f"{label} changed while being read"
            )
        return digest.hexdigest()
    except PreparedCloneInventoryError:
        raise
    except OSError as exc:
        raise PreparedCloneInventoryError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _release_git_output(
    release_root: Path,
    *arguments: str,
    label: str,
) -> str:
    try:
        return RELEASE._run_text(  # noqa: SLF001
            [
                RELEASE.GIT,
                "-C",
                str(release_root),
                *arguments,
            ],
            timeout=60,
            env=RELEASE.SAFE_GIT_ENV,
        )
    except RELEASE.ReleaseArtifactError as exc:
        raise PreparedCloneInventoryError(
            f"{label} verification failed closed"
        ) from exc


def _verify_immutable_release_checkout(
    release_root: Path,
    *,
    release_sha: str,
    release_tree_sha: str,
) -> tuple[dict[str, str], dict[str, str]]:
    release_root = _absolute_normalized_path(
        release_root,
        label="immutable release root",
    )
    try:
        RELEASE._verify_release_source(  # noqa: SLF001
            release_root,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            owner_uid=0,
        )
    except RELEASE.ReleaseArtifactError as exc:
        raise PreparedCloneInventoryError(
            "release is not exact, detached, clean, and isolated"
        ) from exc
    artifact_sha256: dict[str, str] = {}
    git_blobs: dict[str, str] = {}
    for label, relative in RELEASE_ARTIFACT_RELATIVE_PATHS.items():
        row = _release_git_output(
            release_root,
            "ls-tree",
            "HEAD",
            "--",
            relative.as_posix(),
            label=f"release Git tree entry {label}",
        )
        pieces = row.split(None, 3)
        if (
            len(pieces) != 4
            or pieces[0] not in {"100644", "100755"}
            or pieces[1] != "blob"
            or INVENTORY.SHA40_RE.fullmatch(pieces[2]) is None
            or pieces[3] != relative.as_posix()
        ):
            raise PreparedCloneInventoryError(
                f"release Git tree entry {label} is not exact"
            )
        source = release_root / relative
        observed_blob = _release_git_output(
            release_root,
            "hash-object",
            "--no-filters",
            str(source),
            label=f"release Git blob {label}",
        )
        if observed_blob != pieces[2]:
            raise PreparedCloneInventoryError(
                f"release artifact {label} differs from its Git blob"
            )
        artifact_sha256[label] = _read_release_artifact(
            source,
            label=f"release-bound {label.replace('_', ' ')}",
        )
        git_blobs[label] = pieces[2]
    try:
        RELEASE._verify_release_source(  # noqa: SLF001
            release_root,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            owner_uid=0,
        )
    except RELEASE.ReleaseArtifactError as exc:
        raise PreparedCloneInventoryError(
            "release changed during immutable artifact verification"
        ) from exc
    return artifact_sha256, git_blobs


def _load_release_closure(
    path: Path,
    *,
    manifest: Mapping[str, Any],
) -> str:
    document, payload, digest = _read_private_canonical_json(
        path,
        label="sealed release closure",
    )
    try:
        closure, observed_payload, observed_digest = (
            FINLAND.load_release_closure(
                path,
                operation_id=manifest["operation_id"],
                release_sha=manifest["release_sha"],
                release_tree_sha=manifest["release_tree_sha"],
                required_uid=0,
            )
        )
    except FINLAND.FinlandArtifactOrchestratorError as exc:
        raise PreparedCloneInventoryError(
            "sealed release closure is invalid"
        ) from exc
    artifacts = manifest["artifacts"]
    if (
        closure != document
        or observed_payload != payload
        or observed_digest != digest
        or closure["release"]["bundle"]["sha256"]
        != artifacts["release_bundle_sha256"]
        or closure["release"]["bundle"]["bytes"]
        != artifacts["release_bundle_bytes"]
        or closure["images"] != artifacts["image_artifacts"]
    ):
        raise PreparedCloneInventoryError(
            "sealed release closure differs from the manifest"
        )
    return digest


def _validate_prepare_metadata(
    document: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise PreparedCloneInventoryError(
            "prepare material metadata is not an object"
        )
    value = dict(document)
    if runtime_targets.is_legacy_prepare_material_schema(value):
        raise PreparedCloneInventoryError(
            runtime_targets.PREPARE_V2_MIGRATION_MESSAGE
        )
    if runtime_targets.is_legacy_cutover_manifest_schema(manifest):
        raise PreparedCloneInventoryError(
            runtime_targets.CUTOVER_V2_MIGRATION_MESSAGE
        )
    if manifest.get("schema") != CONTROLLER.MANIFEST_SCHEMA:
        raise PreparedCloneInventoryError(
            "prepared clone inventory requires a fresh v4 cutover manifest"
        )
    artifacts = manifest["artifacts"]
    try:
        runtime_targets.validate_runtime_target_capabilities(
            value.get("capabilities"),
            label="prepare material capabilities",
        )
        runtime_targets.validate_runtime_target_capabilities(
            manifest.get("capabilities"),
            label="cutover manifest capabilities",
        )
        metadata_runtime_targets = runtime_targets.validate_runtime_target_descriptor(
            value.get("controller_bindings", {}).get(
                "convergence_runtime_targets"
            ),
            label="prepare convergence runtime target descriptor",
        )
        manifest_runtime_targets = runtime_targets.validate_runtime_target_descriptor(
            artifacts["convergence_runtime_targets"],
            label="cutover convergence runtime target descriptor",
        )
    except (
        AttributeError,
        KeyError,
        runtime_targets.ConvergenceRuntimeTargetDescriptorError,
    ) as exc:
        raise PreparedCloneInventoryError(
            "prepare convergence runtime target descriptor or capability is invalid"
        ) from exc
    if metadata_runtime_targets != manifest_runtime_targets:
        raise PreparedCloneInventoryError(
            "prepare convergence runtime target descriptor differs from the "
            "cutover manifest"
        )
    if (
        set(value) != PREPARE_METADATA_FIELDS
        or value["schema"] != PREPARE.SET_SCHEMA
        or value["capabilities"]
        != list(runtime_targets.RUNTIME_TARGET_CAPABILITIES)
        or value["operation_id"] != manifest["operation_id"]
        or value["release_sha"] != manifest["release_sha"]
        or value["canonical_compose_sha256"]
        != artifacts["shadow_compose_sha256"]
        or value["activation_secrets_included"] is not False
        or value["precommit_manifest_bound"] is not False
        or not isinstance(value.get("roles"), Mapping)
        or set(value["roles"]) != set(PREPARE.ALL_ROLES)
        or value.get("controller_bindings")
        != {
            "role_materials": artifacts["role_materials"],
            "role_runtime_image_ids": artifacts[
                "role_runtime_image_ids"
            ],
            "convergence_runtime_targets": manifest_runtime_targets,
        }
    ):
        raise PreparedCloneInventoryError(
            "prepare material metadata differs from the manifest"
        )
    for field in ("dr_ca_sha256", "dr_tls_attestation_sha256"):
        _nonzero_sha256(
            value[field],
            label=f"prepare material {field}",
        )
    epoch = value["dr_tls_attested_at_epoch"]
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or not 1 <= epoch <= 4_102_444_800
    ):
        raise PreparedCloneInventoryError(
            "prepare material TLS attestation epoch is invalid"
        )
    internal_manifests: set[str] = set()
    for role in PREPARE.ALL_ROLES:
        row = value["roles"][role]
        material = artifacts["role_materials"][role]
        if (
            not isinstance(row, Mapping)
            or set(row) != PREPARE_ROLE_FIELDS
            or row["filename"] != PREPARE.ROLE_ARCHIVE_NAMES[role]
            or {
                field: row[field]
                for field in ("sha256", "bytes", "transport", "format")
            }
            != material
        ):
            raise PreparedCloneInventoryError(
                f"{role} prepare material binding differs"
            )
        internal_manifests.add(
            _nonzero_sha256(
                row["internal_manifest_sha256"],
                label=f"{role} role manifest",
            )
        )
        for field in (
            "stage_operation_manifest_sha256",
            "stage_attestation_sha256",
        ):
            _nonzero_sha256(
                row[field],
                label=f"{role} prepare material {field}",
            )
    if len(internal_manifests) != len(PREPARE.ALL_ROLES):
        raise PreparedCloneInventoryError(
            "prepare role manifest digests are not distinct"
        )
    return value


def _assert_private_output_root(path: Path) -> Path:
    path = _absolute_normalized_path(
        path,
        label="prepared inventory output root",
    )
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            path.resolve(strict=True) != path
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PreparedCloneInventoryError(
                "prepared inventory output root is not root-private mode 0700"
            )
    except PreparedCloneInventoryError:
        raise
    except OSError as exc:
        raise PreparedCloneInventoryError(
            "prepared inventory output root is unavailable or unsafe"
        ) from exc
    return path


def _collection_inputs_for_state(
    running_inputs: CollectionInputs,
    *,
    output_root: Path,
    expected_database_state: str,
    prior_running_receipt: Path | None,
    prior_running_challenge_sha256: str | None,
    prior_running_receipt_sha256: str | None,
) -> tuple[CollectionInputs, dict[str, str] | None]:
    running_inputs = _validate_inputs(running_inputs)
    if expected_database_state == "running-healthy":
        if any(
            item is not None
            for item in (
                prior_running_receipt,
                prior_running_challenge_sha256,
                prior_running_receipt_sha256,
            )
        ):
            raise PreparedCloneInventoryError(
                "running collection does not accept a prior receipt"
            )
        return running_inputs, None
    if expected_database_state != "stopped":
        raise PreparedCloneInventoryError(
            "prepared inventory database state is invalid"
        )
    if (
        prior_running_receipt is None
        or prior_running_challenge_sha256 is None
        or prior_running_receipt_sha256 is None
    ):
        raise PreparedCloneInventoryError(
            "stopped collection requires one exact prior running receipt"
        )
    prior_path = _absolute_normalized_path(
        prior_running_receipt,
        label="prior running receipt",
    )
    prior_challenge = _nonzero_sha256(
        prior_running_challenge_sha256,
        label="prior running receipt challenge",
    )
    prior_artifact_sha256 = _nonzero_sha256(
        prior_running_receipt_sha256,
        label="prior running receipt artifact",
    )
    try:
        loaded_prior = (
            load_historical_running_prepared_clone_baseline_receipt(
                prior_path,
                output_root=output_root,
                expected_campaign_id=running_inputs.campaign_id,
                expected_operation_id=running_inputs.operation_id,
                expected_release_sha=running_inputs.release_sha,
                expected_release_tree_sha=running_inputs.release_tree_sha,
                expected_controller_challenge_sha256=prior_challenge,
                expected_aggregate_artifact_sha256=(
                    prior_artifact_sha256
                ),
            )
        )
    except PreparedCloneInventoryError as exc:
        raise PreparedCloneInventoryError(
            "prior running prepared inventory receipt is invalid"
        ) from exc
    prior_receipt = loaded_prior["receipt"]
    if (
        prior_receipt["expected_database_state"] != "running-healthy"
        or any(
            not _request_matches_inputs(
                running_inputs,
                role,
                loaded_prior["requests"][role],
            )
            for role in ROLES
        )
    ):
        raise PreparedCloneInventoryError(
            "prior running receipt release binding differs"
        )
    stopped_inputs = CollectionInputs(
        **{
            **running_inputs.__dict__,
            "expected_database_state": "stopped",
            "prior_requests": loaded_prior["requests"],
            "prior_responses": loaded_prior["responses"],
        }
    )
    _validate_inputs(stopped_inputs)
    return stopped_inputs, {
        "path": str(prior_path),
        "controller_challenge_sha256": prior_challenge,
        "aggregate_artifact_sha256": prior_artifact_sha256,
        "aggregate_sha256": prior_receipt["aggregate_sha256"],
    }


def load_controller_context(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    release_closure_path: Path,
    prepare_metadata_path: Path,
    ssh_identity: Path,
    known_hosts: Path,
    expected_database_state: str = "running-healthy",
    prior_running_receipt: Path | None = None,
    prior_running_challenge_sha256: str | None = None,
    prior_running_receipt_sha256: str | None = None,
) -> ControllerContext:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PreparedCloneInventoryError(
            "prepared inventory controller requires root:root"
        )
    manifest_path = _absolute_normalized_path(
        manifest_path,
        label="production cutover manifest",
    )
    approval_path = _absolute_normalized_path(
        approval_path,
        label="production cutover approval",
    )
    approval_policy_path = _absolute_normalized_path(
        approval_policy_path,
        label="production human approval policy",
    )
    release_closure_path = _absolute_normalized_path(
        release_closure_path,
        label="sealed release closure",
    )
    prepare_metadata_path = _absolute_normalized_path(
        prepare_metadata_path,
        label="prepare material metadata",
    )
    ssh_identity = _absolute_normalized_path(
        ssh_identity,
        label="SSH identity",
    )
    known_hosts = _absolute_normalized_path(
        known_hosts,
        label="SSH known-hosts",
    )
    if ssh_identity == known_hosts:
        raise PreparedCloneInventoryError(
            "production SSH trust binding is invalid"
        )
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(
            manifest_path
        )
        cutover_plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=manifest_path,
        )
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            manifest,
            approval_path=approval_path,
            approval_policy_path=approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreparedCloneInventoryError(
            "production cutover manifest or authorization is invalid"
        ) from exc
    approval = _read_root_private_bytes(
        approval_path,
        label="production cutover approval",
        maximum=MAX_CONTROLLER_JSON_BYTES,
    )
    approval_policy = _read_root_private_bytes(
        approval_policy_path,
        label="production human approval policy",
        maximum=MAX_CONTROLLER_JSON_BYTES,
    )
    approval_sha256 = _sha256(approval)
    approval_policy_sha256 = _sha256(approval_policy)
    if (
        approval_sha256
        != manifest["artifacts"]["cutover_approval_sha256"]
        or approval_policy_sha256
        != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise PreparedCloneInventoryError(
            "approval or policy bytes differ from the manifest"
        )
    release_closure_sha256 = _load_release_closure(
        release_closure_path,
        manifest=manifest,
    )
    prepare_document, _prepare_payload, prepare_sha256 = (
        _read_private_canonical_json(
            prepare_metadata_path,
            label="prepare material metadata",
        )
    )
    prepare_metadata = _validate_prepare_metadata(
        prepare_document,
        manifest=manifest,
    )
    expected_shadow_root = (
        INVENTORY.PROJECT_ROOT_PREFIX / manifest["operation_id"]
    )
    shadow_root = Path(manifest["deployment"]["shadow_root"])
    if shadow_root != expected_shadow_root:
        raise PreparedCloneInventoryError(
            "prepared inventory release root differs from canonical topology"
        )
    release_root = shadow_root / "releases" / manifest["release_sha"]
    (
        release_artifact_sha256,
        release_artifact_git_blob,
    ) = _verify_immutable_release_checkout(
        release_root,
        release_sha=manifest["release_sha"],
        release_tree_sha=manifest["release_tree_sha"],
    )
    for role in ROLES:
        topology = manifest["topology"][role]
        expected = CONTROLLER.EXPECTED_TOPOLOGY[role]
        if (
            topology != expected
            or topology["host"] != INVENTORY.ROLE_HOSTS[role]
        ):
            raise PreparedCloneInventoryError(
                f"{role} prepared inventory topology differs"
            )
    ssh_identity_payload = _read_root_private_bytes(
        ssh_identity,
        label="SSH identity",
        maximum=MAX_SSH_TRUST_BYTES,
    )
    known_hosts_payload = _read_root_private_bytes(
        known_hosts,
        label="SSH known-hosts",
        maximum=MAX_SSH_TRUST_BYTES,
    )
    output_root = _assert_private_output_root(
        Path(manifest["deployment"]["controller_evidence_root"])
    )
    running_inputs = CollectionInputs(
        campaign_id=manifest["campaign_id"],
        operation_id=manifest["operation_id"],
        release_sha=manifest["release_sha"],
        release_tree_sha=manifest["release_tree_sha"],
        agent_sha256=release_artifact_sha256["inventory_agent"],
        roles={
            role: RoleBinding(
                contract_worker_sha256=release_artifact_sha256[
                    (
                        "wa_ir_operation_worker"
                        if role == "webapp_ir"
                        else "finland_precommit_worker"
                    )
                ],
                role_manifest_sha256=prepare_metadata["roles"][role][
                    "internal_manifest_sha256"
                ],
            )
            for role in ROLES
        },
    )
    _validate_inputs(running_inputs)
    collection_inputs, prior_binding = _collection_inputs_for_state(
        running_inputs,
        output_root=output_root,
        expected_database_state=expected_database_state,
        prior_running_receipt=prior_running_receipt,
        prior_running_challenge_sha256=(
            prior_running_challenge_sha256
        ),
        prior_running_receipt_sha256=prior_running_receipt_sha256,
    )
    return ControllerContext(
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        release_closure_path=release_closure_path,
        prepare_metadata_path=prepare_metadata_path,
        ssh_identity=ssh_identity,
        known_hosts=known_hosts,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        cutover_plan_sha256=_nonzero_sha256(
            cutover_plan.get("plan_sha256"),
            label="production cutover plan",
        ),
        approval_sha256=approval_sha256,
        approval_policy_sha256=approval_policy_sha256,
        release_closure_sha256=release_closure_sha256,
        prepare_metadata_sha256=prepare_sha256,
        release_artifact_sha256=release_artifact_sha256,
        release_artifact_git_blob=release_artifact_git_blob,
        ssh_identity_sha256=_sha256(ssh_identity_payload),
        known_hosts_sha256=_sha256(known_hosts_payload),
        output_root=output_root,
        collection_inputs=collection_inputs,
        prior_running_receipt=prior_binding,
    )


def _resume_binding(
    context: ControllerContext,
    *,
    resume_receipt: Path | None,
    resume_receipt_sha256: str | None,
) -> dict[str, str] | None:
    if resume_receipt is None and resume_receipt_sha256 is None:
        return None
    if resume_receipt is None or resume_receipt_sha256 is None:
        raise PreparedCloneInventoryError(
            "resume requires both receipt path and exact artifact digest"
        )
    path = _absolute_normalized_path(
        resume_receipt,
        label="resume receipt",
    )
    digest = _nonzero_sha256(
        resume_receipt_sha256,
        label="resume receipt artifact",
    )
    try:
        challenge = _nonzero_sha256(
            path.parent.name,
            label="resume receipt challenge",
        )
    except PreparedCloneInventoryError:
        raise
    expected = canonical_receipt_path(
        context.output_root,
        operation_id=context.collection_inputs.operation_id,
        controller_challenge_sha256=challenge,
    )
    if path != expected:
        raise PreparedCloneInventoryError(
            "resume receipt path differs from the manifest output root"
        )
    return {"path": str(path), "sha256": digest}


def build_controller_plan(
    context: ControllerContext,
    *,
    resume_receipt: Path | None = None,
    resume_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(context, ControllerContext):
        raise PreparedCloneInventoryError(
            "prepared inventory controller context is invalid"
        )
    inputs = _validate_inputs(context.collection_inputs)
    if (
        set(context.release_artifact_sha256)
        != set(RELEASE_ARTIFACT_RELATIVE_PATHS)
        or set(context.release_artifact_git_blob)
        != set(RELEASE_ARTIFACT_RELATIVE_PATHS)
        or any(
            _nonzero_sha256(
                context.release_artifact_sha256[label],
                label=f"controller release artifact {label}",
            )
            != context.release_artifact_sha256[label]
            for label in RELEASE_ARTIFACT_RELATIVE_PATHS
        )
        or any(
            not isinstance(context.release_artifact_git_blob[label], str)
            or INVENTORY.SHA40_RE.fullmatch(
                context.release_artifact_git_blob[label]
            )
            is None
            for label in RELEASE_ARTIFACT_RELATIVE_PATHS
        )
    ):
        raise PreparedCloneInventoryError(
            "controller release artifact closure is invalid"
        )
    _nonzero_sha256(
        context.release_closure_sha256,
        label="controller release closure",
    )
    expected_prior_fields = {
        "path",
        "controller_challenge_sha256",
        "aggregate_artifact_sha256",
        "aggregate_sha256",
    }
    if inputs.expected_database_state == "running-healthy":
        if context.prior_running_receipt is not None:
            raise PreparedCloneInventoryError(
                "running controller context has a prior receipt"
            )
    elif (
        not isinstance(context.prior_running_receipt, Mapping)
        or set(context.prior_running_receipt) != expected_prior_fields
    ):
        raise PreparedCloneInventoryError(
            "stopped controller context lacks its exact prior receipt"
        )
    else:
        prior = context.prior_running_receipt
        prior_challenge = _nonzero_sha256(
            prior["controller_challenge_sha256"],
            label="controller prior receipt challenge",
        )
        for field in ("aggregate_artifact_sha256", "aggregate_sha256"):
            _nonzero_sha256(
                prior[field],
                label=f"controller prior receipt {field}",
            )
        expected_prior_path = canonical_receipt_path(
            context.output_root,
            operation_id=inputs.operation_id,
            controller_challenge_sha256=prior_challenge,
        )
        if _absolute_normalized_path(
            Path(prior["path"]),
            label="controller prior receipt",
        ) != expected_prior_path:
            raise PreparedCloneInventoryError(
                "controller prior receipt path differs"
            )
        running_inputs = CollectionInputs(
            **{
                **inputs.__dict__,
                "expected_database_state": "running-healthy",
                "prior_requests": None,
                "prior_responses": None,
            }
        )
        if any(
            not _request_matches_inputs(
                running_inputs,
                role,
                inputs.prior_requests[role],
            )
            for role in ROLES
        ):
            raise PreparedCloneInventoryError(
                "controller prior receipt request bindings differ"
            )
    collection_plan = build_plan(context.collection_inputs)
    resume = _resume_binding(
        context,
        resume_receipt=resume_receipt,
        resume_receipt_sha256=resume_receipt_sha256,
    )
    body: dict[str, Any] = {
        "schema": CONTROLLER_PLAN_SCHEMA,
        "status": "planned",
        "mode": "resume-readback" if resume is not None else "fresh-collect",
        "campaign_id": context.collection_inputs.campaign_id,
        "operation_id": context.collection_inputs.operation_id,
        "release_sha": context.collection_inputs.release_sha,
        "release_tree_sha": context.collection_inputs.release_tree_sha,
        "manifest_sha256": context.manifest_sha256,
        "cutover_plan_sha256": context.cutover_plan_sha256,
        "approval_sha256": context.approval_sha256,
        "approval_policy_sha256": context.approval_policy_sha256,
        "release_closure_sha256": context.release_closure_sha256,
        "prepare_metadata_sha256": context.prepare_metadata_sha256,
        "release_artifact_sha256": dict(
            context.release_artifact_sha256
        ),
        "release_artifact_git_blob": dict(
            context.release_artifact_git_blob
        ),
        "ssh_trust": {
            "identity_sha256": context.ssh_identity_sha256,
            "known_hosts_sha256": context.known_hosts_sha256,
        },
        "output_root": str(context.output_root),
        "collection_plan": collection_plan,
        "prior_running_receipt": (
            dict(context.prior_running_receipt)
            if context.prior_running_receipt is not None
            else None
        ),
        "resume_receipt": resume,
        "controller_liveness_pipe_required": True,
        "runtime_authorization_checks": 2 if resume is not None else 7,
        "receipt_artifact_count": 7,
        "create_only": True,
        "readback_required": True,
        "collection_performed": False,
        "production_contacted": False,
    }
    plan_sha256 = _sha256(canonical_json(body))
    body["plan_sha256"] = plan_sha256
    body["required_confirmation"] = (
        f"{CONTROLLER_CONFIRMATION_PREFIX}:"
        f"{context.collection_inputs.operation_id}:"
        f"{body['mode']}:{plan_sha256}"
    )
    return body


def _validate_invocation_result(
    value: Any,
    *,
    role: str,
) -> InvocationResult:
    if (
        not isinstance(value, InvocationResult)
        or type(value.returncode) is not int
        or not isinstance(value.stdout, bytes)
        or not isinstance(value.stderr, bytes)
        or type(value.timed_out) is not bool
        or type(value.stdout_limit_exceeded) is not bool
        or type(value.stderr_limit_exceeded) is not bool
        or value.process_group_cleanup_performed is not True
        or value.returncode != 0
        or value.timed_out
        or value.stdout_limit_exceeded
        or value.stderr_limit_exceeded
        or value.stderr
        or not value.stdout.endswith(b"\n")
        or value.stdout.count(b"\n") != 1
        or len(value.stdout) > INVENTORY.MAX_RESPONSE_BYTES + 1
    ):
        raise PreparedCloneInventoryError(
            f"{role} bounded inventory invocation failed"
        )
    return value


def build_aggregate(
    *,
    inputs: CollectionInputs,
    requests: Mapping[str, Mapping[str, Any]],
    responses: Mapping[str, Mapping[str, Any]],
    command_times: Mapping[str, tuple[datetime, datetime]],
    now: datetime,
) -> dict[str, Any]:
    inputs = _validate_inputs(inputs)
    observed_now = _utc_datetime(now, label="aggregate controller time")
    if (
        set(requests) != set(ROLES)
        or set(responses) != set(ROLES)
        or set(command_times) != set(ROLES)
    ):
        raise PreparedCloneInventoryError(
            "aggregate roles are not exact"
        )
    validated_requests: dict[str, dict[str, Any]] = {}
    validated_responses: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        try:
            request = INVENTORY.validate_prepared_request(
                requests[role],
                now=observed_now,
            )
            response = INVENTORY.validate_prepared_response(
                responses[role],
                request=request,
                now=observed_now,
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise PreparedCloneInventoryError(
                f"{role} prepared inventory is invalid"
            ) from exc
        if not _request_matches_inputs(inputs, role, request):
            raise PreparedCloneInventoryError(
                f"{role} prepared request differs from collection inputs"
            )
        started, completed = command_times[role]
        started = _utc_datetime(
            started,
            label=f"{role} command start",
        )
        completed = _utc_datetime(
            completed,
            label=f"{role} command completion",
        )
        captured = _parse_timestamp(
            response["captured_at"],
            label=f"{role} captured_at",
        )
        if (
            completed < started
            or captured
            < started - timedelta(seconds=COMMAND_CLOCK_SKEW_SECONDS)
            or captured
            > completed + timedelta(seconds=COMMAND_CLOCK_SKEW_SECONDS)
            or completed > observed_now
        ):
            raise PreparedCloneInventoryError(
                f"{role} command/capture chronology differs"
            )
        request_bytes = canonical_json(request) + b"\n"
        response_bytes = canonical_json(response) + b"\n"
        rows[role] = {
            "role": role,
            "expected_host": response["expected_host"],
            "contract_kind": response["contract_kind"],
            "request_sha256": _sha256(request_bytes),
            "request_bytes": len(request_bytes),
            "response_sha256": _sha256(response_bytes),
            "response_document_sha256": response["response_sha256"],
            "response_bytes": len(response_bytes),
            "request_binding_sha256": request["request_binding_sha256"],
            "command_started_at": _timestamp(started),
            "command_completed_at": _timestamp(completed),
            "captured_at": response["captured_at"],
            "prepared_container_id": response["prepared_container_id"],
            "prepared_network_id": response["prepared_network_id"],
            "prepared_redis_identity_sha256": response[
                "prepared_redis_identity_sha256"
            ],
            "prepared_redis_chain_metadata_sha256": response[
                "prepared_redis_chain_metadata_sha256"
            ],
            "prepared_redis_metadata_sha256": response[
                "prepared_redis_metadata_sha256"
            ],
            "prepared_redis_target_count": response[
                "prepared_redis_target_count"
            ],
            "prepared_redis_unsafe_path_count": response[
                "prepared_redis_unsafe_path_count"
            ],
            "prepared_redis_entry_count": response[
                "prepared_redis_entry_count"
            ],
            "prepared_redis_pristine": response[
                "prepared_redis_pristine"
            ],
            "inventory_root_sha256": response["inventory_root_sha256"],
            "non_operation_inventory_root_sha256": response[
                "non_operation_inventory_root_sha256"
            ],
            "operation_resource_root_sha256": response[
                "operation_resource_root_sha256"
            ],
        }
        validated_requests[role] = request
        validated_responses[role] = response
    challenges = {
        request["controller_challenge_sha256"]
        for request in validated_requests.values()
    }
    issued_values = {
        request["issued_at"] for request in validated_requests.values()
    }
    expiry_values = {
        request["expires_at"] for request in validated_requests.values()
    }
    identities = {
        (
            request["campaign_id"],
            request["operation_id"],
            request["release_sha"],
            request["release_tree_sha"],
            request["expected_database_state"],
        )
        for request in validated_requests.values()
    }
    if (
        len(challenges) != 1
        or len(issued_values) != 1
        or len(expiry_values) != 1
        or len(identities) != 1
    ):
        raise PreparedCloneInventoryError(
            "cross-role request identity differs"
        )
    captured_values = [
        _parse_timestamp(
            validated_responses[role]["captured_at"],
            label=f"{role} captured_at",
        )
        for role in ROLES
    ]
    role_skew = (
        max(captured_values) - min(captured_values)
    ).total_seconds()
    if role_skew > MAX_ROLE_CAPTURE_SKEW_SECONDS:
        raise PreparedCloneInventoryError(
            "cross-role prepared capture skew exceeds its bound"
        )
    issued_at = _parse_timestamp(
        next(iter(issued_values)),
        label="aggregate issued_at",
    )
    expires_at = _parse_timestamp(
        next(iter(expiry_values)),
        label="aggregate expires_at",
    )
    lifetime = (expires_at - issued_at).total_seconds()
    identity = next(iter(identities))
    result: dict[str, Any] = {
        "schema": AGGREGATE_SCHEMA,
        "status": "captured-prepared-three-role",
        "campaign_id": identity[0],
        "operation_id": identity[1],
        "release_sha": identity[2],
        "release_tree_sha": identity[3],
        "expected_database_state": identity[4],
        "controller_challenge_sha256": next(iter(challenges)),
        "issued_at": _timestamp(issued_at),
        "expires_at": _timestamp(expires_at),
        "captured_at": _timestamp(max(captured_values)),
        "controller_observed_at": _timestamp(
            observed_now
        ),
        "role_captured_at": {
            role: validated_responses[role]["captured_at"]
            for role in ROLES
        },
        "role_capture_skew_seconds": role_skew,
        "request_lifetime_seconds": lifetime,
        "roles": rows,
        "collection_performed": True,
        "production_contacted": True,
        "docker_read_only": True,
        "application_payload_bytes_over_ssh": 0,
    }
    result["aggregate_sha256"] = _sha256(canonical_json(result))
    return validate_aggregate(
        result,
        requests=validated_requests,
        responses=validated_responses,
        now=observed_now,
    )


def validate_aggregate(
    value: Mapping[str, Any],
    *,
    requests: Mapping[str, Mapping[str, Any]],
    responses: Mapping[str, Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != AGGREGATE_FIELDS
        or set(requests) != set(ROLES)
        or set(responses) != set(ROLES)
    ):
        raise PreparedCloneInventoryError(
            "prepared aggregate fields or roles are not exact"
        )
    document = json.loads(canonical_json(dict(value)).decode("ascii"))
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else _utc_datetime(now, label="receipt validation time")
    )
    if (
        document["schema"] != AGGREGATE_SCHEMA
        or document["status"] != "captured-prepared-three-role"
        or document["collection_performed"] is not True
        or document["production_contacted"] is not True
        or document["docker_read_only"] is not True
        or document["application_payload_bytes_over_ssh"] != 0
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != set(ROLES)
        or not isinstance(document["role_captured_at"], dict)
        or set(document["role_captured_at"]) != set(ROLES)
    ):
        raise PreparedCloneInventoryError(
            "prepared aggregate safety boundary differs"
        )
    command_times: dict[str, tuple[datetime, datetime]] = {}
    for role in ROLES:
        row = document["roles"][role]
        if not isinstance(row, dict) or set(row) != ROLE_AGGREGATE_FIELDS:
            raise PreparedCloneInventoryError(
                f"{role} aggregate fields are not exact"
            )
        try:
            request = INVENTORY.validate_prepared_request(
                requests[role],
                now=observed_now,
            )
            response = INVENTORY.validate_prepared_response(
                responses[role],
                request=request,
                now=observed_now,
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise PreparedCloneInventoryError(
                f"{role} aggregate source is invalid"
            ) from exc
        if request["role"] != role:
            raise PreparedCloneInventoryError(
                f"{role} aggregate request role differs"
            )
        request_bytes = canonical_json(request) + b"\n"
        response_bytes = canonical_json(response) + b"\n"
        expected = {
            "role": role,
            "expected_host": response["expected_host"],
            "contract_kind": response["contract_kind"],
            "request_sha256": _sha256(request_bytes),
            "request_bytes": len(request_bytes),
            "response_sha256": _sha256(response_bytes),
            "response_document_sha256": response["response_sha256"],
            "response_bytes": len(response_bytes),
            "request_binding_sha256": request["request_binding_sha256"],
            "captured_at": response["captured_at"],
            "prepared_container_id": response["prepared_container_id"],
            "prepared_network_id": response["prepared_network_id"],
            "prepared_redis_identity_sha256": response[
                "prepared_redis_identity_sha256"
            ],
            "prepared_redis_chain_metadata_sha256": response[
                "prepared_redis_chain_metadata_sha256"
            ],
            "prepared_redis_metadata_sha256": response[
                "prepared_redis_metadata_sha256"
            ],
            "prepared_redis_target_count": response[
                "prepared_redis_target_count"
            ],
            "prepared_redis_unsafe_path_count": response[
                "prepared_redis_unsafe_path_count"
            ],
            "prepared_redis_entry_count": response[
                "prepared_redis_entry_count"
            ],
            "prepared_redis_pristine": response[
                "prepared_redis_pristine"
            ],
            "inventory_root_sha256": response["inventory_root_sha256"],
            "non_operation_inventory_root_sha256": response[
                "non_operation_inventory_root_sha256"
            ],
            "operation_resource_root_sha256": response[
                "operation_resource_root_sha256"
            ],
        }
        if any(row[field] != item for field, item in expected.items()):
            raise PreparedCloneInventoryError(
                f"{role} aggregate source binding differs"
            )
        command_times[role] = (
            _parse_timestamp(
                row["command_started_at"],
                label=f"{role} command_started_at",
            ),
            _parse_timestamp(
                row["command_completed_at"],
                label=f"{role} command_completed_at",
            ),
        )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "aggregate_sha256"
    }
    if document["aggregate_sha256"] != _sha256(canonical_json(unsigned)):
        raise PreparedCloneInventoryError(
            "prepared aggregate SHA-256 differs"
        )
    _nonzero_sha256(
        document["controller_challenge_sha256"],
        label="aggregate controller challenge",
    )
    if (
        type(document["role_capture_skew_seconds"]) not in {int, float}
        or isinstance(document["role_capture_skew_seconds"], bool)
        or not 0
        <= float(document["role_capture_skew_seconds"])
        <= MAX_ROLE_CAPTURE_SKEW_SECONDS
        or document["request_lifetime_seconds"]
        != REQUEST_LIFETIME_SECONDS
    ):
        raise PreparedCloneInventoryError(
            "prepared aggregate freshness metrics differ"
        )
    captured = [
        _parse_timestamp(
            responses[role]["captured_at"],
            label=f"{role} captured_at",
        )
        for role in ROLES
    ]
    if (
        document["captured_at"] != _timestamp(max(captured))
        or document["role_captured_at"]
        != {role: responses[role]["captured_at"] for role in ROLES}
        or document["role_capture_skew_seconds"]
        != (max(captured) - min(captured)).total_seconds()
        or any(
            document[field] != requests["bot_fi"][field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "expected_database_state",
                "controller_challenge_sha256",
                "issued_at",
                "expires_at",
            )
        )
    ):
        raise PreparedCloneInventoryError(
            "prepared aggregate cross-role binding differs"
        )
    controller_observed_at = _parse_timestamp(
        document["controller_observed_at"],
        label="aggregate controller_observed_at",
    )
    if (
        controller_observed_at
        < max(completed for _, completed in command_times.values())
        or controller_observed_at
        < _parse_timestamp(
            document["issued_at"],
            label="aggregate issued_at",
        )
        or controller_observed_at
        > _parse_timestamp(
            document["expires_at"],
            label="aggregate expires_at",
        )
        or controller_observed_at
        > observed_now + timedelta(seconds=COMMAND_CLOCK_SKEW_SECONDS)
    ):
        raise PreparedCloneInventoryError(
            "prepared aggregate controller time differs"
        )
    for role in ROLES:
        if any(
            requests[role][field] != requests["bot_fi"][field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "expected_database_state",
                "controller_challenge_sha256",
                "issued_at",
                "expires_at",
            )
        ):
            raise PreparedCloneInventoryError(
                "prepared aggregate request identities differ"
            )
        started, completed = command_times[role]
        observed = captured[ROLES.index(role)]
        if (
            completed < started
            or observed
            < started - timedelta(seconds=COMMAND_CLOCK_SKEW_SECONDS)
            or observed
            > completed + timedelta(seconds=COMMAND_CLOCK_SKEW_SECONDS)
        ):
            raise PreparedCloneInventoryError(
                f"{role} aggregate chronology differs"
            )
    expires_at = _parse_timestamp(
        document["expires_at"],
        label="aggregate expires_at",
    )
    if observed_now > expires_at:
        raise PreparedCloneInventoryError(
            "prepared aggregate is expired"
        )
    return document


def validate_pre_freeze_current_operation_receipt(
    value: Mapping[str, Any],
    *,
    requests: Mapping[str, Mapping[str, Any]],
    responses: Mapping[str, Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Strict public validator consumed by the pre-freeze phase bridge."""

    return validate_aggregate(
        value,
        requests=requests,
        responses=responses,
        now=now,
    )


def canonical_receipt_path(
    output_root: Path,
    *,
    operation_id: str,
    controller_challenge_sha256: str,
) -> Path:
    root = Path(output_root)
    if (
        not root.is_absolute()
        or root != Path(os.path.abspath(root))
        or ".." in root.parts
    ):
        raise PreparedCloneInventoryError(
            "receipt output root is not absolute and normalized"
        )
    _nonzero_sha256(
        controller_challenge_sha256,
        label="receipt controller challenge",
    )
    try:
        parsed = INVENTORY._canonical_uuid4(  # noqa: SLF001
            operation_id,
            label="operation ID",
        )
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(
            "receipt operation ID is invalid"
        ) from exc
    return (
        root
        / "prepared-clone-inventory"
        / parsed
        / controller_challenge_sha256
        / PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
    )


def _open_private_directory(path: Path, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise PreparedCloneInventoryError(
                "receipt directory cannot be created"
            ) from exc
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise PreparedCloneInventoryError(
            "receipt directory is unavailable or unsafe"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise PreparedCloneInventoryError(
            "receipt directory is not root-only"
        )
    return descriptor


def _open_private_child_directory(
    parent_fd: int,
    component: str,
    *,
    create: bool,
) -> int:
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\x00" in component
    ):
        raise PreparedCloneInventoryError(
            "receipt directory component is invalid"
        )
    if create:
        try:
            os.mkdir(component, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise PreparedCloneInventoryError(
                "receipt directory cannot be created"
            ) from exc
    try:
        descriptor = os.open(
            component,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise PreparedCloneInventoryError(
            "receipt directory is unavailable or unsafe"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise PreparedCloneInventoryError(
            "receipt directory is not root-only"
        )
    return descriptor


def _read_private_artifact(
    directory_fd: int,
    filename: str,
    *,
    maximum: int,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= maximum
        ):
            raise PreparedCloneInventoryError(
                "receipt artifact metadata differs"
            )
        observed = bytearray()
        while len(observed) <= maximum:
            chunk = os.read(
                descriptor,
                min(64 * 1024, maximum + 1 - len(observed)),
            )
            if not chunk:
                break
            observed.extend(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            filename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        stable_fields = (
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
            len(observed) != before.st_size
            or len(observed) > maximum
            or any(
                getattr(before, field) != getattr(item, field)
                for item in (after, visible)
                for field in stable_fields
            )
        ):
            raise PreparedCloneInventoryError(
                "receipt artifact changed during readback"
            )
        return bytes(observed)
    except PreparedCloneInventoryError:
        raise
    except OSError as exc:
        raise PreparedCloneInventoryError(
            "receipt artifact is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_or_reconcile_artifact(
    directory_fd: int,
    filename: str,
    payload: bytes,
) -> dict[str, Any]:
    descriptor = -1
    created = False
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            created = True
        except FileExistsError:
            descriptor = -1
        except OSError as exc:
            raise PreparedCloneInventoryError(
                "receipt artifact cannot be published safely"
            ) from exc
        if created:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise PreparedCloneInventoryError(
                        "receipt artifact write made no progress"
                    )
                offset += written
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size != len(payload)
            ):
                raise PreparedCloneInventoryError(
                    "published receipt artifact metadata differs"
                )
            os.close(descriptor)
            descriptor = -1
            os.fsync(directory_fd)
        observed = _read_private_artifact(
            directory_fd,
            filename,
            maximum=len(payload),
        )
        if observed != payload:
            raise PreparedCloneInventoryError(
                "existing receipt artifact differs from exact bytes"
            )
        return {
            "filename": filename,
            "sha256": _sha256(payload),
            "bytes": len(payload),
            "created": created,
            "reconciled": not created,
            "readback_verified": True,
        }
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _artifact_reference(
    directory: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **dict(metadata),
        "path": str(directory / metadata["filename"]),
    }


def publish_receipt_create_only(
    receipt: Mapping[str, Any],
    *,
    requests: Mapping[str, Mapping[str, Any]],
    responses: Mapping[str, Mapping[str, Any]],
    output_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PreparedCloneInventoryError(
            "receipt publication requires root:root"
        )
    validated = validate_aggregate(
        receipt,
        requests=requests,
        responses=responses,
        now=now,
    )
    path = canonical_receipt_path(
        output_root,
        operation_id=validated["operation_id"],
        controller_challenge_sha256=validated[
            "controller_challenge_sha256"
        ],
    )
    root_fd = _open_private_directory(Path(output_root), create=False)
    descriptors = [root_fd]
    try:
        for component in path.relative_to(output_root).parts[:-1]:
            descriptor = _open_private_child_directory(
                descriptors[-1],
                component,
                create=True,
            )
            descriptors.append(descriptor)
        directory = path.parent
        artifacts: dict[str, Any] = {}
        for role in ROLES:
            request_payload = canonical_json(requests[role]) + b"\n"
            response_payload = canonical_json(responses[role]) + b"\n"
            request_reference = _publish_or_reconcile_artifact(
                descriptors[-1],
                REQUEST_FILENAMES[role],
                request_payload,
            )
            response_reference = _publish_or_reconcile_artifact(
                descriptors[-1],
                RESPONSE_FILENAMES[role],
                response_payload,
            )
            artifacts[role] = {
                "request": _artifact_reference(
                    directory,
                    request_reference,
                ),
                "response": _artifact_reference(
                    directory,
                    response_reference,
                ),
            }
        aggregate_payload = canonical_json(validated) + b"\n"
        aggregate_reference = _publish_or_reconcile_artifact(
            descriptors[-1],
            path.name,
            aggregate_payload,
        )
        for descriptor in reversed(descriptors):
            try:
                os.fsync(descriptor)
            except OSError as exc:
                raise PreparedCloneInventoryError(
                    "published receipt directory sync failed"
                ) from exc
        loaded = load_pre_freeze_current_operation_receipt(
            path,
            output_root=output_root,
            now=now,
        )
        return {
            "schema": PUBLICATION_SCHEMA,
            "status": "published-create-only-readback-verified",
            "path": str(path),
            "sha256": _sha256(aggregate_payload),
            "bytes": len(aggregate_payload),
            "aggregate_sha256": validated["aggregate_sha256"],
            "controller_challenge_sha256": validated[
                "controller_challenge_sha256"
            ],
            "artifacts": artifacts,
            "aggregate": _artifact_reference(
                directory,
                aggregate_reference,
            ),
            "artifact_count": 7,
            "create_only": True,
            "readback_verified": loaded["readback_verified"],
        }
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error: OSError | None = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            message = "receipt publication directory close failed"
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(message)
            else:
                raise PreparedCloneInventoryError(message) from cleanup_error


def load_pre_freeze_current_operation_receipt(
    receipt_path: Path,
    *,
    output_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load one still-fresh receipt for the pre-freeze transition."""

    return _load_persisted_receipt(
        receipt_path,
        output_root=output_root,
        now=now,
        historical_expectations=None,
    )


def load_historical_running_prepared_clone_baseline_receipt(
    receipt_path: Path,
    *,
    output_root: Path,
    expected_campaign_id: str,
    expected_operation_id: str,
    expected_release_sha: str,
    expected_release_tree_sha: str,
    expected_controller_challenge_sha256: str,
    expected_aggregate_artifact_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reload an immutable expired running receipt only as a stopped baseline."""

    try:
        campaign_id = INVENTORY._canonical_uuid4(  # noqa: SLF001
            expected_campaign_id,
            label="expected campaign ID",
        )
        operation_id = INVENTORY._canonical_uuid4(  # noqa: SLF001
            expected_operation_id,
            label="expected operation ID",
        )
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(
            "historical baseline identity is invalid"
        ) from exc
    if (
        campaign_id == operation_id
        or not isinstance(expected_release_sha, str)
        or INVENTORY.SHA40_RE.fullmatch(expected_release_sha) is None
        or not isinstance(expected_release_tree_sha, str)
        or INVENTORY.SHA40_RE.fullmatch(expected_release_tree_sha) is None
    ):
        raise PreparedCloneInventoryError(
            "historical baseline release identity is invalid"
        )
    expectations = {
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": expected_release_sha,
        "release_tree_sha": expected_release_tree_sha,
        "controller_challenge_sha256": _nonzero_sha256(
            expected_controller_challenge_sha256,
            label="historical baseline controller challenge",
        ),
        "aggregate_artifact_sha256": _nonzero_sha256(
            expected_aggregate_artifact_sha256,
            label="historical baseline aggregate artifact",
        ),
    }
    return _load_persisted_receipt(
        receipt_path,
        output_root=output_root,
        now=now,
        historical_expectations=expectations,
    )


def _load_persisted_receipt(
    receipt_path: Path,
    *,
    output_root: Path,
    now: datetime | None,
    historical_expectations: Mapping[str, str] | None,
) -> dict[str, Any]:
    path = Path(receipt_path)
    root = Path(output_root)
    if (
        not path.is_absolute()
        or path != Path(os.path.abspath(path))
        or ".." in path.parts
        or path.name != PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
        or not root.is_absolute()
        or root != Path(os.path.abspath(root))
        or ".." in root.parts
    ):
        raise PreparedCloneInventoryError(
            "receipt path is not canonical"
        )
    challenge = _nonzero_sha256(
        path.parent.name,
        label="receipt path challenge",
    )
    try:
        operation_id = INVENTORY._canonical_uuid4(  # noqa: SLF001
            path.parent.parent.name,
            label="operation ID",
        )
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise PreparedCloneInventoryError(
            "receipt path operation ID is invalid"
        ) from exc
    if historical_expectations is not None and (
        operation_id != historical_expectations["operation_id"]
        or challenge
        != historical_expectations["controller_challenge_sha256"]
    ):
        raise PreparedCloneInventoryError(
            "historical baseline path identity differs"
        )
    expected_path = canonical_receipt_path(
        root,
        operation_id=operation_id,
        controller_challenge_sha256=challenge,
    )
    if path != expected_path:
        raise PreparedCloneInventoryError(
            "receipt path differs from its expected output root"
        )
    root_fd = _open_private_directory(root, create=False)
    descriptors = [root_fd]
    try:
        for component in (
            "prepared-clone-inventory",
            operation_id,
            challenge,
        ):
            descriptors.append(
                _open_private_child_directory(
                    descriptors[-1],
                    component,
                    create=False,
                )
            )
        directory_fd = descriptors[-1]
        expected_names = {
            PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME,
            *REQUEST_FILENAMES.values(),
            *RESPONSE_FILENAMES.values(),
        }
        try:
            observed_names = set(os.listdir(directory_fd))
        except OSError as exc:
            raise PreparedCloneInventoryError(
                "receipt artifact inventory is unavailable"
            ) from exc
        if observed_names != expected_names:
            raise PreparedCloneInventoryError(
                "receipt artifact filename closure differs"
            )
        requests: dict[str, dict[str, Any]] = {}
        responses: dict[str, dict[str, Any]] = {}
        references: dict[str, Any] = {}
        for role in ROLES:
            references[role] = {}
            for kind, filename, destination in (
                ("request", REQUEST_FILENAMES[role], requests),
                ("response", RESPONSE_FILENAMES[role], responses),
            ):
                payload = _read_private_artifact(
                    directory_fd,
                    filename,
                    maximum=INVENTORY.MAX_RESPONSE_BYTES + 1,
                )
                if (
                    not payload.endswith(b"\n")
                    or payload.count(b"\n") != 1
                ):
                    raise PreparedCloneInventoryError(
                        "receipt source bytes are not newline-delimited"
                    )
                document = _strict_json(
                    payload[:-1],
                    label=f"{role} receipt {kind}",
                )
                if payload != canonical_json(document) + b"\n":
                    raise PreparedCloneInventoryError(
                        "receipt source bytes are not canonical"
                    )
                destination[role] = document
                references[role][kind] = {
                    "filename": filename,
                    "path": str(path.parent / filename),
                    "sha256": _sha256(payload),
                    "bytes": len(payload),
                }
        aggregate_payload = _read_private_artifact(
            directory_fd,
            path.name,
            maximum=INVENTORY.MAX_RESPONSE_BYTES + 1,
        )
        if (
            not aggregate_payload.endswith(b"\n")
            or aggregate_payload.count(b"\n") != 1
        ):
            raise PreparedCloneInventoryError(
                "receipt aggregate bytes are not newline-delimited"
            )
        aggregate = _strict_json(
            aggregate_payload[:-1],
            label="prepared receipt aggregate",
        )
        if aggregate_payload != canonical_json(aggregate) + b"\n":
            raise PreparedCloneInventoryError(
                "receipt aggregate bytes are not canonical"
            )
        aggregate_artifact_sha256 = _sha256(aggregate_payload)
        if historical_expectations is None:
            validation_time = now
            loaded_schema = LOADED_RECEIPT_SCHEMA
            loaded_status = "loaded-readback-verified"
        else:
            current_time = (
                datetime.now(timezone.utc)
                if now is None
                else _utc_datetime(
                    now,
                    label="historical baseline current time",
                )
            )
            validation_time = _parse_timestamp(
                aggregate.get("controller_observed_at"),
                label="historical baseline controller_observed_at",
            )
            if (
                validation_time
                > current_time
                + timedelta(seconds=COMMAND_CLOCK_SKEW_SECONDS)
            ):
                raise PreparedCloneInventoryError(
                    "historical baseline controller time is in the future"
                )
        validated = validate_aggregate(
            aggregate,
            requests=requests,
            responses=responses,
            now=validation_time,
        )
        if (
            validated["operation_id"] != operation_id
            or validated["controller_challenge_sha256"] != challenge
        ):
            raise PreparedCloneInventoryError(
                "receipt directory identity differs from aggregate"
            )
        if historical_expectations is not None:
            expected_identity = {
                key: historical_expectations[key]
                for key in (
                    "campaign_id",
                    "operation_id",
                    "release_sha",
                    "release_tree_sha",
                    "controller_challenge_sha256",
                )
            }
            if (
                validated["expected_database_state"]
                != "running-healthy"
                or any(
                    validated[field] != expected
                    for field, expected in expected_identity.items()
                )
                or aggregate_artifact_sha256
                != historical_expectations[
                    "aggregate_artifact_sha256"
                ]
            ):
                raise PreparedCloneInventoryError(
                    "historical running baseline binding differs"
                )
            loaded_schema = HISTORICAL_BASELINE_LOADED_RECEIPT_SCHEMA
            loaded_status = (
                "loaded-historical-running-baseline-readback-verified"
            )
        return {
            "schema": loaded_schema,
            "status": loaded_status,
            "receipt": validated,
            "requests": requests,
            "responses": responses,
            "artifacts": references,
            "aggregate": {
                "filename": path.name,
                "path": str(path),
                "sha256": aggregate_artifact_sha256,
                "bytes": len(aggregate_payload),
            },
            "artifact_count": 7,
            "readback_verified": True,
        }
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            message = "receipt loader directory close failed"
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(message)
            elif not isinstance(cleanup_error, OSError):
                raise cleanup_error
            else:
                raise PreparedCloneInventoryError(
                    message
                ) from cleanup_error


class ControllerLiveness:
    def __init__(self, control_fd: int):
        self.control_fd = control_fd
        self._target = ""
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ControllerLiveness":
        try:
            metadata = os.fstat(self.control_fd)
            flags = fcntl.fcntl(self.control_fd, fcntl.F_GETFL)
            target = os.readlink(f"/proc/self/fd/{self.control_fd}")
        except OSError as exc:
            raise PreparedCloneInventoryError(
                "controller liveness descriptor is unavailable"
            ) from exc
        if (
            not stat.S_ISFIFO(metadata.st_mode)
            or flags & os.O_ACCMODE != os.O_RDONLY
            or re.fullmatch(r"pipe:\[[0-9]+\]", target) is None
        ):
            raise PreparedCloneInventoryError(
                "controller liveness must be an anonymous read-only pipe"
            )
        self._target = target
        try:
            candidates = os.listdir("/proc/self/fd")
        except OSError as exc:
            raise PreparedCloneInventoryError(
                "controller descriptor inventory is unavailable"
            ) from exc
        for candidate in candidates:
            if not candidate.isdigit() or int(candidate) == self.control_fd:
                continue
            descriptor = int(candidate)
            try:
                if os.readlink(f"/proc/self/fd/{descriptor}") != target:
                    continue
                candidate_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            except OSError:
                continue
            if candidate_flags & os.O_ACCMODE != os.O_RDONLY:
                raise PreparedCloneInventoryError(
                    "controller process retains a liveness writer"
                )
        poller = select.poll()
        poller.register(
            self.control_fd,
            select.POLLIN
            | select.POLLHUP
            | select.POLLERR
            | select.POLLNVAL,
        )
        events = poller.poll(0)
        if any(
            mask & (select.POLLHUP | select.POLLERR | select.POLLNVAL)
            for _, mask in events
        ):
            raise PreparedCloneInventoryError(
                "controller liveness was lost before collection"
            )
        os.set_blocking(self.control_fd, False)
        if any(mask & select.POLLIN for _, mask in events):
            try:
                initial = os.read(self.control_fd, 1)
            except BlockingIOError:
                initial = None
            except OSError as exc:
                raise PreparedCloneInventoryError(
                    "controller liveness cannot be read"
                ) from exc
            if initial is not None:
                raise PreparedCloneInventoryError(
                    "controller liveness was lost before collection"
                )
        self._thread = threading.Thread(
            target=self._watch,
            name="prepared-inventory-controller-liveness",
            daemon=True,
        )
        self._thread.start()
        return self

    def _watch(self) -> None:
        poller = select.poll()
        poller.register(
            self.control_fd,
            select.POLLIN
            | select.POLLHUP
            | select.POLLERR
            | select.POLLNVAL,
        )
        while not self._stop.is_set():
            try:
                events = poller.poll(100)
            except OSError:
                events = [(self.control_fd, select.POLLERR)]
            for _, mask in events:
                if mask & (
                    select.POLLHUP | select.POLLERR | select.POLLNVAL
                ):
                    self._lost.set()
                    os.kill(os.getpid(), signal.SIGUSR1)
                    return
                if mask & select.POLLIN:
                    try:
                        payload = os.read(self.control_fd, 1)
                    except BlockingIOError:
                        continue
                    except OSError:
                        payload = b""
                    if payload == b"":
                        self._lost.set()
                        os.kill(os.getpid(), signal.SIGUSR1)
                        return
                    self._lost.set()
                    os.kill(os.getpid(), signal.SIGUSR1)
                    return

    def check(self) -> None:
        if self._lost.is_set():
            raise PreparedCloneInventoryCancellation(
                "controller liveness was lost"
            )

    def __exit__(self, exc_type, exc, traceback) -> bool:
        cleanup_error: BaseException | None = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                cleanup_error = PreparedCloneInventoryError(
                    "controller liveness watcher did not stop"
                )
        try:
            os.close(self.control_fd)
        except OSError as close_error:
            if cleanup_error is None:
                cleanup_error = PreparedCloneInventoryError(
                    "controller liveness descriptor could not be closed"
                )
                cleanup_error.__cause__ = close_error
        if cleanup_error is not None:
            if exc is not None:
                if hasattr(exc, "add_note"):
                    exc.add_note(str(cleanup_error))
                return False
            raise cleanup_error
        return False


@contextmanager
def _signal_authority() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise PreparedCloneInventoryError(
            "live collection must run in the main thread"
        )
    watched = (
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGHUP,
        signal.SIGUSR1,
    )
    previous: dict[int, Any] = {}

    def cancel(signum: int, _frame: Any) -> None:
        raise PreparedCloneInventoryCancellation(
            f"prepared inventory received signal {signum}"
        )

    try:
        for signum in watched:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, cancel)
    except BaseException:
        for signum, handler in reversed(tuple(previous.items())):
            signal.signal(signum, handler)
        raise
    try:
        yield
    finally:
        restore_error: BaseException | None = None
        for signum, handler in reversed(tuple(previous.items())):
            try:
                signal.signal(signum, handler)
            except BaseException as exc:
                if restore_error is None:
                    restore_error = exc
        if restore_error is not None and sys.exc_info()[0] is None:
            raise restore_error


def collect(
    inputs: CollectionInputs,
    *,
    invoke: InventoryInvoker,
    confirm: str,
    controller_liveness_fd: int | None,
    authorization_check: AuthorizationCheck | None = None,
    clock: Callable[[], datetime] | None = None,
    active_liveness: ControllerLiveness | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    inputs = _validate_inputs(inputs)
    plan = build_plan(inputs)
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PreparedCloneInventoryError(
            "live prepared inventory collection requires root:root"
        )
    if confirm != plan["required_confirmation"]:
        raise PreparedCloneInventoryError(
            "live collection requires exact digest-bound confirmation"
        )
    if not callable(invoke):
        raise PreparedCloneInventoryError(
            "prepared inventory invoker is unavailable"
        )
    active_clock = clock or (lambda: datetime.now(timezone.utc))
    if active_liveness is not None:
        if (
            not isinstance(active_liveness, ControllerLiveness)
            or controller_liveness_fd is not None
        ):
            raise PreparedCloneInventoryError(
                "active controller liveness authority is invalid"
            )
        return _collect_with_active_authority(
            inputs,
            invoke=invoke,
            liveness=active_liveness,
            authorization_check=authorization_check,
            clock=active_clock,
        )
    if (
        isinstance(controller_liveness_fd, bool)
        or not isinstance(controller_liveness_fd, int)
        or controller_liveness_fd < 0
    ):
        raise PreparedCloneInventoryError(
            "live collection requires an anonymous controller-liveness pipe"
        )
    with _signal_authority():
        with ControllerLiveness(controller_liveness_fd) as liveness:
            return _collect_with_active_authority(
                inputs,
                invoke=invoke,
                liveness=liveness,
                authorization_check=authorization_check,
                clock=active_clock,
            )


def _collect_with_active_authority(
    inputs: CollectionInputs,
    *,
    invoke: InventoryInvoker,
    liveness: ControllerLiveness,
    authorization_check: AuthorizationCheck | None,
    clock: Callable[[], datetime],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    liveness.check()
    _verify_authorization(inputs, authorization_check)
    challenge = INVENTORY.new_controller_challenge()
    issued_at = _utc_datetime(
        clock(),
        label="collection issue time",
    )
    expires_at = issued_at + timedelta(
        seconds=REQUEST_LIFETIME_SECONDS
    )
    requests = _request_set(
        inputs,
        challenge=challenge,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    responses: dict[str, dict[str, Any]] = {}
    command_times: dict[str, tuple[datetime, datetime]] = {}
    for role in ROLES:
        liveness.check()
        _verify_authorization(inputs, authorization_check)
        started = _utc_datetime(
            clock(),
            label=f"{role} command start",
        )
        try:
            raw = invoke(role, requests[role])
        except PreparedCloneInventoryCancellation:
            raise
        except Exception as exc:
            raise PreparedCloneInventoryError(
                f"{role} prepared inventory invocation failed"
            ) from exc
        completed = _utc_datetime(
            clock(),
            label=f"{role} command completion",
        )
        liveness.check()
        bounded = _validate_invocation_result(raw, role=role)
        document = _strict_json(
            bounded.stdout[:-1],
            label=f"{role} prepared response",
        )
        if bounded.stdout != canonical_json(document) + b"\n":
            raise PreparedCloneInventoryError(
                f"{role} prepared response is not canonical"
            )
        try:
            responses[role] = INVENTORY.validate_prepared_response(
                document,
                request=requests[role],
                now=completed,
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise PreparedCloneInventoryError(
                f"{role} prepared response failed validation"
            ) from exc
        command_times[role] = (started, completed)
    liveness.check()
    _verify_authorization(inputs, authorization_check)
    completed_at = _utc_datetime(
        clock(),
        label="collection completion time",
    )
    aggregate = build_aggregate(
        inputs=inputs,
        requests=requests,
        responses=responses,
        command_times=command_times,
        now=completed_at,
    )
    liveness.check()
    return aggregate, requests, responses


class ProductionInvoker:
    """Concrete bounded local/SSH inventory invoker."""

    def __init__(
        self,
        *,
        ssh_identity: Path,
        ssh_identity_sha256: str,
        known_hosts: Path,
        known_hosts_sha256: str,
        runner: Callable[[Any], Any] = RUNNER.run_bounded_process,
    ):
        self.ssh_identity = self._absolute_trust_path(
            ssh_identity,
            label="SSH identity",
        )
        self.known_hosts = self._absolute_trust_path(
            known_hosts,
            label="SSH known-hosts",
        )
        self.ssh_identity_sha256 = _nonzero_sha256(
            ssh_identity_sha256,
            label="SSH identity",
        )
        self.known_hosts_sha256 = _nonzero_sha256(
            known_hosts_sha256,
            label="SSH known-hosts",
        )
        if self.ssh_identity == self.known_hosts or not callable(runner):
            raise PreparedCloneInventoryError(
                "production SSH trust binding is invalid"
            )
        self.runner = runner

    @staticmethod
    def _absolute_trust_path(path: Path, *, label: str) -> Path:
        parsed = Path(path)
        if (
            not parsed.is_absolute()
            or parsed != Path(os.path.abspath(parsed))
            or ".." in parsed.parts
        ):
            raise PreparedCloneInventoryError(
                f"{label} path is not absolute and normalized"
            )
        return parsed

    def _verify_ssh_trust(self) -> None:
        if os.geteuid() != 0 or os.getegid() != 0:
            raise PreparedCloneInventoryError(
                "production SSH trust requires root:root"
            )
        for path, expected, label in (
            (
                self.ssh_identity,
                self.ssh_identity_sha256,
                "SSH identity",
            ),
            (
                self.known_hosts,
                self.known_hosts_sha256,
                "SSH known-hosts",
            ),
        ):
            try:
                payload = read_secure_bytes(
                    path,
                    label=label,
                    owner_uid=0,
                    max_size=MAX_SSH_TRUST_BYTES,
                )
            except SecureFileError as exc:
                raise PreparedCloneInventoryError(
                    f"{label} is unavailable or unsafe"
                ) from exc
            if not payload or _sha256(payload) != expected:
                raise PreparedCloneInventoryError(
                    f"{label} digest differs"
                )

    @staticmethod
    def _endpoint(role: str) -> tuple[str, int]:
        if role not in ROLES:
            raise PreparedCloneInventoryError(
                "production endpoint role is invalid"
            )
        host = INVENTORY.ROLE_HOSTS[role]
        canonical_hosts = RUNNER.RESTORE.ROLE_HOSTS
        canonical_ports = RUNNER.RESTORE.ROLE_PORTS
        port = canonical_ports.get(role)
        if (
            canonical_hosts.get(role) != host
            or type(port) is not int
            or not 1 <= port <= 65535
        ):
            raise PreparedCloneInventoryError(
                f"{role} production endpoint topology differs"
            )
        return host, port

    def _argv(self, role: str, request: Mapping[str, Any]) -> tuple[str, ...]:
        host = (
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "HOME=/root",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3",
            "-I",
            "-B",
            request["agent_path"],
            "--host-stdio",
        )
        if role == "bot_fi":
            return host
        expected_host, port = self._endpoint(role)
        if request["expected_host"] != expected_host:
            raise PreparedCloneInventoryError(
                f"{role} production host substitution was refused"
            )
        self._verify_ssh_trust()
        remote = " ".join(
            "'" + item.replace("'", "'\"'\"'") + "'" for item in host
        )
        return (
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-T",
            "-p",
            str(port),
            "-i",
            str(self.ssh_identity),
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
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            f"root@{request['expected_host']}",
            remote,
        )

    def __call__(
        self,
        role: str,
        request: Mapping[str, Any],
    ) -> InvocationResult:
        validated = INVENTORY.validate_prepared_request(request)
        if validated["role"] != role:
            raise PreparedCloneInventoryError(
                "production invocation role differs"
            )
        control = RUNNER.InventoryControl(
            role=role,
            argv=self._argv(role, validated),
            stdin=canonical_json(validated) + b"\n",
            max_stdout_bytes=INVENTORY.MAX_RESPONSE_BYTES + 1,
            max_stderr_bytes=MAX_STDERR_BYTES,
            timeout_seconds=CONTROL_TIMEOUT_SECONDS,
            start_new_session=True,
            terminate_process_group_on_exit=True,
            kill_process_group_after_seconds=PROCESS_TERM_GRACE_SECONDS,
            application_payload_bytes_over_ssh=0,
        )
        try:
            result = RUNNER._invoke_bounded_process(  # noqa: SLF001
                self.runner,
                control,
                label=f"{role} prepared inventory",
            )
        except RUNNER.RestorePhaseCoordinatorError as exc:
            raise PreparedCloneInventoryError(
                f"{role} bounded production invocation failed"
            ) from exc
        return InvocationResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            stdout_limit_exceeded=result.stdout_limit_exceeded,
            stderr_limit_exceeded=result.stderr_limit_exceeded,
            process_group_cleanup_performed=(
                result.process_group_cleanup_performed
            ),
        )


def _validate_controller_receipt(
    context: ControllerContext,
    loaded: Mapping[str, Any],
    *,
    expected_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(loaded, Mapping)
        or loaded.get("schema") != LOADED_RECEIPT_SCHEMA
        or loaded.get("status") != "loaded-readback-verified"
        or loaded.get("artifact_count") != 7
        or loaded.get("readback_verified") is not True
        or not isinstance(loaded.get("receipt"), Mapping)
        or not isinstance(loaded.get("requests"), Mapping)
        or not isinstance(loaded.get("responses"), Mapping)
        or set(loaded["requests"]) != set(ROLES)
        or set(loaded["responses"]) != set(ROLES)
        or not isinstance(loaded.get("aggregate"), Mapping)
    ):
        raise PreparedCloneInventoryError(
            "controller receipt readback closure differs"
        )
    receipt = dict(loaded["receipt"])
    inputs = context.collection_inputs
    expected_identity = {
        "campaign_id": inputs.campaign_id,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "expected_database_state": inputs.expected_database_state,
    }
    if (
        any(
            receipt.get(field) != expected
            for field, expected in expected_identity.items()
        )
        or receipt.get("collection_performed") is not True
        or receipt.get("production_contacted") is not True
        or receipt.get("docker_read_only") is not True
        or receipt.get("application_payload_bytes_over_ssh") != 0
    ):
        raise PreparedCloneInventoryError(
            "controller receipt identity or safety boundary differs"
        )
    for role in ROLES:
        request = loaded["requests"][role]
        response = loaded["responses"][role]
        if (
            not isinstance(request, Mapping)
            or not isinstance(response, Mapping)
            or not _request_matches_inputs(inputs, role, request)
            or response.get("role") != role
            or response.get("request_binding_sha256")
            != request.get("request_binding_sha256")
            or response.get("role_manifest_sha256")
            != request.get("role_manifest_sha256")
        ):
            raise PreparedCloneInventoryError(
                f"{role} controller receipt binding differs"
            )
    aggregate_reference = loaded["aggregate"]
    if (
        not isinstance(aggregate_reference.get("sha256"), str)
        or INVENTORY.SHA256_RE.fullmatch(
            aggregate_reference["sha256"]
        )
        is None
        or (
            expected_artifact_sha256 is not None
            and aggregate_reference["sha256"]
            != expected_artifact_sha256
        )
    ):
        raise PreparedCloneInventoryError(
            "controller receipt artifact digest differs"
        )
    return receipt


def _runtime_authorization_check(context: ControllerContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            dict(context.manifest),
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreparedCloneInventoryError(
            "production cutover authorization is invalid or expired"
        ) from exc


def _assert_controller_sources_unchanged(
    context: ControllerContext,
) -> None:
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(
            context.manifest_path
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreparedCloneInventoryError(
            "production cutover manifest changed or became unsafe"
        ) from exc
    if (
        manifest_sha256 != context.manifest_sha256
        or manifest != context.manifest
    ):
        raise PreparedCloneInventoryError(
            "production cutover manifest changed after planning"
        )
    if (
        _load_release_closure(
            context.release_closure_path,
            manifest=context.manifest,
        )
        != context.release_closure_sha256
    ):
        raise PreparedCloneInventoryError(
            "sealed release closure changed after planning"
        )
    prepare_document, _prepare_payload, prepare_sha256 = (
        _read_private_canonical_json(
            context.prepare_metadata_path,
            label="prepare material metadata",
        )
    )
    _validate_prepare_metadata(
        prepare_document,
        manifest=context.manifest,
    )
    if prepare_sha256 != context.prepare_metadata_sha256:
        raise PreparedCloneInventoryError(
            "prepare material metadata changed after planning"
        )
    release_root = (
        Path(context.manifest["deployment"]["shadow_root"])
        / "releases"
        / context.collection_inputs.release_sha
    )
    artifact_sha256, git_blobs = _verify_immutable_release_checkout(
        release_root,
        release_sha=context.collection_inputs.release_sha,
        release_tree_sha=context.collection_inputs.release_tree_sha,
    )
    if (
        artifact_sha256 != context.release_artifact_sha256
        or git_blobs != context.release_artifact_git_blob
    ):
        raise PreparedCloneInventoryError(
            "release artifacts changed after planning"
        )
    for path, expected, label in (
        (
            context.ssh_identity,
            context.ssh_identity_sha256,
            "SSH identity",
        ),
        (
            context.known_hosts,
            context.known_hosts_sha256,
            "SSH known-hosts",
        ),
    ):
        payload = _read_root_private_bytes(
            path,
            label=label,
            maximum=MAX_SSH_TRUST_BYTES,
        )
        if _sha256(payload) != expected:
            raise PreparedCloneInventoryError(
                f"{label} changed after planning"
            )
    if context.collection_inputs.expected_database_state == "stopped":
        prior = context.prior_running_receipt
        if not isinstance(prior, Mapping):
            raise PreparedCloneInventoryError(
                "stopped controller prior receipt binding is absent"
            )
        try:
            loaded_prior = (
                load_historical_running_prepared_clone_baseline_receipt(
                    Path(prior["path"]),
                    output_root=context.output_root,
                    expected_campaign_id=(
                        context.collection_inputs.campaign_id
                    ),
                    expected_operation_id=(
                        context.collection_inputs.operation_id
                    ),
                    expected_release_sha=(
                        context.collection_inputs.release_sha
                    ),
                    expected_release_tree_sha=(
                        context.collection_inputs.release_tree_sha
                    ),
                    expected_controller_challenge_sha256=(
                        prior["controller_challenge_sha256"]
                    ),
                    expected_aggregate_artifact_sha256=(
                        prior["aggregate_artifact_sha256"]
                    ),
                )
            )
        except PreparedCloneInventoryError as exc:
            raise PreparedCloneInventoryError(
                "prior running receipt changed after planning"
            ) from exc
        if (
            loaded_prior["receipt"]["aggregate_sha256"]
            != prior["aggregate_sha256"]
            or loaded_prior["requests"]
            != context.collection_inputs.prior_requests
            or loaded_prior["responses"]
            != context.collection_inputs.prior_responses
        ):
            raise PreparedCloneInventoryError(
                "prior running receipt bytes changed after planning"
            )
    _assert_private_output_root(context.output_root)


def _publish_with_exact_reconciliation(
    receipt: Mapping[str, Any],
    *,
    requests: Mapping[str, Mapping[str, Any]],
    responses: Mapping[str, Mapping[str, Any]],
    output_root: Path,
    now: datetime,
) -> dict[str, Any]:
    try:
        return publish_receipt_create_only(
            receipt,
            requests=requests,
            responses=responses,
            output_root=output_root,
            now=now,
        )
    except PreparedCloneInventoryError as first_error:
        try:
            return publish_receipt_create_only(
                receipt,
                requests=requests,
                responses=responses,
                output_root=output_root,
                now=now,
            )
        except PreparedCloneInventoryError as second_error:
            if hasattr(second_error, "add_note"):
                second_error.add_note(
                    "exact create-only publication reconciliation failed"
                )
                second_error.add_note(
                    f"initial publication error: {first_error}"
                )
            raise


def execute_controller(
    context: ControllerContext,
    *,
    apply: bool = False,
    confirm: str | None = None,
    controller_liveness_fd: int | None = None,
    resume_receipt: Path | None = None,
    resume_receipt_sha256: str | None = None,
    invoker_factory: Callable[..., InventoryInvoker] = ProductionInvoker,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    plan = build_controller_plan(
        context,
        resume_receipt=resume_receipt,
        resume_receipt_sha256=resume_receipt_sha256,
    )
    if not apply:
        if confirm is not None or controller_liveness_fd is not None:
            raise PreparedCloneInventoryError(
                "plan mode does not accept apply authority"
            )
        return plan
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PreparedCloneInventoryError(
            "prepared inventory controller apply requires root:root"
        )
    if threading.current_thread() is not threading.main_thread():
        raise PreparedCloneInventoryError(
            "prepared inventory controller apply must run in the main thread"
        )
    if confirm != plan["required_confirmation"]:
        raise PreparedCloneInventoryError(
            "controller apply requires exact digest-bound confirmation"
        )
    if (
        isinstance(controller_liveness_fd, bool)
        or not isinstance(controller_liveness_fd, int)
        or controller_liveness_fd < 0
    ):
        raise PreparedCloneInventoryError(
            "apply requires an anonymous controller-liveness pipe"
        )
    active_clock = clock or (lambda: datetime.now(timezone.utc))
    resume = plan["resume_receipt"]
    if resume is not None:
        with _signal_authority():
            with ControllerLiveness(controller_liveness_fd) as liveness:
                liveness.check()
                _assert_controller_sources_unchanged(context)
                liveness.check()
                _runtime_authorization_check(context)
                loaded = load_pre_freeze_current_operation_receipt(
                    Path(resume["path"]),
                    output_root=context.output_root,
                    now=_utc_datetime(
                        active_clock(),
                        label="resume readback time",
                    ),
                )
                liveness.check()
                receipt = _validate_controller_receipt(
                    context,
                    loaded,
                    expected_artifact_sha256=resume["sha256"],
                )
                _runtime_authorization_check(context)
                liveness.check()
        return {
            "schema": RESULT_SCHEMA,
            "status": "resumed-readback-verified",
            "mode": "resume-readback",
            "campaign_id": context.collection_inputs.campaign_id,
            "operation_id": context.collection_inputs.operation_id,
            "release_sha": context.collection_inputs.release_sha,
            "release_tree_sha": context.collection_inputs.release_tree_sha,
            "manifest_sha256": context.manifest_sha256,
            "plan_sha256": plan["plan_sha256"],
            "receipt_path": resume["path"],
            "receipt_sha256": loaded["aggregate"]["sha256"],
            "receipt": receipt,
            "artifact_count": 7,
            "create_only": True,
            "readback_verified": True,
            "collection_performed": False,
            "production_contacted": False,
        }
    try:
        invoker = invoker_factory(
            ssh_identity=context.ssh_identity,
            ssh_identity_sha256=context.ssh_identity_sha256,
            known_hosts=context.known_hosts,
            known_hosts_sha256=context.known_hosts_sha256,
        )
    except PreparedCloneInventoryError:
        raise
    except Exception as exc:
        raise PreparedCloneInventoryError(
            "production inventory invoker could not be constructed"
        ) from exc
    with _signal_authority():
        with ControllerLiveness(controller_liveness_fd) as liveness:
            liveness.check()
            _assert_controller_sources_unchanged(context)
            liveness.check()
            aggregate, requests, responses = collect(
                context.collection_inputs,
                invoke=invoker,
                confirm=plan["collection_plan"][
                    "required_confirmation"
                ],
                controller_liveness_fd=None,
                authorization_check=(
                    lambda: _runtime_authorization_check(context)
                ),
                clock=active_clock,
                active_liveness=liveness,
            )
            liveness.check()
            _runtime_authorization_check(context)
            publication_time = _utc_datetime(
                active_clock(),
                label="controller receipt publication time",
            )
            expires_at = _parse_timestamp(
                aggregate["expires_at"],
                label="controller receipt expiry",
            )
            if publication_time > expires_at:
                raise PreparedCloneInventoryError(
                    "prepared inventory receipt expired before publication"
                )
            publication = _publish_with_exact_reconciliation(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=context.output_root,
                now=publication_time,
            )
            liveness.check()
            readback_time = _utc_datetime(
                active_clock(),
                label="controller receipt readback time",
            )
            if readback_time > expires_at:
                raise PreparedCloneInventoryError(
                    "prepared inventory receipt expired before final readback"
                )
            loaded = load_pre_freeze_current_operation_receipt(
                Path(publication["path"]),
                output_root=context.output_root,
                now=readback_time,
            )
            liveness.check()
            receipt = _validate_controller_receipt(
                context,
                loaded,
                expected_artifact_sha256=publication["sha256"],
            )
            _runtime_authorization_check(context)
            liveness.check()
    return {
        "schema": RESULT_SCHEMA,
        "status": "completed",
        "mode": "fresh-collect",
        "campaign_id": context.collection_inputs.campaign_id,
        "operation_id": context.collection_inputs.operation_id,
        "release_sha": context.collection_inputs.release_sha,
        "release_tree_sha": context.collection_inputs.release_tree_sha,
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": plan["plan_sha256"],
        "receipt_path": publication["path"],
        "receipt_sha256": publication["sha256"],
        "receipt": receipt,
        "publication": publication,
        "artifact_count": publication["artifact_count"],
        "create_only": publication["create_only"],
        "readback_verified": publication["readback_verified"],
        "collection_performed": True,
        "production_contacted": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--release-closure", type=Path, required=True)
    parser.add_argument("--prepare-metadata", type=Path, required=True)
    parser.add_argument("--ssh-identity", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument(
        "--expected-database-state",
        choices=("running-healthy", "stopped"),
        default="running-healthy",
    )
    parser.add_argument("--prior-running-receipt", type=Path)
    parser.add_argument("--prior-running-challenge-sha256")
    parser.add_argument("--prior-running-receipt-sha256")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--controller-liveness-fd", type=int)
    parser.add_argument("--resume-receipt", type=Path)
    parser.add_argument("--resume-receipt-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        context = load_controller_context(
            manifest_path=args.manifest,
            approval_path=args.approval,
            approval_policy_path=args.approval_policy,
            release_closure_path=args.release_closure,
            prepare_metadata_path=args.prepare_metadata,
            ssh_identity=args.ssh_identity,
            known_hosts=args.known_hosts,
            expected_database_state=args.expected_database_state,
            prior_running_receipt=args.prior_running_receipt,
            prior_running_challenge_sha256=(
                args.prior_running_challenge_sha256
            ),
            prior_running_receipt_sha256=(
                args.prior_running_receipt_sha256
            ),
        )
        result = execute_controller(
            context,
            apply=args.apply,
            confirm=args.confirm,
            controller_liveness_fd=args.controller_liveness_fd,
            resume_receipt=args.resume_receipt,
            resume_receipt_sha256=args.resume_receipt_sha256,
        )
        status = 0
    except PreparedCloneInventoryError as exc:
        may_have_contacted = bool(
            args.apply and args.resume_receipt is None
        )
        result = {
            "schema": RESULT_SCHEMA,
            "status": "blocked",
            "error": str(exc),
            "error_class": type(exc).__name__,
            "reconciliation_required": may_have_contacted,
            "collection_performed": (
                None if may_have_contacted else False
            ),
            "production_contacted": (
                None if may_have_contacted else False
            ),
        }
        status = 2
    sys.stdout.buffer.write(canonical_json(result) + b"\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
