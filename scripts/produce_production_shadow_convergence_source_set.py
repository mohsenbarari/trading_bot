#!/usr/bin/env python3
"""Build controller-local production-shadow convergence inputs from role reads.

This producer consumes only pre-installed, root-only, digest-addressed role
requests, redacted role attestations, and transport receipts.  It performs no
SSH, Docker, Object Storage, database, or peer-network operation.  In
particular, it cannot turn a controller-supplied boolean into convergence
evidence: database and DR values are recomputed from the redacted records
returned by the exact-release role workers.

Bot-FI is local and WebApp-FI may return a redacted JSON attestation over its
trusted SSH control path.  WebApp-IR and Witness use only an age-encrypted,
private/versioned Arvan Object Storage artifact path.  A controller-local
receipt for either Object Storage route is *not* remote-observation proof:
it becomes one only after a root-only policy and signed receiver attestation
are both bound to an immutable manifest policy contract.  There is no FI-to-IR
artifact route.

The current worker can truthfully reduce local database and DR reads.  This
module never publishes the ``ready`` source-set that the convergence gate
accepts while the controller producer itself lacks an exact-release execution
contract.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import SecureFileError, read_secure_bytes, write_secure_new_bytes  # noqa: E402
from core.sync_parity import business_snapshot_fingerprint, compare_parity_snapshots  # noqa: E402
from scripts import orchestrate_production_shadow_convergence_gate as BRIDGE  # noqa: E402
from scripts import production_shadow_convergence_observer_worker as WORKER  # noqa: E402
from scripts import production_shadow_convergence_blob_roundtrip as BLOB_ROUNDTRIP  # noqa: E402
from scripts import production_shadow_convergence_dr_tls as DR_TLS  # noqa: E402
from scripts import production_shadow_convergence_witness_live as WITNESS_LIVE  # noqa: E402
from scripts import production_shadow_destination_firewall_observation as DESTINATION_FIREWALL  # noqa: E402
from scripts import production_shadow_queue_state_observation as QUEUE_STATE  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import production_shadow_ed25519_verifier as ED25519  # noqa: E402
from scripts import production_shadow_remote_receiver_provenance as REMOTE_PROVENANCE  # noqa: E402
from scripts import production_shadow_remote_receiver_signing_policy as RECEIVER_POLICY  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402
from scripts.wa_ir_production_transport_contract import (  # noqa: E402
    PRODUCTION_BUCKET,
    ProductionTransportError,
    validate_object_key_binding,
)


PLAN_SCHEMA = "production-shadow-convergence-source-set-producer-plan-v1"
READY_SOURCE_SET_PLAN_SCHEMA = "production-shadow-convergence-ready-source-set-plan-v1"
TRANSPORT_RECEIPT_SCHEMA = "production-shadow-convergence-observation-transport-receipt-v1"
AVAILABILITY_SCHEMA = "production-shadow-convergence-observation-source-set-availability-v1"
CONVERGENCE_ROLE_VALIDATION_SCHEMA = (
    "production-shadow-convergence-role-validation-v1"
)

PHASE = BRIDGE.PHASE
OPERATION = BRIDGE.OPERATION
ROLES = BRIDGE.ROLES
RUNTIME_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
PURE_OBSERVATIONS = (
    "blob_roundtrip",
    "queue_state",
    "dr_tls",
    "destination_firewall",
    "witness_live",
)
SUPPORTED_OBSERVATIONS = tuple(BRIDGE.SOURCE_LABELS)
MISSING_OBSERVATIONS = tuple(
    label for label in BRIDGE.SOURCE_LABELS if label not in SUPPORTED_OBSERVATIONS
)

OUTPUT_FILE_MODE = BRIDGE.OUTPUT_FILE_MODE
MAX_JSON_BYTES = BRIDGE.MAX_JSON_BYTES
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64

INCOMING_DIRECTORY = "incoming"
INCOMING_KINDS = (
    "requests",
    "attestations",
    "transport-receipts",
    "remote-receiver-policies",
    "remote-receiver-signed-attestations",
    "pure-observations",
)
TRANSPORT_BY_ROLE = {
    "bot_fi": "controller-local-root-only",
    "webapp_fi": "trusted-ssh-redacted-attestation",
    "webapp_ir": "object-storage-private-versioned-age",
    "witness": "object-storage-private-versioned-age",
}
OBJECT_STORAGE_ROLES = frozenset(
    role for role, transport in TRANSPORT_BY_ROLE.items()
    if transport == "object-storage-private-versioned-age"
)
REMOTE_RECEIVER_POLICY_CONTRACT_FIELDS = frozenset(
    {
        "policy_file_sha256",
        "policy_sha256",
        "key_id",
        "public_key_sha256",
        "receiver_sha256",
        "worker_sha256",
    }
)

REMOTE_RECEIVER_ATTESTATION_SCHEMA = REMOTE_PROVENANCE.PROVENANCE_SCHEMA
REMOTE_RECEIVER_ATTESTATION_FIELDS = REMOTE_PROVENANCE.PROVENANCE_FIELDS

TRANSPORT_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "request_sha256",
        "attestation_sha256",
        "attestation_file_sha256",
        "transport",
        "payload_class",
        "transport_detail",
        "remote_receiver_attestation",
        "remote_receiver_policy_file_sha256",
        "remote_receiver_signed_attestation_file_sha256",
        "received_at",
        "direct_fi_to_ir_transfer",
        "transport_receipt_sha256",
    }
)
CONVERGENCE_ROLE_VALIDATION_FIELDS = frozenset(
    set(VERIFY.HOST_AGENT_VALIDATION_FIELDS)
    | {
        "worker_request",
        "worker_attestation",
            "transport_receipt",
            "host_identity_proof_sha256",
            "compose_execution",
            "provenance_closure_sha256",
    }
)
AVAILABILITY_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "phase_started_at",
        "captured_at",
        "role_inputs",
        "pure_observation_inputs",
        "role_validation",
        "produced_observations",
        "unavailable_observations",
        "ready_source_set_publication_blocker",
        "source_available",
        "bridge_ready_source_set_published",
        "direct_fi_to_ir_transfer_observed",
        "producer_network_io",
        "producer_docker_io",
        "producer_ssh_io",
        "availability_binding_sha256",
    }
)


class ConvergenceSourceSetProducerError(RuntimeError):
    """The controller cannot safely assemble production observation inputs."""


class ConvergenceSourceSetUnavailable(ConvergenceSourceSetProducerError):
    """A required independent remote observation provenance is not available."""


class ControllerProducerExactReleaseUnavailable(ConvergenceSourceSetUnavailable):
    """The local controller producer has no exact-release execution proof."""


CONTROLLER_PRODUCER_EXACT_RELEASE_REQUIREMENT = (
    "exact-release-bound-controller-convergence-producer"
)
CONTROLLER_PRODUCER_RELATIVE_PATH = (
    "scripts/produce_production_shadow_convergence_source_set.py"
)
CONTROLLER_PRODUCER_LAUNCHER_RELATIVE_PATH = (
    "scripts/production_shadow_convergence_source_set_launcher"
)
CONTROLLER_PRODUCER_RELEASE_ROOT_FD_ENV = (
    "PRODUCTION_SHADOW_HELD_CONVERGENCE_PRODUCER_RELEASE_ROOT_FD"
)
CONTROLLER_PRODUCER_FD_ENV = "PRODUCTION_SHADOW_HELD_CONVERGENCE_PRODUCER_FD"
CONTROLLER_PRODUCER_LAUNCHER_FD_ENV = (
    "PRODUCTION_SHADOW_HELD_CONVERGENCE_PRODUCER_LAUNCHER_FD"
)
CONTROLLER_PRODUCER_MAX_SOURCE_BYTES = 16 * 1024 * 1024
CONTROLLER_PRODUCER_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_PAGER": "cat",
}


@dataclass(frozen=True)
class Ingress:
    role: str
    request: BRIDGE.SecureRecord
    attestation: BRIDGE.SecureRecord
    receipt: BRIDGE.SecureRecord
    observed_at: datetime
    captured_at: datetime | None


@dataclass(frozen=True)
class PreparedReadySourceSet:
    context: BRIDGE.EvidenceContext
    role_validation: Mapping[str, BRIDGE.Reference]
    observations: Mapping[str, BRIDGE.Reference]
    document: dict[str, Any]
    payload: bytes
    sha256: str
    output: Path
    required_confirmation: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceSourceSetProducerError("JSON document has duplicate fields")
        result[key] = value
    return result


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
        raise ConvergenceSourceSetProducerError("value is not canonical JSON") from exc


def _sha256(value: bytes | Mapping[str, Any] | list[Any]) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise ConvergenceSourceSetProducerError(f"{label} must be a nonzero SHA-256")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ConvergenceSourceSetProducerError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConvergenceSourceSetProducerError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConvergenceSourceSetProducerError(f"{label} lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utcnow() -> datetime:
    """Production clock seam; tests patch this private helper only."""

    return datetime.now(timezone.utc)


def _reference_document(reference: BRIDGE.Reference) -> dict[str, str]:
    return {"path": os.fspath(reference.path), "sha256": reference.sha256}


def _reference(record: BRIDGE.SecureRecord) -> BRIDGE.Reference:
    return BRIDGE.Reference(record.path, record.sha256)


def _availability_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {key: value for key, value in document.items() if key != "availability_binding_sha256"}
    )


def _receipt_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {key: value for key, value in document.items() if key != "transport_receipt_sha256"}
    )


def _role_provenance_closure_digest(document: Mapping[str, Any]) -> str:
    """Bind the role-local proof inputs without a controller-derived host fact.

    The normalized role-validation record is later the only artifact carried
    into phase evidence.  Its request digest therefore commits to the exact
    immutable request, role attestation, transport receipt, and nested local
    host proof rather than merely committing to a controller boolean.
    """

    return _sha256(
        {
            "schema": CONVERGENCE_ROLE_VALIDATION_SCHEMA,
            "campaign_id": document["campaign_id"],
            "operation_id": document["operation_id"],
            "app_release_sha": document["app_release_sha"],
            "manifest_sha256": document["manifest_sha256"],
            "approval_sha256": document["approval_sha256"],
            "phase": PHASE,
            "operation": document["operation"],
            "role": document["role"],
            "expected_host": document["expected_host"],
            "observed_host": document["observed_host"],
            "observed_at": document["observed_at"],
            "transport": document["transport"],
            "worker_request": document["worker_request"],
            "worker_attestation": document["worker_attestation"],
            "transport_receipt": document["transport_receipt"],
            "host_identity_proof_sha256": document["host_identity_proof_sha256"],
            "compose_execution": document["compose_execution"],
        }
    )


def _phase_root(context: BRIDGE.EvidenceContext) -> Path:
    return BRIDGE._phase_root(context.manifest)  # noqa: SLF001


def _incoming_root(context: BRIDGE.EvidenceContext) -> Path:
    return BRIDGE._source_input_root(context.manifest) / INCOMING_DIRECTORY  # noqa: SLF001


def canonical_incoming_path(
    context: BRIDGE.EvidenceContext,
    *,
    kind: str,
    role: str,
    digest: str,
) -> Path:
    if kind not in INCOMING_KINDS or role not in ROLES:
        raise ConvergenceSourceSetProducerError("incoming role artifact kind is invalid")
    _nonzero_sha256(digest, label="incoming artifact digest")
    return _incoming_root(context) / kind / f"{role}.{digest}.json"


def canonical_pure_observation_path(
    context: BRIDGE.EvidenceContext,
    *,
    label: str,
    digest: str,
) -> Path:
    """Return the only accepted immutable input location for a pure record."""

    if label not in PURE_OBSERVATIONS:
        raise ConvergenceSourceSetProducerError("pure observation label is invalid")
    _nonzero_sha256(digest, label="pure observation digest")
    return _incoming_root(context) / "pure-observations" / f"{label}.{digest}.json"


def _availability_path(context: BRIDGE.EvidenceContext, digest: str) -> Path:
    _nonzero_sha256(digest, label="availability digest")
    return BRIDGE._source_set_root(context.manifest) / f"unavailable-source-set.{digest}.json"  # noqa: SLF001


def _ensure_layout(context: BRIDGE.EvidenceContext) -> None:
    """Create only root-only directories; no input or runtime state is changed."""

    try:
        phase_root = BRIDGE._ensure_private_child(  # noqa: SLF001
            context.evidence_root,
            "convergence-gate",
            label="convergence source-set producer root",
        )
        source_input = BRIDGE._ensure_private_child(  # noqa: SLF001
            phase_root,
            "observation-inputs",
            label="convergence source-set producer inputs",
        )
        for name in ("role-validations", "observations", "source-sets"):
            BRIDGE._ensure_private_child(  # noqa: SLF001
                source_input,
                name,
                label=f"convergence source-set producer {name}",
            )
        incoming = BRIDGE._ensure_private_child(  # noqa: SLF001
            source_input,
            INCOMING_DIRECTORY,
            label="convergence source-set producer incoming root",
        )
        for kind in INCOMING_KINDS:
            BRIDGE._ensure_private_child(  # noqa: SLF001
                incoming,
                kind,
                label=f"convergence source-set producer incoming {kind}",
            )
    except BRIDGE.ConvergenceGateError as exc:
        raise ConvergenceSourceSetProducerError("controller evidence layout is unsafe") from exc


def _read_record(reference: BRIDGE.Reference, *, label: str) -> BRIDGE.SecureRecord:
    try:
        # Role attestations can contain bounded redacted parity rows, which are
        # intentionally larger than the small bridge publication documents.
        # The worker and this reader share one explicit upper bound.
        return BRIDGE._read_secure_record(  # noqa: SLF001
            reference,
            label=label,
            maximum=WORKER.MAX_JSON_BYTES,
        )
    except (BRIDGE.ConvergenceGateError, BRIDGE.ConvergenceSourceUnavailable) as exc:
        raise ConvergenceSourceSetProducerError(f"{label} is unavailable or unsafe") from exc


def _validate_context(context: BRIDGE.EvidenceContext) -> None:
    try:
        BRIDGE._validate_context(context, required_position="started")  # noqa: SLF001
    except BRIDGE.ConvergenceGateError as exc:
        raise ConvergenceSourceSetProducerError("convergence journal is not durably started") from exc


def _pure_observation_identity(context: BRIDGE.EvidenceContext) -> dict[str, Any]:
    return _context_identity(context)


def _pure_observation_identity_with_phase(
    context: BRIDGE.EvidenceContext,
) -> dict[str, Any]:
    return {
        **_pure_observation_identity(context),
        "phase_started_at": context.journal["started_at"],
    }


def _validate_pure_observation(
    document: Mapping[str, Any],
    *,
    label: str,
    context: BRIDGE.EvidenceContext,
    now: datetime,
) -> dict[str, Any]:
    """Validate one externally collected record without trusting its path alone."""

    identity = _pure_observation_identity(context)
    try:
        if label == "blob_roundtrip":
            return BLOB_ROUNDTRIP.validate_observation(document, identity=identity, now=now)
        if label == "queue_state":
            return QUEUE_STATE.validate_published_queue_observation(
                document,
                identity=_pure_observation_identity_with_phase(context),
                now=now,
            )
        if label == "dr_tls":
            return DR_TLS.validate_observation(document, identity=identity, now=now)
        if label == "destination_firewall":
            return DESTINATION_FIREWALL.validate_published_destination_firewall_observation(
                document,
                identity=_pure_observation_identity_with_phase(context),
                now=now,
            )
        if label == "witness_live":
            return WITNESS_LIVE.validate_observation(
                document,
                identity=identity,
                journal_started_at=_timestamp(context.journal["started_at"], label="phase start"),
                now=now,
            )
    except (
        BLOB_ROUNDTRIP.BlobRoundtripContractError,
        QUEUE_STATE.QueueStateObservationError,
        DR_TLS.DrTlsContractError,
        DESTINATION_FIREWALL.DestinationFirewallObservationError,
        WITNESS_LIVE.WitnessLiveContractError,
    ) as exc:
        raise ConvergenceSourceSetProducerError(f"{label} pure observation is invalid") from exc
    raise ConvergenceSourceSetProducerError("pure observation label is invalid")


def _load_pure_observations(
    context: BRIDGE.EvidenceContext,
    *,
    digests: Mapping[str, str],
    now: datetime,
) -> dict[str, BRIDGE.SecureRecord]:
    if set(digests) != set(PURE_OBSERVATIONS):
        raise ConvergenceSourceSetProducerError("pure observation digests must cover exactly five records")
    records: dict[str, BRIDGE.SecureRecord] = {}
    for label in PURE_OBSERVATIONS:
        digest = _nonzero_sha256(digests[label], label=f"{label} pure observation")
        path = canonical_pure_observation_path(context, label=label, digest=digest)
        record = _read_record(
            BRIDGE.Reference(path, digest),
            label=f"{label} pure observation",
        )
        checked = _validate_pure_observation(
            record.document,
            label=label,
            context=context,
            now=now,
        )
        if checked != record.document:
            raise ConvergenceSourceSetProducerError(f"{label} pure observation normalization differs")
        records[label] = record
    return records


def _controller_producer_release_root(context: BRIDGE.EvidenceContext) -> Path:
    return (
        WORKER.PROJECT_ROOT_PREFIX
        / context.manifest["operation_id"]
        / "releases"
        / context.manifest["release_sha"]
    )


@dataclass(frozen=True)
class _ControllerProducerLauncherContract:
    release_root_descriptor: int
    producer_descriptor: int
    launcher_descriptor: int


def _controller_producer_descriptor(value: str | None, *, label: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[3-9][0-9]*", value) is None:
        raise ControllerProducerExactReleaseUnavailable(f"{label} descriptor is invalid")
    return int(value)


def _assert_controller_producer_regular_descriptor(descriptor: int, *, label: str, private: bool) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ControllerProducerExactReleaseUnavailable(f"{label} descriptor is unavailable") from exc
    unsafe_mode = 0o077 if private else 0o022
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & unsafe_mode
        or metadata.st_size < 1
        or metadata.st_size > CONTROLLER_PRODUCER_MAX_SOURCE_BYTES
    ):
        raise ControllerProducerExactReleaseUnavailable(f"{label} descriptor is not root-controlled")
    return metadata


def _assert_controller_producer_descriptor_path(
    descriptor: int,
    path: Path,
    *,
    label: str,
    directory: bool,
) -> None:
    try:
        held = os.fstat(descriptor)
        named = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ControllerProducerExactReleaseUnavailable(f"{label} path is unavailable") from exc
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        path.is_symlink()
        or not expected_type(named.st_mode)
        or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise ControllerProducerExactReleaseUnavailable(
            f"{label} descriptor differs from its canonical path"
        )


def _require_isolated_controller_producer_execution() -> None:
    flags = sys.flags
    if (
        getattr(flags, "isolated", 0) != 1
        or getattr(flags, "ignore_environment", 0) != 1
        or getattr(flags, "no_user_site", 0) != 1
        or getattr(flags, "no_site", 0) != 1
        or not bool(getattr(flags, "safe_path", False))
    ):
        raise ControllerProducerExactReleaseUnavailable(
            "controller convergence producer must use an isolated Python interpreter (-I -S); "
            f"required={CONTROLLER_PRODUCER_EXACT_RELEASE_REQUIREMENT}"
        )
    if any(not isinstance(entry, str) or not Path(entry).is_absolute() for entry in sys.path):
        raise ControllerProducerExactReleaseUnavailable(
            "controller convergence producer interpreter path is unsafe"
        )


def _require_controller_producer_launcher_contract(
    context: BRIDGE.EvidenceContext,
) -> _ControllerProducerLauncherContract:
    """Accept only the three no-follow capabilities supplied by the launcher."""

    _require_isolated_controller_producer_execution()
    release_root = _controller_producer_release_root(context)
    producer_path = release_root / CONTROLLER_PRODUCER_RELATIVE_PATH
    launcher_path = release_root / CONTROLLER_PRODUCER_LAUNCHER_RELATIVE_PATH
    try:
        WORKER._assert_release_directory_chain(  # noqa: SLF001
            release_root,
            label="controller producer immutable release root",
        )
        WORKER._assert_root_controlled_directory_chain(  # noqa: SLF001
            producer_path.parent,
            boundary=release_root,
            label="controller producer release directory",
            private=False,
        )
    except WORKER.ConvergenceRoleObserverError as exc:
        raise ControllerProducerExactReleaseUnavailable(
            "controller producer release path is unavailable"
        ) from exc

    contract = _ControllerProducerLauncherContract(
        release_root_descriptor=_controller_producer_descriptor(
            os.environ.get(CONTROLLER_PRODUCER_RELEASE_ROOT_FD_ENV),
            label="controller producer release root",
        ),
        producer_descriptor=_controller_producer_descriptor(
            os.environ.get(CONTROLLER_PRODUCER_FD_ENV),
            label="controller producer",
        ),
        launcher_descriptor=_controller_producer_descriptor(
            os.environ.get(CONTROLLER_PRODUCER_LAUNCHER_FD_ENV),
            label="controller producer launcher",
        ),
    )
    try:
        WORKER._assert_release_directory_descriptor(  # noqa: SLF001
            contract.release_root_descriptor,
            label="controller producer release root",
            private=True,
        )
        _assert_controller_producer_regular_descriptor(
            contract.producer_descriptor,
            label="controller producer",
            private=False,
        )
        _assert_controller_producer_regular_descriptor(
            contract.launcher_descriptor,
            label="controller producer launcher",
            private=True,
        )
    except WORKER.ConvergenceRoleObserverError as exc:
        raise ControllerProducerExactReleaseUnavailable(
            "controller producer launcher capability is unavailable"
        ) from exc
    _assert_controller_producer_descriptor_path(
        contract.release_root_descriptor,
        release_root,
        label="controller producer release root",
        directory=True,
    )
    _assert_controller_producer_descriptor_path(
        contract.producer_descriptor,
        producer_path,
        label="controller producer",
        directory=False,
    )
    _assert_controller_producer_descriptor_path(
        contract.launcher_descriptor,
        launcher_path,
        label="controller producer launcher",
        directory=False,
    )
    try:
        executing = os.stat(__file__, follow_symlinks=True)
        producer = os.fstat(contract.producer_descriptor)
    except OSError as exc:
        raise ControllerProducerExactReleaseUnavailable(
            "controller producer execution path is unavailable"
        ) from exc
    if (executing.st_dev, executing.st_ino) != (producer.st_dev, producer.st_ino):
        raise ControllerProducerExactReleaseUnavailable(
            "controller producer is not executing the launcher-held inode"
        )
    return contract


def _held_controller_release_git_text(
    context: BRIDGE.EvidenceContext,
    *,
    release_root_descriptor: int,
    arguments: list[str],
    label: str,
) -> str:
    """Run only fixed object reads against the launcher-held release FD."""

    release_sha = context.manifest["release_sha"]
    allowed = {
        ("rev-parse", "--verify", f"{release_sha}^{{commit}}"),
        ("rev-parse", "--verify", f"{release_sha}^{{tree}}"),
        ("cat-file", "blob", f"{release_sha}:{CONTROLLER_PRODUCER_RELATIVE_PATH}"),
        ("cat-file", "blob", f"{release_sha}:{CONTROLLER_PRODUCER_LAUNCHER_RELATIVE_PATH}"),
    }
    if tuple(arguments) not in allowed or not Path("/proc/self/fd").is_dir():
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git object read is invalid")
    try:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.attributesfile=/dev/null",
                "--no-replace-objects",
                "-C",
                f"/proc/self/fd/{release_root_descriptor}",
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=CONTROLLER_PRODUCER_SAFE_ENV,
            close_fds=True,
            pass_fds=(release_root_descriptor,),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git object read is unavailable") from exc
    if (
        completed.returncode != 0
        or len(completed.stdout) < 1
        or len(completed.stdout) > CONTROLLER_PRODUCER_MAX_SOURCE_BYTES
        or len(completed.stderr) > 64 * 1024
    ):
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git object read is invalid")
    try:
        return completed.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git object read is not ASCII") from exc


def _held_controller_release_blob_sha256(
    context: BRIDGE.EvidenceContext,
    *,
    release_root_descriptor: int,
    relative_path: str,
    label: str,
) -> str:
    release_sha = context.manifest["release_sha"]
    if (
        relative_path
        not in {
            CONTROLLER_PRODUCER_RELATIVE_PATH,
            CONTROLLER_PRODUCER_LAUNCHER_RELATIVE_PATH,
        }
        or not Path("/proc/self/fd").is_dir()
    ):
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git blob is invalid")
    try:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.attributesfile=/dev/null",
                "--no-replace-objects",
                "-C",
                f"/proc/self/fd/{release_root_descriptor}",
                "cat-file",
                "blob",
                f"{release_sha}:{relative_path}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=CONTROLLER_PRODUCER_SAFE_ENV,
            close_fds=True,
            pass_fds=(release_root_descriptor,),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git blob is unavailable") from exc
    if (
        completed.returncode != 0
        or not completed.stdout
        or len(completed.stdout) > CONTROLLER_PRODUCER_MAX_SOURCE_BYTES
        or len(completed.stderr) > 64 * 1024
    ):
        raise ControllerProducerExactReleaseUnavailable(f"{label} Git blob is invalid")
    return hashlib.sha256(completed.stdout).hexdigest()


def _require_controller_producer_exact_release(context: BRIDGE.EvidenceContext) -> None:
    """Authorize publication only from the root-only launcher FD capability."""

    contract = _require_controller_producer_launcher_contract(context)
    release_sha = context.manifest["release_sha"]
    release_tree_sha = context.manifest["release_tree_sha"]
    if (
        _held_controller_release_git_text(
            context,
            release_root_descriptor=contract.release_root_descriptor,
            arguments=["rev-parse", "--verify", f"{release_sha}^{{commit}}"],
            label="controller producer release commit",
        )
        != release_sha
        or _held_controller_release_git_text(
            context,
            release_root_descriptor=contract.release_root_descriptor,
            arguments=["rev-parse", "--verify", f"{release_sha}^{{tree}}"],
            label="controller producer release tree",
        )
        != release_tree_sha
    ):
        raise ControllerProducerExactReleaseUnavailable(
            "controller producer release commit/tree differs from the manifest"
        )
    producer_sha256 = _assert_controller_producer_regular_descriptor(
        contract.producer_descriptor,
        label="controller producer",
        private=False,
    )
    launcher_sha256 = _assert_controller_producer_regular_descriptor(
        contract.launcher_descriptor,
        label="controller producer launcher",
        private=True,
    )
    del producer_sha256, launcher_sha256
    if (
        WORKER._verified_release_file_sha256(  # noqa: SLF001
            contract.producer_descriptor,
            label="controller producer",
        )
        != _held_controller_release_blob_sha256(
            context,
            release_root_descriptor=contract.release_root_descriptor,
            relative_path=CONTROLLER_PRODUCER_RELATIVE_PATH,
            label="controller producer",
        )
        or WORKER._verified_release_file_sha256(  # noqa: SLF001
            contract.launcher_descriptor,
            label="controller producer launcher",
        )
        != _held_controller_release_blob_sha256(
            context,
            release_root_descriptor=contract.release_root_descriptor,
            relative_path=CONTROLLER_PRODUCER_LAUNCHER_RELATIVE_PATH,
            label="controller producer launcher",
        )
    ):
        raise ControllerProducerExactReleaseUnavailable(
            "controller producer or launcher differs from the exact release"
        )


def _context_identity(context: BRIDGE.EvidenceContext) -> dict[str, Any]:
    return BRIDGE._source_identity_fields(context)  # noqa: SLF001


def _validate_worker_request(
    request: Mapping[str, Any],
    *,
    context: BRIDGE.EvidenceContext,
    role: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        document = WORKER.validate_request(request, now=now)
    except WORKER.ConvergenceRoleObserverError as exc:
        raise ConvergenceSourceSetProducerError(f"{role} worker request is invalid") from exc
    expected = {
        **_context_identity(context),
        "phase": PHASE,
        "operation": OPERATION,
        "role": role,
        "expected_host": CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
        "phase_started_at": context.journal["started_at"],
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise ConvergenceSourceSetProducerError(f"{role} worker request differs from controller context")
    return document


def _validate_object_storage_detail(
    detail: Mapping[str, Any],
    *,
    context: BRIDGE.EvidenceContext,
    role: str,
) -> dict[str, Any]:
    """Validate a redacted exact-version Object Storage descriptor.

    This proves only the *shape and binding* of an artifact route.  It does
    not prove that the remote host observed anything; that separate provenance
    requirement is enforced by ``_validate_remote_receiver_provenance``.
    """

    fields = {
        "provider", "bucket", "artifact_kind", "object_key", "version_id",
        "readback_version_id", "ciphertext_sha256", "ciphertext_bytes",
        "age_recipient_sha256", "private", "versioned",
    }
    if set(detail) != fields:
        raise ConvergenceSourceSetProducerError(
            f"{role} Object Storage detail fields differ"
        )
    if (
        detail.get("provider") != "arvan"
        or detail.get("bucket") != PRODUCTION_BUCKET
        or detail.get("artifact_kind") != "convergence-attestation"
        or not isinstance(detail.get("object_key"), str)
        or not isinstance(detail.get("version_id"), str)
        or not detail["version_id"].strip()
        or detail.get("readback_version_id") != detail["version_id"]
        or detail.get("private") is not True
        or detail.get("versioned") is not True
        or type(detail.get("ciphertext_bytes")) is not int
        or detail["ciphertext_bytes"] < 1
    ):
        raise ConvergenceSourceSetProducerError(
            f"{role} Object Storage detail differs"
        )
    ciphertext_sha256 = _nonzero_sha256(
        detail.get("ciphertext_sha256"), label=f"{role} ciphertext"
    )
    _nonzero_sha256(detail.get("age_recipient_sha256"), label=f"{role} age recipient")
    try:
        validate_object_key_binding(
            detail["object_key"],
            operation_id=context.manifest["operation_id"],
            artifact_kind=detail["artifact_kind"],
            ciphertext_sha256=ciphertext_sha256,
        )
    except ProductionTransportError as exc:
        raise ConvergenceSourceSetProducerError(
            f"{role} Object Storage key binding differs"
        ) from exc
    return dict(detail)


def _validate_transport_detail(
    receipt: Mapping[str, Any],
    *,
    context: BRIDGE.EvidenceContext,
    role: str,
) -> dict[str, Any] | None:
    transport = receipt["transport"]
    detail = receipt["transport_detail"]
    if not isinstance(detail, Mapping):
        raise ConvergenceSourceSetProducerError(f"{role} transport detail is invalid")
    topology = CONTROLLER.EXPECTED_TOPOLOGY[role]
    if transport == "controller-local-root-only":
        if (
            set(detail) != {"source_host", "controller_role"}
            or detail.get("source_host") != topology["host"]
            or detail.get("controller_role") != "bot_fi"
        ):
            raise ConvergenceSourceSetProducerError("local Bot-FI transport detail differs")
        return None
    if transport == "trusted-ssh-redacted-attestation":
        if set(detail) != {"host", "port", "user", "known_hosts_sha256"}:
            raise ConvergenceSourceSetProducerError(f"{role} SSH transport detail fields differ")
        if (
            detail.get("host") != topology["host"]
            or detail.get("port") != topology["ssh_port"]
            or detail.get("user") != topology["ssh_user"]
        ):
            raise ConvergenceSourceSetProducerError(f"{role} SSH transport endpoint differs")
        _nonzero_sha256(detail.get("known_hosts_sha256"), label=f"{role} known_hosts")
        return None
    if transport == "object-storage-private-versioned-age":
        return _validate_object_storage_detail(detail, context=context, role=role)
    raise ConvergenceSourceSetProducerError(f"{role} transport is not allowed")


def _remote_receiver_policy_contract(
    context: BRIDGE.EvidenceContext,
    *,
    role: str,
) -> dict[str, str]:
    """Return the immutable manifest trust anchor for one remote receiver."""

    artifacts = context.manifest.get("artifacts")
    contracts = (
        artifacts.get("remote_receiver_signing_policies")
        if isinstance(artifacts, Mapping)
        else None
    )
    value = contracts.get(role) if isinstance(contracts, Mapping) else None
    if not isinstance(value, Mapping) or set(value) != REMOTE_RECEIVER_POLICY_CONTRACT_FIELDS:
        raise ConvergenceSourceSetUnavailable(
            f"{role} immutable remote receiver signing-policy contract is unavailable"
        )
    contract = dict(value)
    for field in (
        "policy_file_sha256",
        "policy_sha256",
        "public_key_sha256",
        "receiver_sha256",
        "worker_sha256",
    ):
        _nonzero_sha256(contract.get(field), label=f"{role} immutable policy {field}")
    if not isinstance(contract.get("key_id"), str) or not contract["key_id"].strip():
        raise ConvergenceSourceSetProducerError(
            f"{role} immutable policy key id is invalid"
        )
    return contract


def _validate_remote_receiver_provenance(
    *,
    receipt: Mapping[str, Any],
    context: BRIDGE.EvidenceContext,
    role: str,
    request: Mapping[str, Any],
    attestation: BRIDGE.SecureRecord,
    object_storage: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Read and verify only receipt-addressed root-only receiver inputs."""

    contract = _remote_receiver_policy_contract(context, role=role)
    receipt_policy_digest = _nonzero_sha256(
        receipt["remote_receiver_policy_file_sha256"],
        label=f"{role} remote receiver policy file",
    )
    if receipt_policy_digest != contract["policy_file_sha256"]:
        raise ConvergenceSourceSetProducerError(
            f"{role} receipt-selected remote receiver policy is not immutable-manifest pinned"
        )
    signed_attestation_digest = _nonzero_sha256(
        receipt["remote_receiver_signed_attestation_file_sha256"],
        label=f"{role} remote receiver signed attestation file",
    )
    policy = _read_record(
        BRIDGE.Reference(
            canonical_incoming_path(
                context,
                kind="remote-receiver-policies",
                role=role,
                digest=contract["policy_file_sha256"],
            ),
            contract["policy_file_sha256"],
        ),
        label=f"{role} remote receiver signing policy",
    )
    try:
        parsed_policy = RECEIVER_POLICY.parse_policy_payload(policy.payload)
    except RECEIVER_POLICY.RemoteReceiverSigningPolicyError as exc:
        raise ConvergenceSourceSetProducerError(
            f"{role} immutable remote receiver policy is invalid"
        ) from exc
    policy_anchor = {
        "policy_sha256": parsed_policy.policy_sha256,
        "key_id": parsed_policy.key_id,
        "public_key_sha256": hashlib.sha256(parsed_policy.public_key).hexdigest(),
        "receiver_sha256": parsed_policy.receiver_sha256,
        "worker_sha256": parsed_policy.worker_sha256,
    }
    if any(policy_anchor[key] != contract[key] for key in policy_anchor):
        raise ConvergenceSourceSetProducerError(
            f"{role} immutable remote receiver policy anchor differs"
        )
    signed_attestation = _read_record(
        BRIDGE.Reference(
            canonical_incoming_path(
                context,
                kind="remote-receiver-signed-attestations",
                role=role,
                digest=signed_attestation_digest,
            ),
            signed_attestation_digest,
        ),
        label=f"{role} remote receiver signed attestation",
    )
    expected = REMOTE_PROVENANCE.ExpectedRemoteReceiverProvenance(
        campaign_id=context.manifest["campaign_id"],
        operation_id=context.manifest["operation_id"],
        release_sha=context.manifest["release_sha"],
        release_tree_sha=context.manifest["release_tree_sha"],
        role=role,
        manifest_sha256=context.manifest_sha256,
        plan_sha256=context.plan_sha256,
        approval_sha256=context.manifest["artifacts"]["cutover_approval_sha256"],
        phase=PHASE,
        operation=OPERATION,
        expected_host=CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
        phase_started_at=context.journal["started_at"],
        request_sha256=request["request_sha256"],
        worker_attestation_sha256=attestation.document["attestation_sha256"],
        worker_attestation_file_sha256=attestation.sha256,
    )
    try:
        document = REMOTE_PROVENANCE.verify_remote_receiver_provenance(
            policy_payload=policy.payload,
            attestation_payload=signed_attestation.payload,
            expected=expected,
            now=now,
            verify_ed25519=ED25519.verify_ed25519,
        )
    except REMOTE_PROVENANCE.RemoteReceiverProvenanceError as exc:
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver signed provenance is invalid"
        ) from exc
    if (
        set(document) != REMOTE_RECEIVER_ATTESTATION_FIELDS
        or document.get("schema") != REMOTE_RECEIVER_ATTESTATION_SCHEMA
        or document.get("status") != "received"
    ):
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver provenance fields differ"
        )
    remote_detail = document.get("object_storage")
    if not isinstance(remote_detail, Mapping):
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver Object Storage detail is invalid"
        )
    normalized_remote_detail = _validate_object_storage_detail(
        remote_detail,
        context=context,
        role=role,
    )
    if normalized_remote_detail != dict(object_storage):
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver VersionId/read-back differs from transport receipt"
        )
    observed_at = _timestamp(
        document.get("observed_at"), label=f"{role} remote receiver observation time"
    )
    phase_start = _timestamp(context.journal["started_at"], label="convergence phase start")
    if observed_at < phase_start:
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver attestation predates phase start"
        )
    if observed_at > now + BRIDGE.MAX_FUTURE_SKEW:
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver attestation is future dated"
        )
    if now - observed_at > BRIDGE.MAX_SOURCE_AGE:
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver attestation is stale"
        )
    if (
        document.get("presigned_url_persisted") is not False
        or document.get("presigned_url_logged") is not False
        or document.get("contains_secret_material") is not False
        or document.get("direct_fi_to_ir_transfer") is not False
        or document.get("receiver_attestation_sha256")
        != _sha256({key: value for key, value in document.items() if key != "receiver_attestation_sha256"})
    ):
        raise ConvergenceSourceSetProducerError(
            f"{role} remote receiver attestation redaction or digest differs"
        )
    return document


def _validate_receipt(
    receipt: Mapping[str, Any],
    *,
    context: BRIDGE.EvidenceContext,
    role: str,
    request: Mapping[str, Any],
    attestation: BRIDGE.SecureRecord,
    now: datetime,
) -> dict[str, Any]:
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != TRANSPORT_RECEIPT_FIELDS
        or receipt.get("schema") != TRANSPORT_RECEIPT_SCHEMA
        or receipt.get("status") != "received"
        or receipt.get("payload_class") != "redacted-attestation-json"
        or receipt.get("transport") != TRANSPORT_BY_ROLE[role]
        or receipt.get("direct_fi_to_ir_transfer") is not False
    ):
        raise ConvergenceSourceSetProducerError(f"{role} transport receipt fields differ")
    expected = {
        **_context_identity(context),
        "phase": PHASE,
        "operation": OPERATION,
        "role": role,
        "expected_host": CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
        "phase_started_at": context.journal["started_at"],
        "request_sha256": request["request_sha256"],
        "attestation_sha256": attestation.document["attestation_sha256"],
        "attestation_file_sha256": attestation.sha256,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ConvergenceSourceSetProducerError(f"{role} transport receipt binding differs")
    received_at = _timestamp(receipt.get("received_at"), label=f"{role} transport receipt time")
    phase_start = _timestamp(context.journal["started_at"], label="convergence phase start")
    if received_at < phase_start:
        raise ConvergenceSourceSetProducerError(f"{role} transport receipt predates phase start")
    if received_at > now + BRIDGE.MAX_FUTURE_SKEW:
        raise ConvergenceSourceSetProducerError(f"{role} transport receipt is future dated")
    if now - received_at > BRIDGE.MAX_SOURCE_AGE:
        raise ConvergenceSourceSetProducerError(f"{role} transport receipt is stale")
    if receipt.get("transport_receipt_sha256") != _receipt_digest(receipt):
        raise ConvergenceSourceSetProducerError(f"{role} transport receipt digest differs")
    object_storage = _validate_transport_detail(receipt, context=context, role=role)
    remote = receipt.get("remote_receiver_attestation")
    if role not in OBJECT_STORAGE_ROLES:
        if (
            remote is not None
            or receipt.get("remote_receiver_policy_file_sha256") is not None
            or receipt.get("remote_receiver_signed_attestation_file_sha256") is not None
        ):
            raise ConvergenceSourceSetProducerError(
                f"{role} non-Object-Storage receipt must not carry remote receiver provenance"
            )
        return dict(receipt)
    if object_storage is None:
        raise ConvergenceSourceSetProducerError(
            f"{role} Object Storage receipt has no exact-version binding"
        )
    if remote is not None:
        raise ConvergenceSourceSetProducerError(
            f"{role} legacy unsigned remote receiver attestation is not accepted"
        )
    _validate_remote_receiver_provenance(
        receipt=receipt,
        context=context,
        role=role,
        request=request,
        attestation=attestation,
        object_storage=object_storage,
        now=now,
    )
    return dict(receipt)


def _validate_ingress(
    context: BRIDGE.EvidenceContext,
    *,
    role: str,
    request_digest: str,
    attestation_digest: str,
    receipt_digest: str,
    now: datetime,
) -> Ingress:
    request_ref = BRIDGE.Reference(
        canonical_incoming_path(context, kind="requests", role=role, digest=request_digest),
        request_digest,
    )
    attestation_ref = BRIDGE.Reference(
        canonical_incoming_path(context, kind="attestations", role=role, digest=attestation_digest),
        attestation_digest,
    )
    receipt_ref = BRIDGE.Reference(
        canonical_incoming_path(context, kind="transport-receipts", role=role, digest=receipt_digest),
        receipt_digest,
    )
    request = _read_record(request_ref, label=f"{role} observation request")
    attestation = _read_record(attestation_ref, label=f"{role} observation attestation")
    receipt = _read_record(receipt_ref, label=f"{role} observation transport receipt")
    request_document = _validate_worker_request(
        request.document,
        context=context,
        role=role,
        now=now,
    )
    try:
        attestation_document = WORKER.validate_attestation(
            attestation.document,
            request=request_document,
            now=now,
        )
    except WORKER.ConvergenceRoleObserverError as exc:
        raise ConvergenceSourceSetProducerError(f"{role} observation attestation is invalid") from exc
    _validate_receipt(
        receipt.document,
        context=context,
        role=role,
        request=request_document,
        attestation=attestation,
        now=now,
    )
    observed_at = _timestamp(attestation_document["observed_at"], label=f"{role} observation time")
    snapshot = attestation_document.get("runtime_snapshot")
    captured_at = (
        _timestamp(snapshot["captured_at"], label=f"{role} runtime capture")
        if isinstance(snapshot, Mapping)
        else None
    )
    return Ingress(
        role=role,
        request=request,
        attestation=attestation,
        receipt=receipt,
        observed_at=observed_at,
        captured_at=captured_at,
    )


def _validate_capture_times(
    ingresses: Mapping[str, Ingress],
    *,
    context: BRIDGE.EvidenceContext,
    now: datetime,
) -> datetime:
    phase_start = _timestamp(context.journal["started_at"], label="convergence phase start")
    times: list[datetime] = []
    for ingress in ingresses.values():
        times.append(ingress.observed_at)
        if ingress.captured_at is not None:
            times.append(ingress.captured_at)
    if len(times) != len(ROLES) + len(RUNTIME_ROLES):
        raise ConvergenceSourceSetProducerError("convergence capture coverage is incomplete")
    if any(value < phase_start for value in times):
        raise ConvergenceSourceSetProducerError("convergence capture predates durable phase start")
    if any(value > now + BRIDGE.MAX_FUTURE_SKEW for value in times):
        raise ConvergenceSourceSetProducerError("convergence capture is future dated")
    if any(now - value > BRIDGE.MAX_SOURCE_AGE for value in times):
        raise ConvergenceSourceSetProducerError("fresh convergence role observations are unavailable")
    if max(times) - min(times) > BRIDGE.MAX_SOURCE_SKEW:
        raise ConvergenceSourceSetProducerError("convergence role capture skew is too large")
    return max(times)


def _assert_publication_freshness(
    ingresses: Mapping[str, Ingress],
    *,
    context: BRIDGE.EvidenceContext,
) -> datetime:
    """Use an unoverrideable fresh clock immediately before each publication."""

    current = _utcnow()
    _validate_capture_times(ingresses, context=context, now=current)
    for role, ingress in ingresses.items():
        received_at = _timestamp(
            ingress.receipt.document.get("received_at"),
            label=f"{role} transport receipt time",
        )
        if received_at > current + BRIDGE.MAX_FUTURE_SKEW:
            raise ConvergenceSourceSetProducerError(
                f"{role} transport receipt is future dated at publication"
            )
        if current - received_at > BRIDGE.MAX_SOURCE_AGE:
            raise ConvergenceSourceSetProducerError(
                f"{role} transport receipt is stale at publication"
            )
    return current


def _runtime_snapshot(ingress: Ingress) -> Mapping[str, Any]:
    snapshot = ingress.attestation.document.get("runtime_snapshot")
    if ingress.role not in RUNTIME_ROLES or not isinstance(snapshot, Mapping):
        raise ConvergenceSourceSetProducerError(f"{ingress.role} lacks a runtime observation")
    return snapshot


def _validated_parity_snapshot(ingress: Ingress) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    snapshot = _runtime_snapshot(ingress)
    database = snapshot.get("database")
    parity = snapshot.get("redacted_parity_snapshot")
    if (
        not isinstance(database, Mapping)
        or not isinstance(parity, Mapping)
        or database.get("redacted_snapshot_sha256") != _sha256(parity)
    ):
        raise ConvergenceSourceSetProducerError(f"{ingress.role} redacted parity binding differs")
    try:
        fingerprint = business_snapshot_fingerprint(parity)
    except ValueError as exc:
        raise ConvergenceSourceSetProducerError(f"{ingress.role} redacted parity is incomplete") from exc
    tables = parity.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise ConvergenceSourceSetProducerError(f"{ingress.role} parity table set is invalid")
    table_names = sorted(str(name) for name in tables)
    row_count = 0
    for table in tables.values():
        if not isinstance(table, Mapping) or type(table.get("row_count")) is not int:
            raise ConvergenceSourceSetProducerError(f"{ingress.role} parity table count is invalid")
        row_count += int(table["row_count"])
    if (
        database.get("table_set_sha256") != _sha256(table_names)
        or database.get("business_fingerprint_sha256") != fingerprint
        or database.get("row_count") != row_count
        or database.get("table_count") != len(table_names)
    ):
        raise ConvergenceSourceSetProducerError(f"{ingress.role} parity summary differs from records")
    expected_database = {key: value for key, value in database.items() if key != "database_state_sha256"}
    if database.get("database_state_sha256") != _sha256(expected_database):
        raise ConvergenceSourceSetProducerError(f"{ingress.role} database state digest differs")
    return parity, database


def _database_observation(
    context: BRIDGE.EvidenceContext,
    ingresses: Mapping[str, Ingress],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    parity: dict[str, Mapping[str, Any]] = {}
    summaries: dict[str, Mapping[str, Any]] = {}
    for role in RUNTIME_ROLES:
        parity[role], summaries[role] = _validated_parity_snapshot(ingresses[role])
    comparisons: list[dict[str, Any]] = []
    for scope, source_role, target_role in sorted(BRIDGE.DATABASE_PAIRS):
        report = compare_parity_snapshots(parity[source_role], parity[target_role], sample_limit=0)
        counts = report.get("severity_counts") if isinstance(report, Mapping) else None
        if not isinstance(counts, Mapping):
            raise ConvergenceSourceSetProducerError("database parity comparison has no severity counts")
        harmful = (
            int(counts.get("business_drift") or 0),
            int(counts.get("critical_drift") or 0),
            int(counts.get("incomplete") or 0),
        )
        if any(value != 0 for value in harmful):
            raise ConvergenceSourceSetProducerError(f"database parity {source_role}->{target_role} is not converged")
        source = summaries[source_role]
        target = summaries[target_role]
        if (
            source["table_set_sha256"] != target["table_set_sha256"]
            or source["business_fingerprint_sha256"] != target["business_fingerprint_sha256"]
            or source["row_count"] != target["row_count"]
            or source["table_count"] != target["table_count"]
        ):
            raise ConvergenceSourceSetProducerError(f"database summary {source_role}->{target_role} differs")
        comparisons.append(
            {
                "scope": scope,
                "source_site": source_role,
                "target_site": target_role,
                "table_set_sha256": source["table_set_sha256"],
                "source_business_fingerprint_sha256": source["business_fingerprint_sha256"],
                "target_business_fingerprint_sha256": target["business_fingerprint_sha256"],
                "source_row_count": source["row_count"],
                "target_row_count": target["row_count"],
                "table_count": source["table_count"],
                "business_drift_count": int(counts.get("business_drift") or 0),
                "critical_drift_count": int(counts.get("critical_drift") or 0),
                "incomplete_count": int(counts.get("incomplete") or 0),
                "local_only_difference_count": int(counts.get("local_only_difference") or 0),
                "volatile_difference_count": int(counts.get("volatile_difference") or 0),
            }
        )
    state = {
        "comparisons": comparisons,
        "role_attestations": {role: ingresses[role].attestation.sha256 for role in RUNTIME_ROLES},
    }
    return {
        "schema": BRIDGE.DATABASE_OBSERVATION_SCHEMA,
        "status": "observed",
        **_context_identity(context),
        "observed_at": _timestamp_text(observed_at),
        "comparisons": comparisons,
        "mismatch_count": 0,
        "database_state_sha256": _sha256(state),
    }


def _stream_map(snapshot: Mapping[str, Any], *, role: str) -> tuple[int, dict[str, Mapping[str, Any]], dict[tuple[str, int], Mapping[str, Any]], int]:
    dr = snapshot.get("dr")
    if not isinstance(dr, Mapping) or set(dr) != {
        "producer_epoch", "source_streams", "destination_streams", "unresolved_conflict_count", "dr_state_sha256"
    }:
        raise ConvergenceSourceSetProducerError(f"{role} DR summary fields differ")
    epoch = dr.get("producer_epoch")
    if type(epoch) is not int or epoch < 1:
        raise ConvergenceSourceSetProducerError(f"{role} DR producer epoch is invalid")
    source_rows = dr.get("source_streams")
    destination_rows = dr.get("destination_streams")
    if not isinstance(source_rows, list) or not isinstance(destination_rows, list):
        raise ConvergenceSourceSetProducerError(f"{role} DR streams are invalid")
    sources: dict[str, Mapping[str, Any]] = {}
    expected_peers = set(RUNTIME_ROLES) - {role}
    for row in source_rows:
        if not isinstance(row, Mapping) or set(row) != {
            "destination_site", "source_sequence", "source_transaction_hash"
        }:
            raise ConvergenceSourceSetProducerError(f"{role} DR source stream fields differ")
        destination = row.get("destination_site")
        if destination not in expected_peers or destination in sources:
            raise ConvergenceSourceSetProducerError(f"{role} DR source stream peer differs")
        if type(row.get("source_sequence")) is not int or row["source_sequence"] < 0:
            raise ConvergenceSourceSetProducerError(f"{role} DR source sequence is invalid")
        _hash = row.get("source_transaction_hash")
        if not isinstance(_hash, str) or SHA256_RE.fullmatch(_hash) is None or (row["source_sequence"] == 0) != (_hash == ZERO_SHA256):
            raise ConvergenceSourceSetProducerError(f"{role} DR source hash is invalid")
        sources[str(destination)] = dict(row)
    if set(sources) != expected_peers:
        raise ConvergenceSourceSetProducerError(f"{role} DR source stream coverage differs")
    destinations: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in destination_rows:
        if not isinstance(row, Mapping) or set(row) != {
            "origin_site", "producer_epoch", "received_sequence", "applied_sequence",
            "received_transaction_hash", "applied_transaction_hash",
        }:
            raise ConvergenceSourceSetProducerError(f"{role} DR destination stream fields differ")
        origin = row.get("origin_site")
        row_epoch = row.get("producer_epoch")
        if origin not in expected_peers or type(row_epoch) is not int or row_epoch < 1 or (origin, row_epoch) in destinations:
            raise ConvergenceSourceSetProducerError(f"{role} DR destination stream identity differs")
        for field in ("received_sequence", "applied_sequence"):
            if type(row.get(field)) is not int or row[field] < 0:
                raise ConvergenceSourceSetProducerError(f"{role} DR {field} is invalid")
        for field, sequence in (("received_transaction_hash", row["received_sequence"]), ("applied_transaction_hash", row["applied_sequence"])):
            value = row.get(field)
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or (sequence == 0) != (value == ZERO_SHA256):
                raise ConvergenceSourceSetProducerError(f"{role} DR {field} is invalid")
        destinations[(str(origin), row_epoch)] = dict(row)
    conflicts = dr.get("unresolved_conflict_count")
    if type(conflicts) is not int or conflicts < 0:
        raise ConvergenceSourceSetProducerError(f"{role} DR conflict count is invalid")
    expected_dr = {key: value for key, value in dr.items() if key != "dr_state_sha256"}
    if dr.get("dr_state_sha256") != _sha256(expected_dr):
        raise ConvergenceSourceSetProducerError(f"{role} DR state digest differs")
    return epoch, sources, destinations, conflicts


def _dr_observation(
    context: BRIDGE.EvidenceContext,
    ingresses: Mapping[str, Ingress],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    details = {
        role: _stream_map(_runtime_snapshot(ingresses[role]), role=role)
        for role in RUNTIME_ROLES
    }
    conflicts = sum(item[3] for item in details.values())
    if conflicts != 0:
        raise ConvergenceSourceSetProducerError("DR conflict quarantine is not empty")
    streams: list[dict[str, Any]] = []
    for origin, destination in sorted(BRIDGE.DR_PAIRS):
        origin_epoch, source_rows, _unused_dest, _unused_conflicts = details[origin]
        source = source_rows[destination]
        _destination_epoch, _unused_source, destination_rows, _unused_conflicts = details[destination]
        target = destination_rows.get((origin, origin_epoch))
        if target is None:
            raise ConvergenceSourceSetProducerError(f"DR destination {origin}->{destination} lacks source epoch")
        sequence = source["source_sequence"]
        source_hash = source["source_transaction_hash"]
        if (
            target["received_sequence"] != sequence
            or target["applied_sequence"] != sequence
            or target["received_transaction_hash"] != source_hash
            or target["applied_transaction_hash"] != source_hash
        ):
            raise ConvergenceSourceSetProducerError(f"DR stream {origin}->{destination} is not exactly applied")
        streams.append(
            {
                "origin_site": origin,
                "destination_site": destination,
                "producer_epoch": origin_epoch,
                "source_sequence": sequence,
                "received_sequence": target["received_sequence"],
                "applied_sequence": target["applied_sequence"],
                "source_transaction_hash": source_hash,
                "received_transaction_hash": target["received_transaction_hash"],
                "applied_transaction_hash": target["applied_transaction_hash"],
            }
        )
    state = {
        "streams": streams,
        "role_attestations": {role: ingresses[role].attestation.sha256 for role in RUNTIME_ROLES},
    }
    return {
        "schema": BRIDGE.DR_OBSERVATION_SCHEMA,
        "status": "observed",
        **_context_identity(context),
        "observed_at": _timestamp_text(observed_at),
        "streams": streams,
        "conflict_count": 0,
        "dr_state_sha256": _sha256(state),
    }


def _role_validation_document(
    context: BRIDGE.EvidenceContext,
    *,
    ingress: Ingress,
) -> dict[str, Any]:
    role = ingress.role
    try:
        host_identity_proof = WORKER.validate_host_identity_proof(
            ingress.attestation.document.get("host_identity_proof"),
            request=ingress.request.document,
            now=ingress.observed_at,
        )
    except WORKER.ConvergenceRoleObserverError as exc:
        raise ConvergenceSourceSetProducerError(
            f"{role} local host identity proof is invalid"
        ) from exc
    # The controller must not turn the topology's expected host into an
    # observed fact.  The role-local worker read this IPv4 from its own kernel
    # before collecting runtime data; preserve the exact source artifacts so a
    # later gate/verifier can reopen the complete proof closure.
    proof_expected_host = str(host_identity_proof["expected_host"])
    proof_observed_host = str(host_identity_proof["observed_host"])
    proof_observed_at = str(host_identity_proof["observed_at"])
    proof_sha256 = str(host_identity_proof["host_identity_proof_sha256"])
    receipt_transport = ingress.receipt.document.get("transport")
    if receipt_transport != TRANSPORT_BY_ROLE[role]:
        raise ConvergenceSourceSetProducerError(
            f"{role} transport receipt does not carry the required transport"
        )
    compose_execution = ingress.attestation.document.get("compose_execution")
    if role in RUNTIME_ROLES:
        try:
            compose_execution = WORKER._validate_compose_execution_proof(  # noqa: SLF001
                compose_execution,
                request=ingress.request.document,
            )
        except WORKER.ConvergenceRoleObserverError as exc:
            raise ConvergenceSourceSetProducerError(
                f"{role} Compose execution proof is invalid"
            ) from exc
    elif compose_execution is not None:
        raise ConvergenceSourceSetProducerError(
            "Witness must not carry a Compose execution proof"
        )
    document = {
        "schema": CONVERGENCE_ROLE_VALIDATION_SCHEMA,
        "status": "validated-request",
        "request_sha256": ZERO_SHA256,
        "operation": OPERATION,
        "role": role,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "app_release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "approval_sha256": context.manifest["artifacts"]["cutover_approval_sha256"],
        "expected_host": proof_expected_host,
        "observed_host": proof_observed_host,
        "required_journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "business_write_policy": "forbid",
        "agent_artifact_sha256": context.manifest["artifacts"]["host_agent_sha256"],
        "host_agent_contract_sha256": context.manifest["artifacts"]["host_agent_contract_sha256"],
        # This is the controller's phase-validation transport vocabulary.  The
        # receipt's stricter payload-route vocabulary is retained in the
        # referenced receipt and validated when the closure is reopened.
        "transport": CONTROLLER.EXPECTED_TOPOLOGY[role]["transport"],
        "observed_at": proof_observed_at,
        "host_identity_observed": proof_observed_host == proof_expected_host,
        "execution_supported": False,
        "production_contacted": False,
        "worker_request": _reference_document(_reference(ingress.request)),
        "worker_attestation": _reference_document(_reference(ingress.attestation)),
        "transport_receipt": _reference_document(_reference(ingress.receipt)),
        "host_identity_proof_sha256": proof_sha256,
        "compose_execution": compose_execution,
        "provenance_closure_sha256": ZERO_SHA256,
    }
    closure = _role_provenance_closure_digest(document)
    document["provenance_closure_sha256"] = closure
    document["request_sha256"] = closure
    if set(document) != CONVERGENCE_ROLE_VALIDATION_FIELDS:
        raise ConvergenceSourceSetProducerError("normalized role validation fields differ")
    return document


def _write_new_or_same(path: Path, payload: bytes, *, label: str) -> str:
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=OUTPUT_FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
        outcome = "created"
    except SecureFileError as exc:
        try:
            existing = read_secure_bytes(path, label=f"existing {label}", owner_uid=0, max_size=MAX_JSON_BYTES)
        except SecureFileError as read_exc:
            raise ConvergenceSourceSetProducerError(f"{label} cannot be published safely") from read_exc
        if existing != payload:
            raise ConvergenceSourceSetProducerError(f"existing {label} differs and will not be replaced") from exc
        outcome = "reused"
    return outcome


def _publish_role_validation(
    context: BRIDGE.EvidenceContext,
    document: Mapping[str, Any],
) -> tuple[BRIDGE.Reference, str]:
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    path = BRIDGE._canonical_role_validation_path(  # noqa: SLF001
        context.manifest,
        role=str(document["role"]),
        digest=digest,
    )
    outcome = _write_new_or_same(path, payload, label=f"{document['role']} convergence role validation")
    observed = _read_record(BRIDGE.Reference(path, digest), label=f"published {document['role']} convergence role validation")
    if observed.document != document:
        raise ConvergenceSourceSetProducerError("published role validation read-back differs")
    return BRIDGE.Reference(path, digest), outcome


def _publish_observation(
    context: BRIDGE.EvidenceContext,
    *,
    label: str,
    document: Mapping[str, Any],
) -> tuple[BRIDGE.Reference, str]:
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    path = BRIDGE._canonical_observation_path(context.manifest, label=label, digest=digest)  # noqa: SLF001
    outcome = _write_new_or_same(path, payload, label=f"{label} convergence observation")
    observed = _read_record(BRIDGE.Reference(path, digest), label=f"published {label} convergence observation")
    if observed.document != document:
        raise ConvergenceSourceSetProducerError("published convergence observation read-back differs")
    return BRIDGE.Reference(path, digest), outcome


def _availability_document(
    context: BRIDGE.EvidenceContext,
    *,
    ingresses: Mapping[str, Ingress],
    pure_observations: Mapping[str, BRIDGE.SecureRecord],
    role_validations: Mapping[str, BRIDGE.Reference],
    observations: Mapping[str, BRIDGE.Reference],
    captured_at: datetime,
) -> dict[str, Any]:
    role_inputs = {
        role: {
            "request": _reference_document(_reference(ingresses[role].request)),
            "attestation": _reference_document(_reference(ingresses[role].attestation)),
            "transport_receipt": _reference_document(_reference(ingresses[role].receipt)),
        }
        for role in ROLES
    }
    document: dict[str, Any] = {
        "schema": AVAILABILITY_SCHEMA,
        "status": "unavailable",
        **_context_identity(context),
        "phase": PHASE,
        "operation": OPERATION,
        "phase_started_at": context.journal["started_at"],
        "captured_at": _timestamp_text(captured_at),
        "role_inputs": role_inputs,
        "pure_observation_inputs": {
            label: _reference_document(_reference(pure_observations[label]))
            for label in PURE_OBSERVATIONS
        },
        "role_validation": {role: _reference_document(role_validations[role]) for role in ROLES},
        "produced_observations": {
            label: _reference_document(observations[label]) for label in SUPPORTED_OBSERVATIONS
        },
        "unavailable_observations": {label: WORKER.UNAVAILABLE_REASONS[label] for label in MISSING_OBSERVATIONS},
        "ready_source_set_publication_blocker": (
            "local source-set integration does not publish a ready source set; "
            "a separately reviewed release-bound readiness publisher is required"
        ),
        "source_available": False,
        "bridge_ready_source_set_published": False,
        "direct_fi_to_ir_transfer_observed": False,
        "producer_network_io": False,
        "producer_docker_io": False,
        "producer_ssh_io": False,
        "availability_binding_sha256": ZERO_SHA256,
    }
    document["availability_binding_sha256"] = _availability_digest(document)
    if set(document) != AVAILABILITY_FIELDS:
        raise ConvergenceSourceSetProducerError("availability record fields differ")
    return document


def _publish_availability(
    context: BRIDGE.EvidenceContext,
    document: Mapping[str, Any],
) -> tuple[BRIDGE.Reference, str]:
    if document.get("availability_binding_sha256") != _availability_digest(document):
        raise ConvergenceSourceSetProducerError("availability binding differs")
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    path = _availability_path(context, digest)
    outcome = _write_new_or_same(path, payload, label="unavailable convergence source-set")
    observed = _read_record(BRIDGE.Reference(path, digest), label="published unavailable convergence source-set")
    if observed.document != document:
        raise ConvergenceSourceSetProducerError("availability read-back differs")
    return BRIDGE.Reference(path, digest), outcome


def _ready_role_validation_references(
    context: BRIDGE.EvidenceContext,
    *,
    digests: Mapping[str, str],
) -> dict[str, BRIDGE.Reference]:
    if set(digests) != set(ROLES):
        raise ConvergenceSourceSetProducerError(
            "ready source-set role validation digests must cover exactly four roles"
        )
    return {
        role: BRIDGE.Reference(
            BRIDGE._canonical_role_validation_path(  # noqa: SLF001
                context.manifest,
                role=role,
                digest=_nonzero_sha256(
                    digests[role],
                    label=f"ready source-set role validation {role}",
                ),
            ),
            _nonzero_sha256(
                digests[role],
                label=f"ready source-set role validation {role}",
            ),
        )
        for role in ROLES
    }


def _ready_observation_references(
    context: BRIDGE.EvidenceContext,
    *,
    digests: Mapping[str, str],
) -> dict[str, BRIDGE.Reference]:
    if set(digests) != set(BRIDGE.SOURCE_LABELS):
        raise ConvergenceSourceSetProducerError(
            "ready source-set observation digests must cover exactly seven observations"
        )
    return {
        label: BRIDGE.Reference(
            BRIDGE._canonical_observation_path(  # noqa: SLF001
                context.manifest,
                label=label,
                digest=_nonzero_sha256(
                    digests[label],
                    label=f"ready source-set observation {label}",
                ),
            ),
            _nonzero_sha256(
                digests[label],
                label=f"ready source-set observation {label}",
            ),
        )
        for label in BRIDGE.SOURCE_LABELS
    }


def _ready_source_set_document(
    context: BRIDGE.EvidenceContext,
    *,
    role_validation: Mapping[str, BRIDGE.Reference],
    observations: Mapping[str, BRIDGE.Reference],
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": BRIDGE.SOURCE_SET_SCHEMA,
        "status": "ready",
        **BRIDGE._source_identity_fields(context),  # noqa: SLF001
        "phase": PHASE,
        "phase_started_at": context.journal["started_at"],
        "role_validation": {
            role: BRIDGE._reference_document(role_validation[role])  # noqa: SLF001
            for role in ROLES
        },
        "observations": {
            label: BRIDGE._reference_document(observations[label])  # noqa: SLF001
            for label in BRIDGE.SOURCE_LABELS
        },
        "source_set_closure_sha256": BRIDGE._source_set_closure(  # noqa: SLF001
            phase_started_at=context.journal["started_at"],
            role_validation=role_validation,
            observations=observations,
        ),
    }
    if set(document) != BRIDGE.SOURCE_SET_FIELDS:
        raise ConvergenceSourceSetProducerError("ready source-set fields differ")
    return document


def prepare_ready_source_set(
    context: BRIDGE.EvidenceContext,
    *,
    role_validation_digests: Mapping[str, str],
    observation_digests: Mapping[str, str],
) -> PreparedReadySourceSet:
    """Prepare a ready source-set from already-published, immutable evidence only."""
    _validate_context(context)
    _require_controller_producer_exact_release(context)
    role_validation = _ready_role_validation_references(
        context,
        digests=role_validation_digests,
    )
    observations = _ready_observation_references(
        context,
        digests=observation_digests,
    )
    try:
        BRIDGE._validate_source_members(  # noqa: SLF001
            context,
            role_validation=role_validation,
            observations=observations,
            phase_started_at=_timestamp(
                context.journal["started_at"],
                label="ready source-set phase start",
            ),
            now=_utcnow(),
            require_fresh=True,
        )
    except (BRIDGE.ConvergenceGateError, BRIDGE.ConvergenceSourceUnavailable) as exc:
        raise ConvergenceSourceSetUnavailable(
            "published ready source-set members are unavailable or unsafe"
        ) from exc
    document = _ready_source_set_document(
        context,
        role_validation=role_validation,
        observations=observations,
    )
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    output = BRIDGE._canonical_source_set_path(context.manifest, digest)  # noqa: SLF001
    confirmation = (
        f"publish-{PHASE}-ready-source-set:"
        f"{context.manifest['operation_id']}:{context.manifest['release_sha']}:{digest}"
    )
    return PreparedReadySourceSet(
        context=context,
        role_validation=role_validation,
        observations=observations,
        document=document,
        payload=payload,
        sha256=digest,
        output=output,
        required_confirmation=confirmation,
    )


def publish_ready_source_set(
    prepared: PreparedReadySourceSet,
    *,
    confirm: str,
) -> dict[str, Any]:
    """Create a ready source-set only after an exact digest-bound confirmation."""
    if confirm != prepared.required_confirmation:
        raise ConvergenceSourceSetProducerError(
            "ready source-set publication requires exact digest-bound confirmation"
        )
    # A prepared object is only a plan. Reload every controller-owned input
    # immediately before the create-only publication so a phase advance,
    # changed approval, or replaced prior-evidence closure cannot leave a
    # stale immutable source-set behind.
    try:
        current_context = BRIDGE.load_evidence_context(
            manifest_path=prepared.context.manifest_path,
            approval_path=prepared.context.approval_path,
            approval_policy_path=prepared.context.approval_policy_path,
            prior_evidence_paths=prepared.context.prior_paths,
        )
    except BRIDGE.ConvergenceGateError as exc:
        raise ConvergenceSourceSetProducerError(
            "trusted convergence context changed or is unavailable before publication"
        ) from exc
    refreshed = prepare_ready_source_set(
        current_context,
        role_validation_digests={
            role: prepared.role_validation[role].sha256
            for role in ROLES
        },
        observation_digests={
            label: prepared.observations[label].sha256
            for label in BRIDGE.SOURCE_LABELS
        },
    )
    if (
        refreshed.payload != prepared.payload
        or refreshed.sha256 != prepared.sha256
        or refreshed.output != prepared.output
        or refreshed.required_confirmation != prepared.required_confirmation
    ):
        raise ConvergenceSourceSetProducerError(
            "ready source-set members changed before publication"
        )
    # Never publish the caller-provided object. The refresh above reopens the
    # canonical member closure and recomputes the confirmation from its exact
    # content; use that freshly validated value for every mutation boundary.
    if confirm != refreshed.required_confirmation:
        raise ConvergenceSourceSetProducerError(
            "ready source-set publication requires exact digest-bound confirmation"
        )
    _require_controller_producer_exact_release(refreshed.context)
    publication = _write_new_or_same(
        refreshed.output,
        refreshed.payload,
        label="ready convergence source-set",
    )
    reference = BRIDGE.Reference(refreshed.output, refreshed.sha256)
    try:
        BRIDGE._validate_source_set(  # noqa: SLF001
            refreshed.context,
            reference,
            now=_utcnow(),
            require_fresh=True,
        )
    except (BRIDGE.ConvergenceGateError, BRIDGE.ConvergenceSourceUnavailable) as exc:
        raise ConvergenceSourceSetProducerError(
            "published ready source-set does not satisfy convergence gate"
        ) from exc
    return {
        "schema": BRIDGE.PUBLICATION_SCHEMA,
        "status": "published",
        "kind": "ready-source-set",
        "phase": PHASE,
        "operation": OPERATION,
        "source_set_path": os.fspath(refreshed.output),
        "source_set_sha256": refreshed.sha256,
        "source_set_closure_sha256": refreshed.document["source_set_closure_sha256"],
        "publication": publication,
        "output_mutated": publication == "created",
        "journal_mutated": False,
        "production_contacted": False,
    }


def produce(
    context: BRIDGE.EvidenceContext,
    *,
    request_digests: Mapping[str, str],
    attestation_digests: Mapping[str, str],
    receipt_digests: Mapping[str, str],
    pure_observation_digests: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Attempt a full, provenance-verified convergence source-set assembly.

    This is a controller-local filesystem operation.  It never starts a
    journal phase, calls a host, or advances the convergence gate.  It is
    currently unavailable before any layout mutation because the controller
    producer itself lacks a manifest-bound exact-release execution contract.
    The independently authenticated Object Storage receiver requirement also
    remains unresolved behind that first blocker.

    The five collector-specific observations are supplied only as
    digest-addressed root-only files.  Their canonical input paths are derived
    locally, each result is read back and validated by its pure contract, and
    only then is it copied to the bridge's canonical create-only observation
    location.  This producer never creates a ``ready`` source-set.
    """

    _validate_context(context)
    if (
        set(request_digests) != set(ROLES)
        or set(attestation_digests) != set(ROLES)
        or set(receipt_digests) != set(ROLES)
    ):
        raise ConvergenceSourceSetProducerError("role ingress digest mappings must cover exactly four roles")
    _require_controller_producer_exact_release(context)
    if pure_observation_digests is None:
        raise ConvergenceSourceSetProducerError(
            "five canonical pure observation records are required before source-set assembly"
        )
    current = _utcnow()
    _ensure_layout(context)
    pure_observations = _load_pure_observations(
        context,
        digests=pure_observation_digests,
        now=current,
    )
    ingresses = {
        role: _validate_ingress(
            context,
            role=role,
            request_digest=_nonzero_sha256(request_digests[role], label=f"{role} request file"),
            attestation_digest=_nonzero_sha256(attestation_digests[role], label=f"{role} attestation file"),
            receipt_digest=_nonzero_sha256(receipt_digests[role], label=f"{role} receipt file"),
            now=current,
        )
        for role in ROLES
    }
    captured_at = _validate_capture_times(ingresses, context=context, now=current)
    database = _database_observation(context, ingresses, observed_at=captured_at)
    dr = _dr_observation(context, ingresses, observed_at=captured_at)
    try:
        BRIDGE._validate_database_observation(database, context=context)  # noqa: SLF001
        BRIDGE._validate_dr_observation(dr, context=context)  # noqa: SLF001
    except BRIDGE.ConvergenceGateError as exc:
        raise ConvergenceSourceSetProducerError("derived production observation does not satisfy bridge schema") from exc
    observations: dict[str, BRIDGE.Reference] = {}
    observation_publications: dict[str, str] = {}
    for label, document in (("database_parity", database), ("dr_convergence", dr)):
        _assert_publication_freshness(ingresses, context=context)
        observations[label], observation_publications[label] = _publish_observation(
            context,
            label=label,
            document=document,
        )
    for label in PURE_OBSERVATIONS:
        _assert_publication_freshness(ingresses, context=context)
        observations[label], observation_publications[label] = _publish_observation(
            context,
            label=label,
            document=pure_observations[label].document,
        )
    role_validations: dict[str, BRIDGE.Reference] = {}
    role_publications: dict[str, str] = {}
    for role in ROLES:
        _assert_publication_freshness(ingresses, context=context)
        role_validations[role], role_publications[role] = _publish_role_validation(
            context,
            _role_validation_document(context, ingress=ingresses[role]),
        )
    try:
        _requests, source_hashes, observed = VERIFY._read_role_validation_records(  # noqa: SLF001
            [f"{role}={role_validations[role].path}" for role in ROLES],
            phase=PHASE,
            manifest=context.manifest,
            manifest_sha256=context.manifest_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceSourceSetProducerError("published role validation closure is invalid") from exc
    if source_hashes != {role: role_validations[role].sha256 for role in ROLES}:
        raise ConvergenceSourceSetProducerError("published role validation source digests differ")
    if set(observed) != set(ROLES):
        raise ConvergenceSourceSetProducerError("published role validation times differ")
    availability = _availability_document(
        context,
        ingresses=ingresses,
        pure_observations=pure_observations,
        role_validations=role_validations,
        observations=observations,
        captured_at=captured_at,
    )
    _assert_publication_freshness(ingresses, context=context)
    availability_ref, availability_publication = _publish_availability(context, availability)
    return {
        "schema": AVAILABILITY_SCHEMA,
        "status": "unavailable",
        "source_available": False,
        "ready_source_set": None,
        "availability": _reference_document(availability_ref),
        "pure_observation_inputs": {
            label: _reference_document(_reference(pure_observations[label]))
            for label in PURE_OBSERVATIONS
        },
        "role_validation": {role: _reference_document(role_validations[role]) for role in ROLES},
        "observations": {label: _reference_document(observations[label]) for label in SUPPORTED_OBSERVATIONS},
        "unavailable_observations": dict(availability["unavailable_observations"]),
        "captured_at": _timestamp_text(captured_at),
        "publication": {
            "role_validation": role_publications,
            "observations": observation_publications,
            "availability": availability_publication,
        },
        "producer_network_io": False,
        "producer_docker_io": False,
        "producer_ssh_io": False,
        "direct_fi_to_ir_transfer": False,
    }


def build_plan(context: BRIDGE.EvidenceContext | None = None) -> dict[str, Any]:
    """Return the current truthful capability state without contacting a role."""

    if context is None:
        identity = {
            "campaign_id": None,
            "operation_id": None,
            "release_sha": None,
            "manifest_sha256": None,
            "controller_plan_sha256": None,
        }
    else:
        _validate_context(context)
        identity = {
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "controller_plan_sha256": context.plan_sha256,
        }
    body = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        **identity,
        "default_action": "plan",
        "source_available": False,
        "bridge_ready_source_set_published": False,
        "supported_observations": list(SUPPORTED_OBSERVATIONS),
        "unavailable_observations": {label: WORKER.UNAVAILABLE_REASONS[label] for label in MISSING_OBSERVATIONS},
        "required_transport": dict(TRANSPORT_BY_ROLE),
        "object_storage_private_versioned_age_roles": sorted(OBJECT_STORAGE_ROLES),
        "controller_producer_exact_release_available": False,
        "controller_producer_exact_release_requirement": (
            CONTROLLER_PRODUCER_EXACT_RELEASE_REQUIREMENT
        ),
        "controller_producer_exact_release_blocker": (
            "no manifest-bound detached controller producer release path and self-hash "
            "are installed; producer cannot publish a ready source set"
        ),
        "remote_receiver_authentication_available": False,
        "remote_receiver_authentication_blocker": (
            "no release-bound pinned remote receiver signing policy is installed; "
            "controller-authored Object Storage receipts are not observation proof"
        ),
        "fi_to_ir_direct_transfer_forbidden": True,
        "redacted_nonsecret_ssh_payload_only": True,
        "producer_network_io": False,
        "producer_docker_io": False,
        "producer_ssh_io": False,
        "producer_production_mutation": False,
    }
    return {**body, "plan_sha256": _sha256(body)}


def _parse_digest_mapping(values: Sequence[str], *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, digest = value.partition("=")
        if not separator or role in result or role not in ROLES:
            raise ConvergenceSourceSetProducerError(f"{label} mapping is invalid")
        result[role] = _nonzero_sha256(digest, label=f"{label} {role}")
    if set(result) != set(ROLES):
        raise ConvergenceSourceSetProducerError(f"{label} mapping must cover exactly four roles")
    return result


def _parse_pure_observation_mapping(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        observation, separator, digest = value.partition("=")
        if (
            not separator
            or observation in result
            or observation not in PURE_OBSERVATIONS
        ):
            raise ConvergenceSourceSetProducerError("pure observation mapping is invalid")
        result[observation] = _nonzero_sha256(
            digest, label=f"pure observation {observation}"
        )
    if set(result) != set(PURE_OBSERVATIONS):
        raise ConvergenceSourceSetUnavailable(
            "pure observation collectors or proofs are incomplete"
        )
    return result


def _parse_ready_role_validation_mapping(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, digest = value.partition("=")
        if not separator or role in result or role not in ROLES:
            raise ConvergenceSourceSetProducerError(
                "ready source-set role validation mapping is invalid"
            )
        result[role] = _nonzero_sha256(
            digest,
            label=f"ready source-set role validation {role}",
        )
    if set(result) != set(ROLES):
        raise ConvergenceSourceSetProducerError(
            "ready source-set role validation mapping must cover exactly four roles"
        )
    return result


def _parse_ready_observation_mapping(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        label, separator, digest = value.partition("=")
        if not separator or label in result or label not in BRIDGE.SOURCE_LABELS:
            raise ConvergenceSourceSetProducerError(
                "ready source-set observation mapping is invalid"
            )
        result[label] = _nonzero_sha256(
            digest,
            label=f"ready source-set observation {label}",
        )
    if set(result) != set(BRIDGE.SOURCE_LABELS):
        raise ConvergenceSourceSetProducerError(
            "ready source-set observation mapping must cover exactly seven observations"
        )
    return result


def _prepared_ready_source_set_result(
    prepared: PreparedReadySourceSet,
) -> dict[str, Any]:
    return {
        "schema": READY_SOURCE_SET_PLAN_SCHEMA,
        "status": "prepared",
        "phase": PHASE,
        "operation": OPERATION,
        "source_set": _reference_document(
            BRIDGE.Reference(prepared.output, prepared.sha256)
        ),
        "source_set_closure_sha256": prepared.document["source_set_closure_sha256"],
        "required_confirmation": prepared.required_confirmation,
        "output_mutated": False,
        "journal_mutated": False,
        "production_contacted": False,
    }


def _parse_prior_mapping(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        phase, separator, raw_path = value.partition("=")
        if not separator or phase in result or phase not in BRIDGE.PRIOR_PHASES:
            raise ConvergenceSourceSetProducerError("prior evidence mapping is invalid")
        path = Path(raw_path)
        if not path.is_absolute():
            raise ConvergenceSourceSetProducerError("prior evidence path is not absolute")
        result[phase] = path
    if set(result) != set(BRIDGE.PRIOR_PHASES):
        raise ConvergenceSourceSetProducerError("prior evidence mapping is incomplete")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("plan", "produce", "ready-source-set"),
        nargs="?",
        default="plan",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approval-policy", type=Path)
    parser.add_argument("--prior-evidence", action="append", default=[])
    parser.add_argument("--role-request", action="append", default=[])
    parser.add_argument("--role-attestation", action="append", default=[])
    parser.add_argument("--role-receipt", action="append", default=[])
    parser.add_argument("--pure-observation", action="append", default=[])
    parser.add_argument("--role-validation", action="append", default=[])
    parser.add_argument("--observation", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        supplied = (args.manifest, args.approval, args.approval_policy)
        if any(value is not None for value in supplied) and not all(value is not None for value in supplied):
            raise ConvergenceSourceSetProducerError("manifest, approval, and approval policy must be supplied together")
        context = None
        if all(value is not None for value in supplied):
            context = BRIDGE.load_evidence_context(
                manifest_path=args.manifest,
                approval_path=args.approval,
                approval_policy_path=args.approval_policy,
                prior_evidence_paths=_parse_prior_mapping(args.prior_evidence),
            )
        if args.action == "plan":
            if (
                args.role_validation
                or args.observation
                or args.apply
                or args.confirm is not None
            ):
                raise ConvergenceSourceSetProducerError(
                    "plan does not accept ready source-set publication arguments"
                )
            print(_canonical_json(build_plan(context)).decode("ascii"))
            return 0
        if context is None:
            raise ConvergenceSourceSetProducerError(
                f"{args.action} requires controller context inputs"
            )
        if args.action == "produce":
            if (
                args.role_validation
                or args.observation
                or args.apply
                or args.confirm is not None
            ):
                raise ConvergenceSourceSetProducerError(
                    "produce does not accept ready source-set publication arguments"
                )
            result = produce(
                context,
                request_digests=_parse_digest_mapping(args.role_request, label="role request"),
                attestation_digests=_parse_digest_mapping(args.role_attestation, label="role attestation"),
                receipt_digests=_parse_digest_mapping(args.role_receipt, label="role receipt"),
                pure_observation_digests=_parse_pure_observation_mapping(
                    args.pure_observation
                ),
            )
        else:
            if (
                args.role_request
                or args.role_attestation
                or args.role_receipt
                or args.pure_observation
            ):
                raise ConvergenceSourceSetProducerError(
                    "ready-source-set accepts only published member digest mappings"
                )
            if args.confirm is not None and not args.apply:
                raise ConvergenceSourceSetProducerError(
                    "ready-source-set confirmation requires --apply"
                )
            if args.apply and not args.confirm:
                raise ConvergenceSourceSetProducerError(
                    "ready-source-set --apply requires --confirm"
                )
            prepared = prepare_ready_source_set(
                context,
                role_validation_digests=_parse_ready_role_validation_mapping(
                    args.role_validation
                ),
                observation_digests=_parse_ready_observation_mapping(args.observation),
            )
            result = (
                publish_ready_source_set(prepared, confirm=args.confirm)
                if args.apply
                else _prepared_ready_source_set_result(prepared)
            )
        print(_canonical_json(result).decode("ascii"))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
